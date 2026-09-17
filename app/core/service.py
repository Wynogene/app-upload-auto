from __future__ import annotations

from loguru import logger

from app.config import get_app_by_id
from app.core.watch_targets import (
    ANDROID_HINT,
    upsert_target,
    watch_hint_command,
)
from app.models import (
    OperationResult,
    Platform,
    ReviewStatus,
    StatusRequest,
    SubmitRequest,
    UploadRequest,
)
from app.stores import AppleStoreClient, GoogleStoreClient


def _maybe_register_watch(result: OperationResult, req_track: str | None = None) -> str | None:
    """On successful production Android submit/upload, persist watch target + return CLI hint."""
    if not result.ok or result.platform != Platform.ANDROID:
        return None
    details = result.details or {}
    track = str(details.get("track") or req_track or "").lower()
    if track not in {"production", "prod"}:
        return None
    version_code = details.get("version_code")
    if version_code is None:
        return None
    vc = str(version_code)
    app_id = result.app_id or ""
    upsert_target(
        app_id=app_id,
        platform="android",
        version_code=vc,
        track="production",
        heartbeat_hours=12.0,
        note="auto after production submit",
    )
    hint = watch_hint_command(app_id, vc, "android")
    logger.info("watch target registered app={} versionCode={}", app_id, vc)
    return (
        f"已登记盯盘目标 versionCode={vc}（同 App 更旧的正式轨 Android 盯盘会自动停掉）。"
        f"建议立刻执行:\n  {hint}\n"
        f"（{ANDROID_HINT}）"
    )


class AppReleaseService:
    def __init__(self) -> None:
        self.apple = AppleStoreClient()
        self.google = GoogleStoreClient()

    def _app(self, app_id: str) -> dict:
        app = get_app_by_id(app_id)
        if not app:
            raise ValueError(f"未知 app_id: {app_id}，请检查 config/apps.yaml")
        return app

    def _ensure_allowed(self, app: dict, operator_open_id: str | None) -> None:
        allowed = app.get("allowed_open_ids") or []
        if not allowed:
            return
        if not operator_open_id or operator_open_id not in allowed:
            raise PermissionError("当前用户无权操作该 App")

    def upload(self, req: UploadRequest) -> OperationResult:
        app = self._app(req.app_id)
        self._ensure_allowed(app, req.operator_open_id)
        if req.platform == Platform.IOS:
            if not (app.get("ios") or {}).get("enabled", True):
                return OperationResult(ok=False, message="该 App 未启用 iOS", app_id=req.app_id)
            return self.apple.upload(req, app)
        if not (app.get("android") or {}).get("enabled", True):
            return OperationResult(ok=False, message="该 App 未启用 Android", app_id=req.app_id)
        result = self.google.upload(req, app)
        hint = _maybe_register_watch(result, req.track)
        if hint:
            result.message = f"{result.message}\n{hint}"
        return result

    def submit(self, req: SubmitRequest) -> OperationResult:
        """Android: promote versionCode to track. iOS: App Store review submit."""
        app = self._app(req.app_id)
        self._ensure_allowed(app, req.operator_open_id)
        if req.platform == Platform.IOS:
            return self.apple.submit(req, app)
        result = self.google.submit(req, app)
        hint = _maybe_register_watch(result, req.track)
        if hint:
            result.message = f"{result.message}\n{hint}"
        return result

    def upload_and_submit(self, req: UploadRequest) -> list[OperationResult]:
        """
        Android: upload already assigns track+commit，一次完成「上传并发布到轨道」.
        iOS: upload then submit (submit still stub until ASC wired).
        """
        upload_result = self.upload(req)
        if not upload_result.ok:
            return [upload_result]
        if req.platform == Platform.ANDROID:
            version_code = (upload_result.details or {}).get("version_code")
            track = (upload_result.details or {}).get("track") or req.track or "internal"
            follow = OperationResult(
                ok=True,
                app_id=req.app_id,
                platform=Platform.ANDROID,
                review_state=upload_result.review_state,
                message=(
                    f"Android 已在 upload 阶段发布到 `{track}`"
                    + (f"（versionCode={version_code}）" if version_code else "")
                    + "。若要推进到其它轨道（含正式版），请用 release 命令。"
                ),
                details=upload_result.details,
            )
            return [upload_result, follow]

        submit_req = SubmitRequest(
            app_id=req.app_id,
            platform=req.platform,
            version_name=req.version_name,
            whats_new=req.whats_new,
            release_notes=req.release_notes,
            allow_production=req.allow_production,
            operator_open_id=req.operator_open_id,
        )
        return [upload_result, self.submit(submit_req)]

    def status(self, req: StatusRequest) -> list[ReviewStatus]:
        app = self._app(req.app_id)
        platforms = (
            [req.platform]
            if req.platform
            else [Platform.IOS, Platform.ANDROID]
        )
        results: list[ReviewStatus] = []
        for platform in platforms:
            if platform == Platform.IOS and (app.get("ios") or {}).get("enabled", True):
                results.append(self.apple.status(app, req.version_name))
            elif platform == Platform.ANDROID and (app.get("android") or {}).get("enabled", True):
                prefer = "production" if req.version_code else None
                results.append(
                    self.google.status(
                        app,
                        version_name=req.version_name,
                        version_code=req.version_code,
                        prefer_track=prefer,
                    )
                )
        return results

    def status_all_apps(self) -> list[ReviewStatus]:
        from app.config import load_apps_config

        out: list[ReviewStatus] = []
        for app in load_apps_config().get("apps") or []:
            app_id = app.get("id")
            if not app_id:
                continue
            try:
                out.extend(self.status(StatusRequest(app_id=app_id)))
            except Exception as exc:  # noqa: BLE001
                logger.exception("status_all failed for {}", app_id)
                out.append(
                    ReviewStatus(
                        app_id=app_id,
                        platform=Platform.IOS,
                        message=f"查询失败: {exc}",
                    )
                )
        return out

    def status_watch_targets(self) -> list[tuple[str, ReviewStatus]]:
        """Poll active persisted watch targets; returns (target_key, status)."""
        from app.core.watch_targets import active_targets

        out: list[tuple[str, ReviewStatus]] = []
        for t in active_targets():
            try:
                statuses = self.status(
                    StatusRequest(
                        app_id=t.app_id,
                        platform=Platform(t.platform) if t.platform else Platform.ANDROID,
                        version_code=t.version_code,
                    )
                )
                for s in statuses:
                    out.append((t.key, s))
            except Exception as exc:  # noqa: BLE001
                logger.exception("watch target status failed {}", t.key)
                out.append(
                    (
                        t.key,
                        ReviewStatus(
                            app_id=t.app_id,
                            platform=Platform.ANDROID,
                            message=f"盯盘查询失败: {exc}",
                        ),
                    )
                )
        return out
