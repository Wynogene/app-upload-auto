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


def is_retryable_media_upload_error(exc: BaseException) -> bool:
    """卡住 / 分片失败 / 常见传输故障 → 可整包重试。"""
    text = str(exc)
    if "无字节进度" in text or "判定卡住" in text or "上传分片失败" in text:
        return True
    lower = text.lower()
    return any(
        h in lower
        for h in (
            "timeout",
            "timed out",
            "connection",
            "proxy",
            "ssl",
            "reset",
            "unavailable",
            "503",
            "502",
            "429",
        )
    )


def _format_mib(num_bytes: int | float) -> str:
    return f"{float(num_bytes) / (1024 * 1024):.1f}"


def list_edit_bundle_version_codes(
    service: Any, package_name: str, edit_id: str
) -> set[str]:
    """列出当前 edit 可见的已上传 bundle versionCode（含历史已入库的包）。"""
    try:
        payload = (
            service.edits()
            .bundles()
            .list(packageName=package_name, editId=edit_id)
            .execute(num_retries=2)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("list bundles failed: {}", exc)
        return set()
    out: set[str] = set()
    for b in payload.get("bundles") or []:
        vc = b.get("versionCode")
        if vc is not None:
            out.add(str(vc))
    return out


def execute_resumable_media_upload(
    request: Any,
    *,
    total_size: int,
    label: str = "bundle",
    num_retries: int = 5,
    stall_timeout_seconds: float = 180.0,
) -> Any:
    """执行可续传媒体上传，并按已传 MB（兼百分比）打进度日志。

    googleapiclient 的 ``.execute()`` 不会回调进度；大 AAB 需 ``next_chunk`` 循环。

    若 ``stall_timeout_seconds`` 内已传字节数无增长，判定卡死并抛错
    （避免线程长时间空转、流量为 0 却一直挂着）。
    """
    import time

    total = max(int(total_size or 0), 0)
    total_note = _format_mib(total) if total else "?"
    logger.info(
        "google.{} upload start: {} MB (chunked resumable)",
        label,
        total_note,
    )
    response = None
    last_logged_bytes = -1
    last_done = 0
    last_progress_at = time.time()
    # 至少每约 8MiB 打一条，避免刷屏；收尾必打
    log_every = 8 * 1024 * 1024
    while response is None:
        try:
            progress, response = request.next_chunk(num_retries=num_retries)
        except Exception as exc:  # noqa: BLE001
            # 底层已按 num_retries 重试；仍失败则视为传包异常
            raise RuntimeError(
                f"Google {label} 上传分片失败（可能网络中断/代理无流量）: {exc}"
            ) from exc
        if progress is None:
            if (
                stall_timeout_seconds > 0
                and (time.time() - last_progress_at) > stall_timeout_seconds
            ):
                raise RuntimeError(
                    f"Google {label} 上传超过 {int(stall_timeout_seconds)}s "
                    f"无字节进度（已停在 {_format_mib(last_done)} MB），"
                    "判定卡住；请检查网络/代理后重试（支持断点续传会话需重新发起上传）。"
                )
            continue
        done = int(getattr(progress, "resumable_progress", 0) or 0)
        known_total = int(getattr(progress, "total_size", 0) or 0) or total
        if known_total and total != known_total:
            total = known_total
            total_note = _format_mib(total)
        if done > last_done:
            last_done = done
            last_progress_at = time.time()
        elif (
            stall_timeout_seconds > 0
            and (time.time() - last_progress_at) > stall_timeout_seconds
        ):
            raise RuntimeError(
                f"Google {label} 上传超过 {int(stall_timeout_seconds)}s "
                f"无字节进度（已停在 {_format_mib(last_done)} MB），"
                "判定卡住；请检查网络/代理后重试。"
            )
        should_log = (
            done >= total > 0
            or last_logged_bytes < 0
            or (done - last_logged_bytes) >= log_every
        )
        if not should_log:
            continue
        last_logged_bytes = done
        if total > 0:
            pct = min(100.0, 100.0 * done / total)
            logger.info(
                "google.{} upload progress: {}/{} MB ({:.0f}%)",
                label,
                _format_mib(done),
                total_note,
                pct,
            )
        else:
            logger.info(
                "google.{} upload progress: {} MB",
                label,
                _format_mib(done),
            )
    logger.info("google.{} upload complete: {} MB", label, total_note)
    return response


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

    def _authorized_session(self, app_cfg: dict | None = None):
        """带代理的 AuthorizedSession（生命周期只读 GET 与 Publisher 共用）。"""
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account

        from app.config import apply_proxy_env, get_google_proxy_url

        apply_proxy_env()
        sa_path = self._sa_path(app_cfg or {})
        if not sa_path.exists():
            raise RuntimeError(f"Google Play 服务账号文件不存在: {sa_path}")
        creds = service_account.Credentials.from_service_account_file(
            str(sa_path),
            scopes=["https://www.googleapis.com/auth/androidpublisher"],
        )
        session = AuthorizedSession(creds)
        proxy_url = get_google_proxy_url()
        if proxy_url:
            session.proxies.update({"http": proxy_url, "https": proxy_url})
        return session

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
        from googleapiclient.discovery import build

        from app.config import get_google_proxy_url

        session = self._authorized_session(app_cfg)
        proxy_url = get_google_proxy_url()
        if proxy_url:
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
        # userFraction 的合法性（实测得出的 Google 行为）：
        #   * completed -> 绝不能带（会 400）
        #   * inProgress -> 需要 (0, 1) 开区间
        #   * halted     -> 必须带，且同样不能是 1.0（否则 400 "User fraction must be less than 1"）
        if status in {"inprogress", "halted"}:
            if user_fraction is None:
                if status == "halted":
                    raise ValueError(
                        "停发(halted)必须指定放量比例（Google 要求），请加 --rollout，例如 --rollout 10"
                    )
            elif not 0 < float(user_fraction) < 1:
                raise ValueError(
                    f"放量比例必须介于 0 与 100% 之间（不含两端），当前为 {user_fraction}"
                )
            else:
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
            RolloutSpecError,
            describe_rollout,
            is_staged,
            release_status_for,
            resolve_rollout_fraction,
            validate_rollout_track,
        )

        try:
            rollout_fraction, rollout_source = resolve_rollout_fraction(
                explicit=getattr(req, "rollout_fraction", None),
                track=track,
                app_cfg=app_cfg,
            )
        except RolloutSpecError as exc:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message=f"分阶段发布配置有误：{exc}",
                details={"track": track},
            )
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

        # 卡住/传输失败后整包重试；每次重试前再查商店，避免「其实已上传成功」又传一遍
        max_full_attempts = 3
        last_exc: BaseException | None = None
        recovered_note = ""

        for attempt in range(1, max_full_attempts + 1):
            edit_id = None
            service = None
            skip_binary_upload = False
            version_code: Any = None
            upload_kind = "aab" if aab_path.suffix.lower() == ".aab" else "apk"
            try:
                service = self._publisher(app_cfg, timeout=600)
                edit = service.edits().insert(packageName=package_name, body={}).execute(
                    num_retries=3
                )
                edit_id = edit["id"]
                logger.info(
                    "created edit_id={} attempt={}/{}",
                    edit_id,
                    attempt,
                    max_full_attempts,
                )

                if aab_meta is not None:
                    tracks = (
                        service.edits()
                        .tracks()
                        .list(packageName=package_name, editId=edit_id)
                        .execute(num_retries=3)
                    )
                    used = collect_track_version_codes(tracks)
                    bundle_codes = list_edit_bundle_version_codes(
                        service, package_name, edit_id
                    )
                    vc_str = str(aab_meta.version_code)

                    if vc_str in used:
                        service.edits().delete(
                            packageName=package_name, editId=edit_id
                        ).execute(num_retries=2)
                        edit_id = None
                        if attempt == 1:
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
                                        used,
                                        key=lambda x: int(x) if x.isdigit() else 0,
                                    )[-12:],
                                },
                            )
                        # 重试前校验发现已在轨道：先前卡住实际已成功提交
                        return OperationResult(
                            ok=True,
                            app_id=req.app_id,
                            platform=Platform.ANDROID,
                            review_state=(
                                ReviewState.WAITING_FOR_REVIEW
                                if track == "production"
                                else ReviewState.RELEASED
                            ),
                            message=(
                                f"重试前校验：versionCode={aab_meta.version_code} "
                                f"已在商店轨道中，判定先前上传实际已成功，跳过重复上传。"
                            ),
                            details={
                                "package_name": package_name,
                                "track": track,
                                "version_code": aab_meta.version_code,
                                "artifact": str(aab_path),
                                "recovered_from_stall": True,
                                "attempt": attempt,
                            },
                        )

                    if vc_str in bundle_codes:
                        # 二进制已在 Play，只需挂轨提交（含卡住后重试发现已入库）
                        skip_binary_upload = True
                        version_code = aab_meta.version_code
                        recovered_note = (
                            f"（检测到 versionCode={vc_str} 已在 Play 包库，跳过重复传包）"
                        )
                        logger.info(
                            "skip binary upload: versionCode {} already in bundles",
                            vc_str,
                        )

                if not skip_binary_upload:
                    media = MediaFileUpload(
                        str(aab_path),
                        mimetype="application/octet-stream",
                        resumable=True,
                        chunksize=8 * 1024 * 1024,
                    )
                    upload_retries = 5
                    artifact_size = aab_path.stat().st_size
                    if aab_path.suffix.lower() == ".aab":
                        upload_req = (
                            service.edits()
                            .bundles()
                            .upload(
                                packageName=package_name,
                                editId=edit_id,
                                media_body=media,
                            )
                        )
                        bundle = execute_resumable_media_upload(
                            upload_req,
                            total_size=artifact_size,
                            label="aab",
                            num_retries=upload_retries,
                        )
                        version_code = bundle.get("versionCode")
                        upload_kind = "aab"
                    else:
                        upload_req = (
                            service.edits()
                            .apks()
                            .upload(
                                packageName=package_name,
                                editId=edit_id,
                                media_body=media,
                            )
                        )
                        apk = execute_resumable_media_upload(
                            upload_req,
                            total_size=artifact_size,
                            label="apk",
                            num_retries=upload_retries,
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
                    default_note = (
                        "（使用配置默认放量）" if rollout_source == "default" else ""
                    )
                    rollout_note = (
                        f"已按 {describe_rollout(rollout_fraction)} 放量{default_note}"
                        f"（{where}）。"
                        f"确认稳定后可续推，例如：\n"
                        f"  python cli.py release --app-id {req.app_id} "
                        f"--platform android --version-code {version_code} "
                        f"--track production --rollout 50 --allow-production\n"
                        f"全量则用 --rollout 100。"
                    )
                else:
                    rollout_note = f"请到 {where} 查看。"

                attempt_note = (
                    f"（第 {attempt} 次尝试成功{recovered_note}）"
                    if attempt > 1 or recovered_note
                    else ""
                )
                return OperationResult(
                    ok=True,
                    app_id=req.app_id,
                    platform=Platform.ANDROID,
                    review_state=review_state,
                    message=(
                        f"已上传并发布到 `{track}`，versionCode={version_code}。"
                        f"{attempt_note}"
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
                        "rollout_source": rollout_source,
                        "user_fraction": rollout_fraction if staged else None,
                        "track_response": track_resp,
                        "commit": commit,
                        "attempt": attempt,
                        "skipped_binary_upload": skip_binary_upload,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.exception(
                    "google.upload error attempt={}/{}", attempt, max_full_attempts
                )
                if service is not None and edit_id is not None:
                    try:
                        service.edits().delete(
                            packageName=package_name, editId=edit_id
                        ).execute(num_retries=2)
                        logger.info("discarded failed edit_id={}", edit_id)
                    except Exception:  # noqa: BLE001
                        logger.warning("failed to discard edit_id={}", edit_id)
                    edit_id = None

                # 「version code already used」：可能上次已传上，下一轮靠校验恢复
                already_used = "already been used" in str(exc).lower()
                can_retry = (
                    attempt < max_full_attempts
                    and (is_retryable_media_upload_error(exc) or already_used)
                )
                if can_retry:
                    logger.warning(
                        "upload will re-validate store then full retry ({}/{}): {}",
                        attempt + 1,
                        max_full_attempts,
                        exc,
                    )
                    continue
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
                        "attempt": attempt,
                    },
                )

        return OperationResult(
            ok=False,
            app_id=req.app_id,
            platform=Platform.ANDROID,
            message=format_google_upload_error(
                last_exc or RuntimeError("上传失败且已用尽重试")
            ),
            details={
                "package_name": package_name,
                "track": track,
                "artifact": str(aab_path),
                "aab_version_code": aab_meta.version_code if aab_meta else None,
                "aab_version_name": aab_meta.version_name if aab_meta else None,
                "attempts": max_full_attempts,
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
            RolloutSpecError,
            compare_rollout,
            describe_rollout,
            is_staged,
            resolve_rollout_fraction,
            validate_rollout_track,
        )

        explicit_rollout = getattr(req, "rollout_fraction", None)
        try:
            rollout_fraction, rollout_source = resolve_rollout_fraction(
                explicit=explicit_rollout,
                track=track,
                app_cfg=app_cfg,
                release_status=req.release_status,
            )
        except RolloutSpecError as exc:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                message=f"分阶段发布配置有误：{exc}",
                details={"track": track},
            )
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
        # 分了批就由 rollout 决定 status；--rollout 100（或默认配成 100）转全量；
        # 其余沿用 release_status（draft/halted/completed）。
        if staged:
            desired_status = "inprogress"
        elif rollout_fraction is not None and rollout_fraction >= 1:
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
        downgrade_warning = None
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

                # 判定是否属于「无意义的推进」：
                #   1. 同在分批中，但放量没变大（相等或缩量）-> 不会产生新审核
                #   2. 状态完全相同（如都已 completed）-> 同样无意义
                # 注意：completed(100%) -> 分批(inProgress) 经实测 **是被 Google 接受的**，
                # 因此不再硬拦，只提示「已放量的用户无法回收」。
                skip_reason = None
                skip_hint = None
                downgrade_warning = None
                if same_version:
                    if current_status == "completed" and desired_status == "inprogress":
                        downgrade_warning = (
                            "注意：该版本此前已是全面发布（100%）。"
                            "改成分批只影响后续放量，**已经收到更新的用户无法回收**。"
                        )
                    elif desired_status == current_status == "inprogress":
                        if compare_rollout(current_fraction, rollout_fraction) <= 0:
                            skip_reason = "fraction_not_increased"
                            skip_hint = (
                                f"当前已放量 {describe_rollout(None if current_fraction is None else float(current_fraction))}，"
                                f"放量未增加，不会产生新的审核。续推请传更大的 --rollout"
                                f"（例如当前 10%，续推用 --rollout 20；全量用 --rollout 100）。"
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

                # halted 必须带 (0,1) 的 userFraction：没显式给就沿用轨道当前比例
                if desired_status == "halted" and not is_staged(rollout_fraction):
                    if current_fraction is not None and 0 < float(current_fraction) < 1:
                        rollout_fraction = float(current_fraction)
                        staged = True
                    else:
                        service.edits().delete(
                            packageName=package_name, editId=edit_id
                        ).execute(num_retries=2)
                        edit_id = None
                        return OperationResult(
                            ok=False,
                            app_id=req.app_id,
                            platform=Platform.ANDROID,
                            message=(
                                "停发(halted) 需要指定放量比例（Google 要求，且不能为 100%）。"
                                "轨道上当前没有可沿用的比例，请显式加 --rollout，例如 --rollout 10。"
                            ),
                            details={
                                "package_name": package_name,
                                "track": track,
                                "version_code": str(version_code),
                                "desired_status": desired_status,
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
                default_note = (
                    "（使用配置默认放量）" if rollout_source == "default" else ""
                )
                tail = (
                    f"已按 {describe_rollout(rollout_fraction)} 放量{default_note}。"
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
                    f"{downgrade_warning or ''}"
                ),
                details={
                    "package_name": package_name,
                    "track": track,
                    "version_code": str(version_code),
                    "release_status": desired_status,
                    "downgraded_from_full": bool(downgrade_warning),
                    "rollout": describe_rollout(rollout_fraction),
                    "rollout_fraction": rollout_fraction,
                    "rollout_source": rollout_source,
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
        from app.core.watch_targets import ANDROID_HINT

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
                frac = rel.get("userFraction")
                frac_note = f" rollout={frac}" if frac is not None else ""
                mark = ""
                if target_vc and target_vc in codes_list:
                    mark = " ← target"
                    found_tracks.append(f"{name}:{st}")
                lines.append(
                    f"{name}: {rname} codes=[{codes}] status={st}{frac_note}{mark}"
                )
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

            # --- 只读生命周期（抓住过审）：优先于旧 tracks.status ---
            lifecycle_raw: dict | None = None
            lifecycle_state_raw: str | None = None
            lifecycle_key: str | None = None
            life_header: str | None = None
            try:
                from app.stores.google_lifecycle import (
                    describe_lifecycle,
                    find_release_for_version,
                    list_track_releases,
                    map_lifecycle_to_review_state,
                    normalize_lifecycle,
                    version_codes_of,
                )

                life_track = prefer if prefer in order else "production"
                life_releases = list_track_releases(
                    self._authorized_session(app_cfg),
                    package_name=package_name,
                    track=life_track,
                )
                life_rel = find_release_for_version(life_releases, target_vc)
                if life_rel is None and life_releases and not target_vc:
                    life_rel = life_releases[0]
                if life_rel:
                    lifecycle_raw = life_rel
                    lifecycle_state_raw = life_rel.get("releaseLifecycleState")
                    lifecycle_key = normalize_lifecycle(lifecycle_state_raw)
                    mapped = map_lifecycle_to_review_state(lifecycle_state_raw)
                    if mapped != ReviewState.UNKNOWN:
                        primary_state = mapped
                    life_codes = ",".join(version_codes_of(life_rel)) or "-"
                    life_header = (
                        f"lifecycle[{life_track}]: "
                        f"{lifecycle_key} "
                        f"({describe_lifecycle(lifecycle_state_raw)}) "
                        f"codes=[{life_codes}] "
                        f"name={life_rel.get('releaseName') or '-'}"
                    )
            except Exception as life_exc:  # noqa: BLE001
                # 生命周期接口失败时降级旧 tracks，不阻断盯盘
                logger.warning("google lifecycle fetch failed: {}", life_exc)
                life_header = f"lifecycle: 查询失败（已降级旧 tracks）: {life_exc}"

            headers: list[str] = []
            if life_header:
                headers.append(life_header)
            if target_vc:
                if found_tracks:
                    headers.append(
                        f"target versionCode={target_vc} 出现在: {', '.join(found_tracks)}"
                    )
                else:
                    headers.append(
                        f"target versionCode={target_vc} 尚未出现在 production/beta/alpha/internal"
                    )
            lines = headers + lines
            if target_vc:
                lines.append(ANDROID_HINT)

            raw_out = dict(tracks) if isinstance(tracks, dict) else {"tracks": tracks}
            if lifecycle_raw is not None:
                raw_out["lifecycle_release"] = lifecycle_raw
                raw_out["lifecycle_state"] = lifecycle_key

            return ReviewStatus(
                app_id=app_cfg.get("id", ""),
                platform=Platform.ANDROID,
                version_name=version_name or (f"vc={target_vc}" if target_vc else None),
                state=primary_state,
                message="\n".join(lines) or "无轨道数据",
                raw=raw_out,
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
