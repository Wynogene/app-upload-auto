"""App Store Connect adapter.

Auth: JWT (ES256) with Key ID + Issuer ID + .p8
Upload: ASC Build Upload API（默认 dry-run，``execute=True`` 才写）
Submit: reviewSubmissions + what's New（同上）
Status: App Store Connect REST API（只读）
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import jwt
from loguru import logger

from app.config import ROOT_DIR, get_settings, resolve_apple_token_sub
from app.core.versions import is_version_greater
from app.models import (
    OperationResult,
    Platform,
    ReviewState,
    ReviewStatus,
    SubmitRequest,
    UploadRequest,
)
from app.stores.apple_states import (
    describe_build_processing_state,
    describe_version_state,
    map_version_state,
    pick_version_state,
    build_processing_stuck_note,
)
from app.stores.base import StoreClient

ASC_BASE = "https://api.appstoreconnect.apple.com"

# /v1/apps/{id} 的 relationships 字段有上百行链接，对排查状态毫无用处，只留关键属性
_APP_KEEP_KEYS = ("name", "bundleId", "sku", "primaryLocale")


@dataclass(frozen=True)
class AppleCred:
    """一套 App Store Connect 凭据（一个 Apple 开发者团队一套）。"""

    key_id: str
    issuer_id: str
    key_path: Path
    token_sub: str | None = None
    source: str = "env"  # env=来自 .env 全局；app=来自 apps.yaml 的 ios.*


def resolve_apple_cred(app_cfg: dict | None = None) -> AppleCred:
    """按 App 解析 ASC 凭据，未配置则回退到 .env 的全局凭据。

    与 Android 的 `android.service_account_json` 对称：因为 App Store Connect 的
    API 密钥是**按 Apple 开发者团队隔离**的，不同公司主体（不同团队）必须各用各的
    Key ID / Issuer ID / .p8。
    """
    settings = get_settings()
    ios = (app_cfg or {}).get("ios") or {}

    raw_path = ios.get("private_key_path") or settings.apple_private_key_path
    key_path = Path(raw_path)
    if not key_path.is_absolute():
        key_path = ROOT_DIR / key_path

    key_id = ios.get("key_id") or settings.apple_key_id
    issuer_id = ios.get("issuer_id") or settings.apple_issuer_id
    from_app = any(ios.get(k) for k in ("key_id", "issuer_id", "private_key_path"))

    return AppleCred(
        key_id=key_id,
        issuer_id=issuer_id,
        key_path=key_path,
        token_sub=resolve_apple_token_sub(key_path, ios.get("token_sub")),
        source="app" if from_app else "env",
    )


def _compact_app(payload: dict) -> dict:
    """把 /v1/apps/{id} 的响应压成可读的几行。"""
    data = (payload or {}).get("data") or {}
    attrs = data.get("attributes") or {}
    return {
        "asc_app_id": data.get("id"),
        **{k: attrs.get(k) for k in _APP_KEEP_KEYS},
    }


class AppleStoreClient(StoreClient):
    platform = Platform.IOS

    def _cred(self, app_cfg: dict | None = None) -> AppleCred:
        return resolve_apple_cred(app_cfg)

    def _token(self, app_cfg: dict | None = None) -> str:
        cred = self._cred(app_cfg)
        if not cred.key_id or not cred.issuer_id:
            raise RuntimeError(
                "未配置 Apple 凭据：请在 .env 设 APPLE_KEY_ID / APPLE_ISSUER_ID，"
                "或在 apps.yaml 的 ios 下按 App 单独配置"
            )
        if not cred.key_path.exists():
            raise RuntimeError(f"Apple 私钥不存在: {cred.key_path}")

        private_key = cred.key_path.read_text(encoding="utf-8")
        now = int(time.time())
        payload = {
            "iss": cred.issuer_id,
            "iat": now,
            "exp": now + 15 * 60,  # Apple 上限 20 分钟，留点余量
            "aud": "appstoreconnect-v1",
        }
        # 个人密钥(ApiKey_*.p8)必须带 sub="user"，团队密钥(AuthKey_*.p8)不能带，
        # 否则都会返回 401 NOT_AUTHORIZED。
        if cred.token_sub:
            payload["sub"] = cred.token_sub
        headers = {"alg": "ES256", "kid": cred.key_id, "typ": "JWT"}
        return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)

    def _client(self) -> httpx.Client:
        """ASC 默认直连，不继承 HTTP(S)_PROXY 环境变量（可用 APPLE_USE_PROXY=true 打开）。"""
        settings = get_settings()
        return httpx.Client(timeout=30, trust_env=bool(settings.apple_use_proxy))

    def _headers(self, app_cfg: dict | None = None) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token(app_cfg)}",
            "Content-Type": "application/json",
        }

    def check_connectivity(self, app_cfg: dict | None = None) -> dict:
        """自检：签名 JWT → 调 /v1/apps，返回可读的结论（不抛异常）。"""
        settings = get_settings()
        cred = self._cred(app_cfg)
        info: dict = {
            "app_id": (app_cfg or {}).get("id"),
            "key_id": cred.key_id,
            "issuer_id": cred.issuer_id,
            "key_path": str(cred.key_path),
            "key_required_sub": cred.token_sub,
            "cred_source": cred.source,
            "use_proxy": bool(settings.apple_use_proxy),
        }
        if not cred.key_id or not cred.issuer_id:
            info.update(
                ok=False,
                message=(
                    "未配置 Apple 凭据。.env 设 APPLE_KEY_ID / APPLE_ISSUER_ID，"
                    "或在 config/apps.yaml 的该 App ios 下配置 key_id / issuer_id / private_key_path"
                ),
            )
            return info
        if not cred.key_path.exists():
            info.update(ok=False, message=f".p8 文件不存在: {cred.key_path}")
            return info

        try:
            token = self._token(app_cfg)
        except Exception as exc:  # noqa: BLE001
            info.update(ok=False, message=f"JWT 签名失败: {exc}")
            return info

        try:
            with self._client() as client:
                resp = client.get(
                    f"{ASC_BASE}/v1/apps",
                    params={"limit": 50},
                    headers={"Authorization": f"Bearer {token}"},
                )
        except Exception as exc:  # noqa: BLE001
            info.update(ok=False, message=f"网络不可达: {exc}")
            return info

        if resp.status_code == 401:
            info.update(
                ok=False,
                status_code=401,
                message=(
                    "401 NOT_AUTHORIZED：网络已通，但鉴权失败。"
                    "常见原因：① 个人密钥缺 sub=\"user\"（或团队密钥误加 sub）"
                    " ② Key ID / Issuer ID 与 .p8 不匹配 ③ 该密钥已被撤销或过期"
                ),
            )
            return info
        if resp.status_code >= 400:
            info.update(
                ok=False,
                status_code=resp.status_code,
                message=f"ASC 返回 HTTP {resp.status_code}",
                raw=resp.text[:500],
            )
            return info

        apps = resp.json().get("data", [])
        listing = [
            {
                "asc_app_id": a.get("id"),
                "bundle_id": (a.get("attributes") or {}).get("bundleId"),
                "name": (a.get("attributes") or {}).get("name"),
            }
            for a in apps
        ]
        info.update(
            ok=True,
            status_code=resp.status_code,
            apps=listing,
            message=f"连通正常，可见 {len(apps)} 个 App",
        )

        # 额外校验：apps.yaml 里登记的目标 App 是否真在这把密钥的可见范围内。
        # 不在的话，后续调用会得到 404（而不是 403），最容易被误判成「App 不存在」。
        target = ((app_cfg or {}).get("ios") or {}).get("app_store_app_id")
        if target:
            visible_ids = {a["asc_app_id"] for a in listing}
            info["target_app_store_app_id"] = str(target)
            info["target_app_visible"] = str(target) in visible_ids
            if not info["target_app_visible"]:
                info["warning"] = (
                    f"凭据本身有效，但 apps.yaml 登记的 app_store_app_id={target} "
                    "不在这把密钥可见的 App 列表里。该 App 很可能属于**另一个 Apple 开发者团队**"
                    "（API 密钥按团队隔离，Issuer ID 也是团队级），"
                    "需在 config/apps.yaml 的该 App ios 下填该团队自己的 "
                    "key_id / issuer_id / private_key_path。"
                )
                info["message"] += f"；但目标 App {target} 不在可见列表"
        return info

    def preflight_ipa(self, app_cfg: dict, ipa_path: str | Path) -> dict:
        """上传前校验：解析 IPA + 拉取 ASC 现状 + 跑三道硬校验。

        返回 {ok, meta, issues, message}；不抛异常（除了解析失败的友好错误）。
        """
        from app.stores.apple_preflight import (
            check_ipa_preflight,
            format_issues,
            has_errors,
        )
        from app.stores.ipa_meta import IpaMeta, IpaParseError, parse_ipa_meta

        ios = app_cfg.get("ios") or {}
        out: dict = {"ok": False, "meta": None, "issues": [], "message": ""}

        try:
            meta: IpaMeta = parse_ipa_meta(ipa_path)
        except IpaParseError as exc:
            out["message"] = str(exc)
            return out

        out["meta"] = meta

        # 收集 ASC 现状；拉取失败不阻断（退化为「只做本地能做的校验」）
        latest_released: str | None = None
        released_versions: set[str] = set()
        existing_builds: set[str] = set()
        version_strings: set[str] = set()
        versions_in_review: set[str] = set()
        notes: list[str] = []
        try:
            versions = self.list_versions(app_cfg, limit=50)
            for v in versions:
                vs = v.get("version_string")
                if not vs:
                    continue
                version_strings.add(vs)
                if v.get("mapped") == ReviewState.RELEASED.value:
                    released_versions.add(vs)
                    if latest_released is None or is_version_greater(vs, latest_released):
                        latest_released = vs
                if v.get("mapped") in {
                    ReviewState.WAITING_FOR_REVIEW.value,
                    ReviewState.IN_REVIEW.value,
                }:
                    versions_in_review.add(vs)
            existing_builds = {
                str(b.get("version")) for b in self.list_builds(app_cfg, limit=100) if b.get("version")
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("preflight: 拉取 ASC 现状失败, 降级为本地校验: {}", exc)
            notes.append(f"未能读取 ASC 现状（{exc}），以下校验不含线上比对")

        issues = check_ipa_preflight(
            meta,
            expected_bundle_id=ios.get("bundle_id"),
            latest_released_version=latest_released,
            released_versions=released_versions,
            existing_build_versions=existing_builds,
            version_strings=version_strings,
            versions_in_review=versions_in_review,
        )
        out["issues"] = issues
        out["latest_released_version"] = latest_released
        out["existing_build_count"] = len(existing_builds)
        out["ok"] = not has_errors(issues)
        out["message"] = format_issues(issues) or "上传前校验通过"
        for n in notes:
            out["message"] += f"\n[提示] {n}"
        return out

    def upload(self, req: UploadRequest, app_cfg: dict) -> OperationResult:
        """上传 IPA 到 ASC（Build Upload API）。

        默认 ``execute=False``：只做预检 + 上传计划，**不写商店**。
        ``execute=True``：真实分片上传并等到 build VALID。
        """
        from app.stores.apple_build_upload import execute_build_upload, plan_build_upload

        ios = app_cfg.get("ios") or {}
        artifact = req.artifact_path or ios.get("artifact_path")
        artifact_url = req.artifact_url or ios.get("artifact_url")
        logger.info(
            "apple.upload app={} artifact={} url={} execute={}",
            req.app_id,
            artifact,
            artifact_url,
            req.execute,
        )
        if not artifact and not artifact_url:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.IOS,
                message="未提供 IPA 路径或 artifact_url",
            )

        if not artifact:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.IOS,
                message=(
                    "仅提供了 artifact_url：请先下载为本地 IPA 再上传"
                    "（群晖分享页不是直链）。可用 card-run --dry-resolve 或手动下载。"
                ),
                details={"artifact_url": artifact_url},
            )

        pre = self.preflight_ipa(app_cfg, artifact)
        meta = pre.get("meta")
        version_name = req.version_name or getattr(meta, "version_name", None)
        build_number = req.build_number or getattr(meta, "build_number", None)
        base_details = {
            "artifact": str(artifact),
            "bundle_id": getattr(meta, "bundle_id", None),
            "version_name": version_name,
            "build_number": build_number,
            "latest_released_version": pre.get("latest_released_version"),
            "execute": bool(req.execute),
        }
        if not pre.get("ok"):
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.IOS,
                message=f"上传前校验未通过，已阻止上传：\n{pre.get('message')}",
                details=base_details,
            )
        if not version_name or not build_number:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.IOS,
                message="无法从 IPA 解析 version / build",
                details=base_details,
            )

        app_store_app_id = ios.get("app_store_app_id")
        if not app_store_app_id:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.IOS,
                message="apps.yaml 缺少 ios.app_store_app_id",
                details=base_details,
            )

        try:
            plan = plan_build_upload(
                app_store_app_id=str(app_store_app_id),
                ipa_path=artifact,
                version_name=str(version_name),
                build_number=str(build_number),
            )
        except Exception as exc:  # noqa: BLE001
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.IOS,
                message=f"准备上传计划失败: {exc}",
                details=base_details,
            )

        base_details.update(
            {
                "file_size": plan.file_size,
                "md5": plan.md5_hex,
                "planned_steps": plan.steps,
            }
        )

        if not req.execute:
            return OperationResult(
                ok=True,
                app_id=req.app_id,
                platform=Platform.IOS,
                message=(
                    "iOS 上传 dry-run：预检通过，未写 ASC。"
                    f"包 {plan.file_name} ({plan.file_size} bytes) "
                    f"version={plan.cf_bundle_short_version} "
                    f"build={plan.cf_bundle_version}。"
                    "真正上传请加 --execute。"
                ),
                details={**base_details, "dry_run": True},
            )

        settings = get_settings()
        with httpx.Client(
            timeout=httpx.Timeout(120.0, connect=30.0),
            trust_env=bool(settings.apple_use_proxy),
        ) as client:
            result = execute_build_upload(
                client=client,
                headers=self._headers(app_cfg),
                plan=plan,
            )
        base_details.update(result.details)
        return OperationResult(
            ok=result.ok,
            app_id=req.app_id,
            platform=Platform.IOS,
            message=result.message,
            details={
                **base_details,
                "build_id": result.build_id,
                "build_upload_id": result.build_upload_id,
                "build_processing_state": result.build_processing_state,
            },
        )

    def submit(self, req: SubmitRequest, app_cfg: dict) -> OperationResult:
        """iOS：创建/复用版本、挂构建、写 what's New、reviewSubmissions 提审。

        默认 ``execute=False`` 只输出计划；``execute=True`` 才写 ASC。
        """
        from app.stores.apple_review_submit import (
            ReviewSubmitPlan,
            build_review_plan_steps,
            execute_review_submit,
        )
        from app.stores.release_notes import (
            build_release_notes,
            format_issues,
            has_errors,
            validate_notes,
        )

        ios = app_cfg.get("ios") or {}
        app_store_app_id = ios.get("app_store_app_id")
        logger.info(
            "apple.submit app={} asc_app_id={} version={} build_id={} execute={}",
            req.app_id,
            app_store_app_id,
            req.version_name,
            req.build_id,
            req.execute,
        )
        if not app_store_app_id:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.IOS,
                message="apps.yaml 缺少 ios.app_store_app_id",
            )

        raw: list[str] = []
        for locale, text in (req.release_notes or {}).items():
            raw.append(f"{locale}={text}")
        if req.whats_new:
            raw.append(req.whats_new)

        try:
            notes, notes_source = build_release_notes(
                raw or None,
                app_cfg,
                platform="ios",
            )
        except Exception as exc:  # noqa: BLE001
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.IOS,
                message=f"版本说明格式有误：{exc}",
                details={"app_store_app_id": app_store_app_id},
            )

        issues = validate_notes(
            notes,
            track="production",
            is_production=True,
            platform="ios",
        )
        if has_errors(issues):
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.IOS,
                message=f"版本说明校验未通过：\n{format_issues(issues)}",
                details={"app_store_app_id": app_store_app_id},
            )

        text_by_locale = {
            str(n.get("language")): str(n.get("text") or "")
            for n in (notes or [])
            if n.get("language")
        }
        fallback = ""
        if notes:
            fallback = str(notes[0].get("text") or "")

        version_name = req.version_name
        build_id = req.build_id
        if not version_name:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.IOS,
                message="提审需要 version_name（CFBundleShortVersionString）",
            )

        plan = ReviewSubmitPlan(
            app_store_app_id=str(app_store_app_id),
            version_string=str(version_name),
            build_id=build_id,
            whats_new_by_locale=text_by_locale,
            fallback_whats_new=fallback,
        )
        plan.steps = build_review_plan_steps(plan)
        details = {
            "app_store_app_id": app_store_app_id,
            "release_notes": notes,
            "release_notes_source": notes_source,
            "planned_steps": plan.steps,
            "execute": bool(req.execute),
            "build_id": build_id,
        }

        if not req.execute:
            return OperationResult(
                ok=True,
                app_id=req.app_id,
                platform=Platform.IOS,
                message=(
                    "iOS 提审 dry-run：版本说明已解析，未写 ASC。"
                    f"version={version_name} build_id={build_id or '（未指定）'}。"
                    "真正提审请加 --execute。"
                ),
                details={**details, "dry_run": True},
            )

        if not build_id:
            return OperationResult(
                ok=False,
                app_id=req.app_id,
                platform=Platform.IOS,
                message="真实提审需要 build_id（请先 upload --execute 或传入已有构建 id）",
                details=details,
            )

        settings = get_settings()
        with httpx.Client(
            timeout=httpx.Timeout(60.0, connect=30.0),
            trust_env=bool(settings.apple_use_proxy),
        ) as client:
            result = execute_review_submit(
                client=client,
                headers=self._headers(app_cfg),
                plan=plan,
            )
        details.update(result.details)
        return OperationResult(
            ok=result.ok,
            app_id=req.app_id,
            platform=Platform.IOS,
            review_state=(
                ReviewState.WAITING_FOR_REVIEW if result.ok else ReviewState.UNKNOWN
            ),
            message=result.message,
            details={
                **details,
                "version_id": result.version_id,
                "submission_id": result.submission_id,
            },
        )

    def status(self, app_cfg: dict, version_name: str | None = None) -> ReviewStatus:
        """Query latest version / review state via ASC API."""
        ios = app_cfg.get("ios") or {}
        app_store_app_id = ios.get("app_store_app_id")
        if not app_store_app_id:
            return ReviewStatus(
                app_id=app_cfg.get("id", ""),
                platform=Platform.IOS,
                version_name=version_name,
                state=ReviewState.UNKNOWN,
                message="缺少 ios.app_store_app_id",
            )

        cred = self._cred(app_cfg)
        if not (cred.key_id and cred.key_path.exists()):
            return ReviewStatus(
                app_id=app_cfg.get("id", ""),
                platform=Platform.IOS,
                version_name=version_name,
                state=ReviewState.UNKNOWN,
                message="iOS 凭据未配置（APPLE_KEY_ID / APPLE_PRIVATE_KEY_PATH）",
            )

        app_id = app_cfg.get("id", "")
        try:
            with self._client() as client:
                headers = self._headers(app_cfg)
                params = {
                    "limit": 10,
                    "fields[appStoreVersions]": (
                        "versionString,appStoreState,appVersionState,"
                        "createdDate,platform,releaseType"
                    ),
                }
                if version_name:
                    params["filter[versionString]"] = version_name
                resp = client.get(
                    f"{ASC_BASE}/v1/apps/{app_store_app_id}/appStoreVersions",
                    params=params,
                    headers=headers,
                )

                if resp.status_code >= 400:
                    hint = ""
                    if resp.status_code == 404:
                        hint = (
                            "（该 App 可能属于另一个 Apple 开发者团队：API 密钥按团队隔离，"
                            "需在 apps.yaml 的 ios 下配置该团队独立凭据）"
                        )
                    return ReviewStatus(
                        app_id=app_id,
                        platform=Platform.IOS,
                        version_name=version_name,
                        state=ReviewState.UNKNOWN,
                        message=f"ASC 查询失败: HTTP {resp.status_code}{hint}",
                        raw={"body": resp.text[:500]},
                    )

                versions = resp.json().get("data") or []
                if not versions:
                    scope = f"版本 {version_name}" if version_name else "任何版本"
                    return ReviewStatus(
                        app_id=app_id,
                        platform=Platform.IOS,
                        version_name=version_name,
                        state=ReviewState.UNKNOWN,
                        message=f"ASC 中未找到{scope}（可能尚未创建或还未上传构建版本）",
                    )

                # 该端点不支持 sort，改为按创建时间在本地取最新
                versions.sort(
                    key=lambda v: (v.get("attributes") or {}).get("createdDate") or "",
                    reverse=True,
                )
                target = versions[0]
                attrs = target.get("attributes") or {}
                raw_state = pick_version_state(attrs)
                vstr = attrs.get("versionString")
                release_type = attrs.get("releaseType")
                state = map_version_state(raw_state)
                state_label = describe_version_state(raw_state)

                build_info = self._fetch_version_build(
                    client, target.get("id"), app_cfg, headers
                )
                phased_attrs = self._fetch_phased_release(
                    client, target.get("id"), headers
                )

                from app.stores.apple_phased import describe_phased_release

                msg = f"{vstr}：{state_label}"
                if release_type:
                    release_type_label = {
                        "MANUAL": "手动发布",
                        "AFTER_APPROVAL": "过审后自动发布",
                        "SCHEDULED": "定时发布",
                    }.get(str(release_type), str(release_type))
                    msg += f"；发布方式={release_type_label}"
                if build_info:
                    msg += (
                        f"；构建 {build_info.get('version')}："
                        f"{describe_build_processing_state(build_info.get('processingState'))}"
                    )
                    stuck = build_processing_stuck_note(
                        build_info.get("processingState"),
                        build_info.get("uploadedDate"),
                    )
                    if stuck:
                        msg += f"；{stuck}"
                elif state == ReviewState.DRAFT:
                    msg += "；该版本还没关联构建版本，需先上传 IPA"

                phased_desc = describe_phased_release(phased_attrs)
                if phased_desc:
                    msg += f"；{phased_desc}"
                elif state == ReviewState.RELEASED:
                    msg += "；未启用分批（或分批记录不可用）"

                if state == ReviewState.APPROVED and raw_state == "PENDING_DEVELOPER_RELEASE":
                    msg += "。注意：这是「手动发布」模式，需你到 ASC 点发布才会对用户生效"

                return ReviewStatus(
                    app_id=app_id,
                    platform=Platform.IOS,
                    version_name=vstr,
                    state=state,
                    raw={
                        "app_store_app_id": app_store_app_id,
                        "version_string": vstr,
                        "app_version_state": raw_state,
                        "app_version_state_label": state_label,
                        "release_type": release_type,
                        "build": build_info,
                        "phased_release": phased_attrs,
                    },
                    message=msg,
                )
        except Exception as exc:  # noqa: BLE001
            logger.exception("apple.status error")
            return ReviewStatus(
                app_id=app_id,
                platform=Platform.IOS,
                version_name=version_name,
                state=ReviewState.UNKNOWN,
                message=f"status 异常: {exc}",
            )

    def _fetch_version_build(
        self,
        client: httpx.Client,
        version_id: str | None,
        app_cfg: dict,
        headers: dict[str, str],
    ) -> dict | None:
        """取该 appStoreVersion 关联的构建；失败不影响主流程。"""
        if not version_id:
            return None
        try:
            resp = client.get(
                f"{ASC_BASE}/v1/appStoreVersions/{version_id}/build",
                headers=headers,
                params={
                    "fields[builds]": "version,processingState,uploadedDate,expired",
                },
            )
            if resp.status_code >= 400:
                return None
            data = (resp.json() or {}).get("data")
            if not data:
                return None
            attrs = data.get("attributes") or {}
            return {
                "build_id": data.get("id"),
                "version": attrs.get("version"),
                "processingState": attrs.get("processingState"),
                "expired": attrs.get("expired"),
                "uploadedDate": attrs.get("uploadedDate"),
            }
        except Exception:  # noqa: BLE001
            return None

    def _fetch_phased_release(
        self,
        client: httpx.Client,
        version_id: str | None,
        headers: dict[str, str],
    ) -> dict | None:
        """只读拉取分批发布；404/空表示未启用，不视为错误。"""
        if not version_id:
            return None
        from app.stores.apple_phased import extract_phased_attrs

        try:
            resp = client.get(
                f"{ASC_BASE}/v1/appStoreVersions/{version_id}/appStoreVersionPhasedRelease",
                headers=headers,
            )
            if resp.status_code == 404:
                return None
            if resp.status_code >= 400:
                logger.warning(
                    "apple phased release HTTP {}: {}",
                    resp.status_code,
                    resp.text[:200],
                )
                return None
            return extract_phased_attrs(resp.json())
        except Exception as exc:  # noqa: BLE001
            logger.warning("apple phased release fetch failed: {}", exc)
            return None

    def list_versions(self, app_cfg: dict, limit: int = 10) -> list[dict]:
        """列出 ASC 中该 App 的版本（供防呆与排查使用）。"""
        ios = app_cfg.get("ios") or {}
        app_store_app_id = ios.get("app_store_app_id")
        if not app_store_app_id:
            return []
        with self._client() as client:
            resp = client.get(
                f"{ASC_BASE}/v1/apps/{app_store_app_id}/appStoreVersions",
                params={
                    "limit": limit,
                    "fields[appStoreVersions]": (
                        "versionString,appStoreState,appVersionState,createdDate"
                    ),
                },
                headers=self._headers(app_cfg),
            )
            if resp.status_code >= 400:
                return []
            out = []
            for v in resp.json().get("data") or []:
                a = v.get("attributes") or {}
                raw_state = pick_version_state(a)
                out.append(
                    {
                        "version_string": a.get("versionString"),
                        "state": raw_state,
                        "state_label": describe_version_state(raw_state),
                        "mapped": map_version_state(raw_state).value,
                        "created_date": a.get("createdDate"),
                    }
                )
            out.sort(key=lambda x: x.get("created_date") or "", reverse=True)
            return out

    def list_builds(self, app_cfg: dict, limit: int = 20) -> list[dict]:
        """列出该 App 已有的构建（上传前查重使用）。"""
        ios = app_cfg.get("ios") or {}
        app_store_app_id = ios.get("app_store_app_id")
        if not app_store_app_id:
            return []
        with self._client() as client:
            resp = client.get(
                f"{ASC_BASE}/v1/builds",
                params={
                    "filter[app]": app_store_app_id,
                    "limit": limit,
                    "sort": "-uploadedDate",
                    "fields[builds]": (
                        "version,processingState,expired,uploadedDate,"
                        "usesNonExemptEncryption"
                    ),
                },
                headers=self._headers(app_cfg),
            )
            if resp.status_code >= 400:
                return []
            out = []
            for b in resp.json().get("data") or []:
                a = b.get("attributes") or {}
                out.append(
                    {
                        "build_id": b.get("id"),
                        "version": a.get("version"),
                        "processing_state": a.get("processingState"),
                        "expired": a.get("expired"),
                        "uploaded_date": a.get("uploadedDate"),
                    }
                )
            return out

    def resolve_version_for_whats_new(
        self,
        app_cfg: dict,
        *,
        version_name: str | None = None,
    ) -> dict[str, Any]:
        """解析要对哪个 appStoreVersion 补全 what's New（只读）。

        优先 ``version_name``；否则取最新一条仍可编辑元数据的版本。
        """
        from app.stores.apple_whats_new import EDITABLE_VERSION_STATES

        ios = app_cfg.get("ios") or {}
        app_store_app_id = ios.get("app_store_app_id")
        if not app_store_app_id:
            return {"ok": False, "message": "apps.yaml 缺少 ios.app_store_app_id"}

        params: dict[str, Any] = {
            "limit": 20,
            "filter[platform]": "IOS",
            "fields[appStoreVersions]": (
                "versionString,appStoreState,appVersionState,createdDate,platform"
            ),
        }
        if version_name:
            params["filter[versionString]"] = version_name

        with self._client() as client:
            resp = client.get(
                f"{ASC_BASE}/v1/apps/{app_store_app_id}/appStoreVersions",
                params=params,
                headers=self._headers(app_cfg),
            )
        if resp.status_code >= 400:
            return {
                "ok": False,
                "message": f"列出版本失败 HTTP {resp.status_code}",
                "raw": resp.text[:400],
            }

        versions = []
        for v in resp.json().get("data") or []:
            attrs = v.get("attributes") or {}
            raw_state = pick_version_state(attrs)
            versions.append(
                {
                    "id": v.get("id"),
                    "version_string": attrs.get("versionString"),
                    "state": raw_state,
                    "state_label": describe_version_state(raw_state),
                    "created_date": attrs.get("createdDate"),
                    "editable": (raw_state or "").upper() in EDITABLE_VERSION_STATES,
                }
            )
        versions.sort(key=lambda x: x.get("created_date") or "", reverse=True)

        if not versions:
            hint = f" versionString={version_name}" if version_name else ""
            return {"ok": False, "message": f"未找到 iOS 版本{hint}", "versions": []}

        if version_name:
            chosen = versions[0]
        else:
            chosen = next((x for x in versions if x.get("editable")), versions[0])

        return {
            "ok": True,
            "app_store_app_id": app_store_app_id,
            "version": chosen,
            "versions": versions,
        }

    def list_version_localizations(
        self,
        app_cfg: dict,
        version_id: str,
    ) -> dict[str, Any]:
        """列出该版本**已存在**的本地化（不创建任何语言）。"""
        from app.stores.apple_whats_new import parse_localization_rows

        with self._client() as client:
            resp = client.get(
                f"{ASC_BASE}/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations",
                params={
                    "limit": 200,
                    "fields[appStoreVersionLocalizations]": "locale,whatsNew",
                },
                headers=self._headers(app_cfg),
            )
        if resp.status_code >= 400:
            return {
                "ok": False,
                "message": f"列出本地化失败 HTTP {resp.status_code}",
                "raw": resp.text[:400],
            }
        payload = resp.json()
        rows = parse_localization_rows(payload)
        return {
            "ok": True,
            "localizations": [
                {"id": r.id, "locale": r.locale, "whatsNew": r.whats_new} for r in rows
            ],
            "count": len(rows),
        }

    def apply_whats_new_patches(
        self,
        app_cfg: dict,
        patches: list[dict[str, str]],
    ) -> dict[str, Any]:
        """对已有 localization id 写入 what's New（不新建语言）。

        ``patches``: [{id, locale, whatsNew}, ...]
        """
        results: list[dict[str, Any]] = []
        with self._client() as client:
            for item in patches:
                loc_id = item["id"]
                text = item["whatsNew"]
                locale = item.get("locale") or ""
                resp = client.patch(
                    f"{ASC_BASE}/v1/appStoreVersionLocalizations/{loc_id}",
                    headers=self._headers(app_cfg),
                    json={
                        "data": {
                            "type": "appStoreVersionLocalizations",
                            "id": loc_id,
                            "attributes": {"whatsNew": text},
                        }
                    },
                )
                ok = resp.status_code < 400
                results.append(
                    {
                        "id": loc_id,
                        "locale": locale,
                        "ok": ok,
                        "status_code": resp.status_code,
                        "error": None if ok else resp.text[:300],
                    }
                )
                if ok:
                    logger.info("apple whatsNew patched locale={} id={}", locale, loc_id)
                else:
                    logger.warning(
                        "apple whatsNew patch failed locale={} HTTP {} {}",
                        locale,
                        resp.status_code,
                        resp.text[:200],
                    )
        failed = [r for r in results if not r["ok"]]
        return {
            "ok": not failed,
            "patched": len(results) - len(failed),
            "failed": len(failed),
            "results": results,
        }
