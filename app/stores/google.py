"""Google Play Android Publisher adapter.

Flow:
  upload  = edits.insert -> bundles.upload -> tracks.update -> edits.commit
  submit  = promote existing versionCode to a track (发布/送审)
  status  = list tracks and map release status

Safety: production requires allow_production=True.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from app.config import get_settings
from app.models import (
    OperationResult,
    Platform,
    ReviewState,
    ReviewStatus,
    SubmitRequest,
    UploadRequest,
)
from app.stores.base import StoreClient

SAFE_TRACKS = frozenset({"internal", "alpha", "beta"})
PROD_TRACKS = frozenset({"production", "prod"})

# Google Play API 的 Release.status 枚举是**驼峰**的 `inProgress`，
# 小写 `inprogress` 会被 400 拒绝。内部统一用小写，出网前再映射回去。
_API_RELEASE_STATUS = {
    "completed": "completed",
    "draft": "draft",
    "halted": "halted",
    "inprogress": "inProgress",
}


def _to_api_status(status: str) -> str:
    """内部小写状态 -> Google API 要求的枚举字面量。"""
    return _API_RELEASE_STATUS.get((status or "").strip().lower(), "completed")


def _normalize_track(track: str) -> str:
    t = track.strip().lower()
    return "production" if t == "prod" else t


def _map_release_status(status: str | None, track: str) -> ReviewState:
    s = (status or "").lower()
    if s == "draft":
        return ReviewState.DRAFT
    if s == "halted":
        return ReviewState.HALTED
    if s == "inprogress":
        # production 上 inProgress 常表示审核/分阶段发布中
        return ReviewState.IN_REVIEW if track == "production" else ReviewState.WAITING_FOR_REVIEW
    if s == "completed":
        return ReviewState.RELEASED
    return ReviewState.UNKNOWN


class _RequestsHttpAdapter:
    """httplib2-compatible adapter so googleapiclient can use requests (proxy-aware)."""

    def __init__(
        self,
        session: Any,
        timeout: int = 120,
        *,
        max_attempts: int = 5,
    ) -> None:
        self.session = session
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)

    def request(self, uri, method="GET", body=None, headers=None, **_kwargs):
        import time

        from app.stores.google_errors import is_retryable_transport_error

        last_exc: BaseException | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=uri,
                    data=body,
                    headers=headers or {},
                    timeout=self.timeout,
                )

                class _Resp(dict):
                    def __init__(self, resp):
                        super().__init__({k.lower(): v for k, v in resp.headers.items()})
                        self.status = resp.status_code
                        self.reason = resp.reason

                # Retry transient HTTP statuses on non-upload idempotent-ish GETs;
                # for PUT/POST resumable chunks, also retry 502/503/429.
                if response.status_code in {429, 502, 503} and attempt < self.max_attempts:
                    logger.warning(
                        "Google API HTTP {} on attempt {}/{}, retrying…",
                        response.status_code,
                        attempt,
                        self.max_attempts,
                    )
                    time.sleep(min(2 ** attempt, 20))
                    continue

                return _Resp(response), response.content
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt >= self.max_attempts or not is_retryable_transport_error(exc):
                    raise
                logger.warning(
                    "Google API transport error on attempt {}/{}: {} — retrying…",
                    attempt,
                    self.max_attempts,
                    exc,
                )
                time.sleep(min(2 ** attempt, 20))
        assert last_exc is not None
        raise last_exc


class GoogleStoreClient(StoreClient):
    platform = Platform.ANDROID

    def _sa_path(self, app_cfg: dict) -> Path:
        android = app_cfg.get("android") or {}
        raw = android.get("service_account_json") or get_settings().google_play_service_account_json
        path = Path(raw)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[2] / path
        return path

    def _resolve_track(
        self,
        req_track: str | None,
        app_cfg: dict,
        *,
        allow_production: bool = False,
    ) -> str:
        android = app_cfg.get("android") or {}
        track = _normalize_track(req_track or android.get("track") or "internal")
        if track in PROD_TRACKS:
            if not allow_production:
                raise PermissionError(
                    "已禁止操作正式轨道 production。若确认要对用户发版，请加 --allow-production"
                )
            return "production"
        if track not in SAFE_TRACKS:
            raise PermissionError(
                f"不支持的轨道 `{track}`。允许: internal/alpha/beta，或显式 production"
            )
        return track

    def resolve_release_notes(
        self,
        req,
        app_cfg: dict,
        *,
        track: str,
    ) -> tuple[list[dict[str, str]] | None, str | None, str | None]:
        """解析并校验版本说明。

        返回 (notes, 错误文案, 来源)。来源为 explicit / default / None，
        用于在结果里告诉用户「这次用的是默认文案」，避免静默。
        """
        from app.stores.release_notes import (
            NotesSpecError,
            build_release_notes,
            format_issues,
            has_errors,
            validate_notes,
        )

        raw: list[str] = []
        for locale, text in (getattr(req, "release_notes", None) or {}).items():
            raw.append(f"{locale}={text}")
        if getattr(req, "whats_new", None):
            raw.append(req.whats_new)

        try:
            notes, source = build_release_notes(raw, app_cfg)
        except NotesSpecError as exc:
            return None, f"版本说明格式有误：{exc}", None

        issues = validate_notes(
            notes, track=track, is_production=track in PROD_TRACKS
        )
        if has_errors(issues):
            return None, f"版本说明校验未通过：\n{format_issues(issues)}", None
        return notes, None, source

    def _publisher(self, app_cfg: dict | None = None, timeout: int = 120):
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        from app.config import apply_proxy_env, get_google_proxy_url

        apply_proxy_env()
        sa_path = self._sa_path(app_cfg or {})
        if not sa_path.exists():
            raise RuntimeError(f"Google Play 服务账号文件不存在: {sa_path}")
        creds = service_account.Credentials.from_service_account_file(
            str(sa_path),
            scopes=["https://www.googleapis.com/auth/androidpublisher"],
        )

        proxy_url = get_google_proxy_url()
        session = AuthorizedSession(creds)
        if proxy_url:
            session.proxies.update({"http": proxy_url, "https": proxy_url})
            logger.info("Google API via requests proxy {}", proxy_url)
        else:
            logger.warning("未配置 HTTP(S)_PROXY，Google API 可能在国内超时")

        http = _RequestsHttpAdapter(session, timeout=timeout, max_attempts=5)
        return build(
            "androidpublisher",
            "v3",
            http=http,
            cache_discovery=False,
        )

    def _assign_track(
        self,
        service,
        *,
        package_name: str,
        edit_id: str,
        track: str,
        version_code: str | int,
        release_notes: list[dict[str, str]] | None = None,
        release_status: str = "completed",
        user_fraction: float | None = None,
    ) -> dict:
        status = (release_status or "completed").strip().lower()
        if status not in {"completed", "draft", "halted", "inprogress"}:
            status = "completed"
        release: dict = {
            "versionCodes": [str(version_code)],
            "status": _to_api_status(status),
        }
        # 分阶段发布：Google 只在 status inProgress/halted 时接受 userFraction，
        # 且必须严格落在 (0, 1)。全面发布（completed）绝不能带这个字段。
        if user_fraction is not None and status in {"inprogress", "halted"}:
            release["userFraction"] = float(user_fraction)
        # releaseNotes 在 API 中是可选的。省略 = 商店不展示版本说明；
        # 绝不能替用户编一句，否则正式版会把工具文案展示给真实用户。
        if release_notes:
            release["releaseNotes"] = release_notes
        track_body = {"track": track, "releases": [release]}
        return (
            service.edits()
            .tracks()
            .update(
                packageName=package_name,
                editId=edit_id,
                track=track,
                body=track_body,
            )
            .execute()
        )

    def upload(self, req: UploadRequest, app_cfg: dict) -> OperationResult:
        from googleapiclient.http import MediaFileUpload

        from app.stores.aab_meta import parse_aab_meta
        from app.stores.google_errors import (
            collect_track_version_codes,
            format_google_upload_error,
        )

        android = app_cfg.get("android") or {}
        package_name = android.get("package_name")
        artifact = req.artifact_path or android.get("artifact_path")
        artifact_url = req.artifact_url or android.get("artifact_url")

        try:
            track = self._resolve_track(
                req.track, app_cfg, allow_production=req.allow_production
            )
        except PermissionError as exc:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message=str(exc),
            )

        # 分阶段发布同样要在「上传大文件之前」校验完
        from app.core.rollout import (
            describe_rollout,
            is_staged,
            release_status_for,
            validate_rollout_track,
        )

        rollout_fraction = getattr(req, "rollout_fraction", None)
        rollout_error = validate_rollout_track(rollout_fraction, track)
        if rollout_error:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message=rollout_error,
                details={"track": track, "rollout_fraction": rollout_fraction},
            )
        staged = is_staged(rollout_fraction)
        upload_status = release_status_for(rollout_fraction)

        # 版本说明必须在「上传大文件之前」校验完，避免传完 200MB 才失败
        release_notes, notes_error, notes_source = self.resolve_release_notes(
            req, app_cfg, track=track
        )
        if notes_error:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message=notes_error,
                details={"track": track, "artifact": str(artifact or artifact_url or "")},
            )

        logger.info(
            "google.upload app={} package={} track={} artifact={}",
            req.app_id,
            package_name,
            track,
            artifact or artifact_url,
        )
        if not package_name:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message="apps.yaml 缺少 android.package_name",
            )
        if artifact_url and not artifact:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message="暂不支持仅 artifact_url；请提供本地 AAB 路径",
            )
        if not artifact:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message="未提供 AAB 路径",
            )

        aab_path = Path(artifact)
        if not aab_path.exists():
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message=f"AAB 文件不存在: {aab_path}",
            )
        if aab_path.suffix.lower() not in {".aab", ".apk"}:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message=f"文件类型不支持（请用 .aab）: {aab_path.suffix}",
            )

        # --- 上传前校验（避免错包 / 已知重复码白传大文件）---
        aab_meta = None
        if aab_path.suffix.lower() == ".aab":
            try:
                aab_meta = parse_aab_meta(aab_path)
            except Exception as exc:  # noqa: BLE001
                return OperationResult(
                    ok=False,
                    app_id=req.app_id,
                    platform=Platform.ANDROID,
                    message=f"上传前校验失败：无法解析 AAB 元数据。{exc}",
                    details={"artifact": str(aab_path)},
                )
            if aab_meta.package_name != package_name:
                return OperationResult(
                    ok=False,
                    app_id=req.app_id,
                    platform=Platform.ANDROID,
                    message=(
                        f"上传前校验失败：AAB 包名 `{aab_meta.package_name}` "
                        f"与 app `{req.app_id}` 配置的 `{package_name}` 不一致。"
                        f"请检查 --app-id 是否选错。"
                    ),
                    details={
                        "artifact": str(aab_path),
                        "aab_package": aab_meta.package_name,
                        "configured_package": package_name,
                        "aab_version_code": aab_meta.version_code,
                        "aab_version_name": aab_meta.version_name,
                    },
                )

        edit_id = None
        service = None
        try:
            service = self._publisher(app_cfg, timeout=600)
            edit = service.edits().insert(packageName=package_name, body={}).execute(
                num_retries=3
            )
            edit_id = edit["id"]
            logger.info("created edit_id={}", edit_id)

            if aab_meta is not None:
                tracks = (
                    service.edits()
                    .tracks()
                    .list(packageName=package_name, editId=edit_id)
                    .execute(num_retries=3)
                )
                used = collect_track_version_codes(tracks)
                if aab_meta.version_code in used:
                    service.edits().delete(
                        packageName=package_name, editId=edit_id
                    ).execute(num_retries=2)
                    edit_id = None
                    return OperationResult(
                        ok=False,
                        app_id=req.app_id,
                        platform=Platform.ANDROID,
                        message=(
                            f"上传前校验失败：versionCode {aab_meta.version_code}"
                            f"（{aab_meta.version_name or '-'}）已出现在当前轨道中。"
                            f"请换更高 versionCode 的新 AAB；若只需推进已有版本，用 "
                            f"`release --version-code {aab_meta.version_code}`。"
                        ),
                        details={
                            "package_name": package_name,
                            "track": track,
                            "artifact": str(aab_path),
                            "aab_version_code": aab_meta.version_code,
                            "aab_version_name": aab_meta.version_name,
                            "track_version_codes_sample": sorted(
                                used, key=lambda x: int(x) if x.isdigit() else 0
                            )[-12:],
                        },
                    )

            media = MediaFileUpload(
                str(aab_path),
                mimetype="application/octet-stream",
                resumable=True,
                chunksize=8 * 1024 * 1024,
            )
            upload_retries = 5
            if aab_path.suffix.lower() == ".aab":
                bundle = (
                    service.edits()
                    .bundles()
                    .upload(
                        packageName=package_name,
                        editId=edit_id,
                        media_body=media,
                    )
                    .execute(num_retries=upload_retries)
                )
                version_code = bundle.get("versionCode")
                upload_kind = "aab"
            else:
                apk = (
                    service.edits()
                    .apks()
                    .upload(
                        packageName=package_name,
                        editId=edit_id,
                        media_body=media,
                    )
                    .execute(num_retries=upload_retries)
                )
                version_code = apk.get("versionCode")
                upload_kind = "apk"

            if version_code is None:
                raise RuntimeError("上传成功但未返回 versionCode")

            track_resp = self._assign_track(
                service,
                package_name=package_name,
                edit_id=edit_id,
                track=track,
                version_code=version_code,
                release_notes=release_notes,
                release_status=upload_status,
                user_fraction=rollout_fraction if staged else None,
            )
            commit = service.edits().commit(
                packageName=package_name,
                editId=edit_id,
            ).execute(num_retries=3)
            edit_id = None

            review_state = (
                ReviewState.WAITING_FOR_REVIEW
                if track == "production"
                else ReviewState.RELEASED
            )
            where = {
                "internal": "Play Console → 测试 → 内部测试",
                "alpha": "Play Console → 测试 → 封闭式测试(alpha)",
                "beta": "Play Console → 测试 → 开放式测试(beta)",
                "production": "Play Console → 正式版（可能进入审核）",
            }.get(track, track)

            notes_note = ""
            if notes_source == "default":
                from app.stores.release_notes import resolve_default_text

                notes_note = (
                    f"（版本说明使用默认文案：{resolve_default_text(app_cfg)}）"
                )
            elif notes_source is None:
                notes_note = "（未提供版本说明，商店不会展示「新版本亮点」）"

            if staged:
                rollout_note = (
                    f"已按 {describe_rollout(rollout_fraction)} 放量（{where}）。"
                    f"确认稳定后可续推，例如：\n"
                    f"  python cli.py release --app-id {req.app_id} "
                    f"--platform android --version-code {version_code} "
                    f"--track production --rollout 50 --allow-production\n"
                    f"全量则用 --rollout 100。"
                )
            else:
                rollout_note = f"请到 {where} 查看。"

            return OperationResult(
                ok=True,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                review_state=review_state,
                message=(
                    f"已上传并发布到 `{track}`，versionCode={version_code}。"
                    f"{notes_note}"
                    f"{rollout_note}"
                ),
                details={
                    "package_name": package_name,
                    "track": track,
                    "version_code": version_code,
                    "upload_kind": upload_kind,
                    "artifact": str(aab_path),
                    "release_notes": release_notes,
                    "release_notes_source": notes_source,
                    "rollout": describe_rollout(rollout_fraction),
                    "rollout_fraction": rollout_fraction,
                    "user_fraction": rollout_fraction if staged else None,
                    "track_response": track_resp,
                    "commit": commit,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("google.upload error")
            if service is not None and edit_id is not None:
                try:
                    service.edits().delete(packageName=package_name, editId=edit_id).execute(
                        num_retries=2
                    )
                    logger.info("discarded failed edit_id={}", edit_id)
                except Exception:  # noqa: BLE001
                    logger.warning("failed to discard edit_id={}", edit_id)
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message=format_google_upload_error(exc),
                details={
                    "package_name": package_name,
                    "track": track,
                    "artifact": str(aab_path),
                    "aab_version_code": aab_meta.version_code if aab_meta else None,
                    "aab_version_name": aab_meta.version_name if aab_meta else None,
                    "error_type": type(exc).__name__,
                },
            )

    def submit(self, req: SubmitRequest, app_cfg: dict) -> OperationResult:
        """Promote an existing versionCode to a track (发布/送审)."""
        android = app_cfg.get("android") or {}
        package_name = android.get("package_name")
        version_code = req.version_code or req.build_id
        if not package_name:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message="apps.yaml 缺少 android.package_name",
            )
        if not version_code:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message="submit/release 需要 --version-code（已上传包的 versionCode）",
            )

        try:
            track = self._resolve_track(
                req.track, app_cfg, allow_production=req.allow_production
            )
        except PermissionError as exc:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message=str(exc),
            )

        # 分阶段发布校验（在建 edit 之前）
        from app.core.rollout import (
            compare_rollout,
            describe_rollout,
            is_staged,
            release_status_for,
            validate_rollout_track,
        )

        rollout_specified = getattr(req, "rollout_fraction", None) is not None
        rollout_fraction = getattr(req, "rollout_fraction", None)
        rollout_error = validate_rollout_track(rollout_fraction, track)
        if rollout_error:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message=rollout_error,
                details={"track": track, "rollout_fraction": rollout_fraction},
            )
        staged = is_staged(rollout_fraction)
        # 分了批就由 rollout 决定 status；没传 rollout 时沿用 release_status
        if staged:
            desired_status = "inprogress"
        elif rollout_specified and rollout_fraction is not None and rollout_fraction >= 1:
            desired_status = "completed"
        else:
            desired_status = (req.release_status or "completed").strip().lower()

        # 版本说明校验放在建 edit / 写入之前，避免正式版发不出去或写错文案
        release_notes, notes_error, notes_source = self.resolve_release_notes(
            req, app_cfg, track=track
        )
        if notes_error:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message=notes_error,
                details={"track": track, "version_code": str(version_code)},
            )

        edit_id = None
        service = None
        try:
            from app.stores.google_errors import primary_release_on_track

            service = self._publisher(app_cfg, timeout=120)
            edit = service.edits().insert(packageName=package_name, body={}).execute(
                num_retries=3
            )
            edit_id = edit["id"]

            # 防呆：目标轨道已是该 versionCode 且状态已达标时，不再 commit（避免假「送审成功」）
            tracks = (
                service.edits()
                .tracks()
                .list(packageName=package_name, editId=edit_id)
                .execute(num_retries=3)
            )
            primary = primary_release_on_track(tracks, track)
            if primary:
                codes = [str(c) for c in (primary.get("versionCodes") or [])]
                current_status = (primary.get("status") or "").lower()
                current_fraction = primary.get("userFraction")
                same_version = str(version_code) in codes

                # 判定是否属于「无效 / 不被 Play 允许的推进」：
                #   1. 已是全面发布(completed)，想退回分批(inProgress)
                #      -> Play 只允许百分比递增，100% 是终态，直接拦下
                #   2. 同在分批中，但放量没变大（相等或缩量）-> 无意义
                #   3. 状态完全相同（如都已 completed）-> 无意义
                # 其余（分批->全量、halted->重新放量等）都放行。
                skip_reason = None
                skip_hint = None
                if same_version:
                    if current_status == "completed" and desired_status == "inprogress":
                        skip_reason = "already_full_no_downgrade"
                        skip_hint = (
                            "该版本已是全面发布（100%），Google 不允许倒退回分批发布，"
                            "已放量的用户也无法回收。若确实需要分批，"
                            "请上传更高 versionCode，并在**提审时就带 --rollout**。"
                        )
                    elif desired_status == current_status == "inprogress":
                        if compare_rollout(current_fraction, rollout_fraction) <= 0:
                            skip_reason = "fraction_not_increased"
                            skip_hint = (
                                f"当前已放量 {describe_rollout(None if current_fraction is None else float(current_fraction))}，"
                                f"请传更大的 --rollout（例如当前 10%，续推用 --rollout 20；全量用 --rollout 100）。"
                            )
                    elif current_status == desired_status:
                        skip_reason = "already_on_track"
                        skip_hint = "若需真正送审，请上传更高 versionCode 的新 AAB。"

                if skip_reason:
                    service.edits().delete(
                        packageName=package_name, editId=edit_id
                    ).execute(num_retries=2)
                    edit_id = None
                    cur_desc = describe_rollout(
                        None if current_fraction is None else float(current_fraction)
                    )
                    return OperationResult(
                        ok=False,
                        app_id=req.app_id,
                        platform=Platform.ANDROID,
                        message=(
                            f"release 已跳过（防呆）：versionCode={version_code} "
                            f"已在轨道 `{track}` 上（status={current_status}，{cur_desc}）。"
                            f"该操作不会产生新的 Google 审核或「提交活动」记录。"
                            f"{skip_hint}"
                        ),
                        details={
                            "skipped": True,
                            "reason": skip_reason,
                            "package_name": package_name,
                            "track": track,
                            "version_code": str(version_code),
                            "current_status": current_status,
                            "current_user_fraction": current_fraction,
                            "desired_status": desired_status,
                            "desired_user_fraction": rollout_fraction,
                            "primary_release": primary,
                        },
                    )

            track_resp = self._assign_track(
                service,
                package_name=package_name,
                edit_id=edit_id,
                track=track,
                version_code=version_code,
                release_notes=release_notes,
                release_status=desired_status,
                user_fraction=rollout_fraction if staged else None,
            )
            commit = service.edits().commit(
                packageName=package_name,
                editId=edit_id,
            ).execute(num_retries=3)
            edit_id = None

            review_state = _map_release_status(desired_status, track)
            if track == "production" and desired_status == "completed":
                review_state = ReviewState.WAITING_FOR_REVIEW

            notes_note = ""
            if notes_source == "default":
                from app.stores.release_notes import resolve_default_text

                notes_note = f"版本说明使用默认文案：{resolve_default_text(app_cfg)}。"

            if staged:
                tail = (
                    f"已按 {describe_rollout(rollout_fraction)} 放量。"
                    f"确认稳定后续推：`--rollout 50`；全量：`--rollout 100`。"
                )
            elif track == "production":
                tail = "正式版可能进入 Google 审核，请用 status/watch 跟踪。"
            else:
                tail = "测试轨道一般无需商店人工审核。"

            return OperationResult(
                ok=True,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                review_state=review_state,
                message=(
                    f"已将 versionCode={version_code} 发布到 `{track}` "
                    f"(status={desired_status})。"
                    f"{notes_note}"
                    f"{tail}"
                ),
                details={
                    "package_name": package_name,
                    "track": track,
                    "version_code": str(version_code),
                    "release_status": desired_status,
                    "rollout": describe_rollout(rollout_fraction),
                    "rollout_fraction": rollout_fraction,
                    "user_fraction": rollout_fraction if staged else None,
                    "release_notes": release_notes,
                    "release_notes_source": notes_source,
                    "track_response": track_resp,
                    "commit": commit,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("google.submit/release error")
            if service is not None and edit_id is not None:
                try:
                    service.edits().delete(packageName=package_name, editId=edit_id).execute(
                        num_retries=2
                    )
                except Exception:  # noqa: BLE001
                    pass
            from app.stores.google_errors import format_google_upload_error

            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message=f"发布/提审失败: {format_google_upload_error(exc)}",
                details={
                    "package_name": package_name,
                    "track": track,
                    "version_code": str(version_code),
                },
            )

    def status(
        self,
        app_cfg: dict,
        version_name: str | None = None,
        version_code: str | None = None,
        prefer_track: str | None = None,
    ) -> ReviewStatus:
        from app.core.watch_targets import CONSOLE_HINT

        android = app_cfg.get("android") or {}
        package_name = android.get("package_name")
        prefer = _normalize_track(
            prefer_track or android.get("track") or "internal"
        )
        if not package_name:
            return ReviewStatus(
                app_id=app_cfg.get("id", ""),
                platform=Platform.ANDROID,
                version_name=version_name,
                state=ReviewState.UNKNOWN,
                message="缺少 android.package_name",
            )

        try:
            sa = self._sa_path(app_cfg)
            if not sa.exists():
                return ReviewStatus(
                    app_id=app_cfg.get("id", ""),
                    platform=Platform.ANDROID,
                    version_name=version_name,
                    state=ReviewState.UNKNOWN,
                    message=f"未找到服务账号文件: {sa}",
                )

            service = self._publisher(app_cfg)
            edit = service.edits().insert(packageName=package_name, body={}).execute()
            edit_id = edit["id"]
            try:
                tracks = (
                    service.edits()
                    .tracks()
                    .list(packageName=package_name, editId=edit_id)
                    .execute()
                )
            finally:
                service.edits().delete(packageName=package_name, editId=edit_id).execute()

            lines: list[str] = []
            primary_state = ReviewState.UNKNOWN
            track_list = tracks.get("tracks") or []
            order = ["production", "beta", "alpha", "internal"]
            by_name = {t.get("track"): t for t in track_list}
            target_vc = str(version_code) if version_code else None
            found_tracks: list[str] = []

            for name in order:
                t = by_name.get(name)
                if not t:
                    continue
                releases = t.get("releases") or []
                if not releases:
                    lines.append(f"{name}: (无版本)")
                    continue
                rel = releases[0]
                st = rel.get("status")
                codes_list = [str(c) for c in (rel.get("versionCodes") or [])]
                codes = ",".join(codes_list)
                rname = rel.get("name") or "-"
                mark = ""
                if target_vc and target_vc in codes_list:
                    mark = " ← target"
                    found_tracks.append(f"{name}:{st}")
                lines.append(f"{name}: {rname} codes=[{codes}] status={st}{mark}")
                if name == prefer or (
                    prefer not in by_name
                    and name == "production"
                    and primary_state == ReviewState.UNKNOWN
                ):
                    primary_state = _map_release_status(st, name)

            prefer_data = by_name.get(prefer)
            if prefer_data and prefer_data.get("releases"):
                primary_state = _map_release_status(
                    prefer_data["releases"][0].get("status"), prefer
                )

            if target_vc:
                if found_tracks:
                    lines.insert(
                        0,
                        f"target versionCode={target_vc} 出现在: {', '.join(found_tracks)}",
                    )
                else:
                    lines.insert(
                        0,
                        f"target versionCode={target_vc} 尚未出现在 production/beta/alpha/internal",
                    )
                lines.append(CONSOLE_HINT)

            return ReviewStatus(
                app_id=app_cfg.get("id", ""),
                platform=Platform.ANDROID,
                version_name=version_name or (f"vc={target_vc}" if target_vc else None),
                state=primary_state,
                message="\n".join(lines) or "无轨道数据",
                raw=tracks,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("google.status error: {}", exc)
            from app.stores.google_errors import format_google_status_error

            return ReviewStatus(
                app_id=app_cfg.get("id", ""),
                platform=Platform.ANDROID,
                version_name=version_name,
                state=ReviewState.UNKNOWN,
                message=format_google_status_error(exc),
            )
