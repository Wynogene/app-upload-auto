"""Feishu card.action handlers for upload-submit / status (async-safe)."""

from __future__ import annotations

import threading
from typing import Any

from loguru import logger

from app.config import get_app_by_id, get_settings
from app.core.artifact_resolve import cleanup_work_dir, resolve_artifact
from app.core.service import AppReleaseService
from app.models import Platform, StatusRequest, UploadRequest
from app.notify.feishu_notify import Notifier

ACTION_UPLOAD_SUBMIT = "app_upload_submit"
ACTION_STATUS = "app_status"

# Card-driven submit defaults to internal track (safe). Production needs explicit flags.
_DEFAULT_CARD_TRACK = "internal"

_IOS_UPLOAD_NOT_READY = (
    "iOS 上传/提审尚未接通（Build Upload / reviewSubmissions）。"
    "当前请用 ipa-check / status；Android 可用提审调试。"
)


def _platforms(raw: str | None, *, require_single: bool = False) -> list[Platform] | str:
    """Return platforms or an error string when require_single and value is missing/both."""
    value = (raw or "").strip().lower()
    if require_single and value in {"", "both", "all"}:
        return "提审必须指定单一 platform：android 或 ios（禁止 both）"
    if value in {"ios", "iphone", "apple"}:
        return [Platform.IOS]
    if value in {"android", "google"}:
        return [Platform.ANDROID]
    if value in {"", "both", "all"}:
        return [Platform.IOS, Platform.ANDROID]
    return f"无法识别 platform: {raw!r}（请用 android / ios）"


def _truthy(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _package_hint(app: dict, platform: Platform) -> str | None:
    if platform == Platform.ANDROID:
        return (app.get("android") or {}).get("package_name")
    return (app.get("ios") or {}).get("bundle_id")


def _build_upload_request(
    value: dict,
    platform: Platform,
    *,
    artifact_path: str | None,
    operator_open_id: str | None,
) -> UploadRequest | str:
    """Build UploadRequest with safe track defaults. Returns error string on reject."""
    track = (value.get("track") or _DEFAULT_CARD_TRACK).strip().lower()
    allow_production = _truthy(value.get("allow_production"))
    if track in {"production", "prod"} and not allow_production:
        return (
            "卡片提审默认禁止正式轨。若确认，请在 value 中同时设置 "
            'track=production 与 allow_production=true（调试请用 track=internal）。'
        )
    if track in {"production", "prod"}:
        track = "production"
    elif not track:
        track = _DEFAULT_CARD_TRACK

    return UploadRequest(
        app_id=str(value["app_id"]),
        platform=platform,
        artifact_path=artifact_path,
        artifact_url=value.get("artifact_url"),
        whats_new=value.get("whats_new"),
        version_name=value.get("version") or value.get("version_name"),
        track=track if platform == Platform.ANDROID else None,
        allow_production=allow_production,
        operator_open_id=operator_open_id,
    )


def run_upload_submit_job(value: dict, operator_open_id: str | None = None) -> list:
    """Synchronous worker: resolve artifact → upload_and_submit → notify.

    Used by background thread and by CLI `card-run`.
    """
    app_id = value.get("app_id")
    if not app_id:
        raise ValueError("缺少 app_id")

    platforms = _platforms(value.get("platform"), require_single=True)
    if isinstance(platforms, str):
        raise ValueError(platforms)

    app = get_app_by_id(str(app_id))
    if not app:
        raise ValueError(f"未知 app_id: {app_id}")

    if _truthy(value.get("preview_only")):
        from app.models import OperationResult

        results = [
            OperationResult(
                ok=True,
                app_id=str(app_id),
                message="preview_only：已跳过，未下载/上传/写商店",
            )
        ]
        try:
            Notifier().notify_operation_results(str(app_id), results)
        except Exception:  # noqa: BLE001
            logger.exception("notify after preview_only skip failed")
        return results

    service = AppReleaseService()
    notifier = Notifier()
    all_results = []

    for platform in platforms:
        if platform == Platform.IOS:
            from app.models import OperationResult

            all_results.append(
                OperationResult(
                    ok=False,
                    app_id=str(app_id),
                    platform=Platform.IOS,
                    message=_IOS_UPLOAD_NOT_READY,
                )
            )
            continue

        work_dir = None
        try:
            resolved = resolve_artifact(
                platform=platform,
                artifact_path=value.get("artifact_path"),
                artifact_url=value.get("artifact_url"),
                package_or_bundle=_package_hint(app, platform),
                version_hint=value.get("version") or value.get("version_name"),
            )
            work_dir = resolved.work_dir
            if not resolved.ok or not resolved.path:
                from app.models import OperationResult

                all_results.append(
                    OperationResult(
                        ok=False,
                        app_id=str(app_id),
                        platform=platform,
                        message=resolved.message,
                        details={"candidates": resolved.candidates},
                    )
                )
                continue

            req_or_err = _build_upload_request(
                value,
                platform,
                artifact_path=str(resolved.path),
                operator_open_id=operator_open_id,
            )
            if isinstance(req_or_err, str):
                from app.models import OperationResult

                all_results.append(
                    OperationResult(
                        ok=False,
                        app_id=str(app_id),
                        platform=platform,
                        message=req_or_err,
                    )
                )
                continue

            logger.info(
                "card submit job app={} platform={} artifact={} track={}",
                app_id,
                platform.value,
                resolved.path,
                req_or_err.track,
            )
            all_results.extend(service.upload_and_submit(req_or_err))
        finally:
            cleanup_work_dir(work_dir)

    try:
        notifier.notify_operation_results(str(app_id), all_results)
    except Exception:  # noqa: BLE001
        logger.exception("notify_operation_results failed after card submit")
    return all_results


def handle_card_action(value: dict, operator_open_id: str | None = None) -> dict:
    """Handle Feishu card.action.trigger button value.

    Upload-submit is accepted asynchronously (toast immediately) to avoid Feishu timeouts.
    Returns toast payload for Feishu response.
    """
    action_type = value.get("type")
    app_id = value.get("app_id")
    if not app_id:
        return _toast("error", "缺少 app_id")

    settings = get_settings()
    if settings.safety_personal_only:
        owner = settings.feishu_owner_open_id
        if owner and operator_open_id and operator_open_id != owner:
            return _toast("info", "个人调试模式：仅所有者可操作")

    try:
        if action_type == ACTION_UPLOAD_SUBMIT:
            platforms = _platforms(value.get("platform"), require_single=True)
            if isinstance(platforms, str):
                return _toast("error", platforms)
            if platforms == [Platform.IOS]:
                return _toast("error", _IOS_UPLOAD_NOT_READY)
            if _truthy(value.get("preview_only")):
                return _toast(
                    "info",
                    "此卡为按钮预览：不会下载、不会上传、不会改商店",
                )
            if not value.get("artifact_path") and not value.get("artifact_url"):
                return _toast("error", "缺少 artifact_path 或 artifact_url")

            # Pre-validate track safety before queueing
            track = (value.get("track") or _DEFAULT_CARD_TRACK).strip().lower()
            if track in {"production", "prod"} and not _truthy(value.get("allow_production")):
                return _toast(
                    "error",
                    "已拒绝：卡片提审默认仅 internal。正式轨需 allow_production=true",
                )

            def _job() -> None:
                try:
                    run_upload_submit_job(value, operator_open_id)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("upload submit job failed")
                    try:
                        Notifier().notify_owner(
                            title=f"提审失败 · {app_id}",
                            markdown=f"后台任务异常：`{exc}`",
                            template="red",
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("notify_owner after job failure also failed")

            threading.Thread(
                target=_job,
                name=f"card-submit-{app_id}",
                daemon=True,
            ).start()
            track_show = (value.get("track") or _DEFAULT_CARD_TRACK).strip() or _DEFAULT_CARD_TRACK
            return _toast(
                "info",
                f"已受理提审（轨道 {track_show}），完成后私聊通知你",
            )

        if action_type == ACTION_STATUS:
            platforms = _platforms(value.get("platform"), require_single=False)
            if isinstance(platforms, str):
                return _toast("error", platforms)
            service = AppReleaseService()
            notifier = Notifier()
            statuses = []
            for platform in platforms:
                statuses.extend(
                    service.status(StatusRequest(app_id=app_id, platform=platform))
                )
            notifier.notify_review_statuses(statuses, title="审核状态查询")
            return _toast("info", "状态已查询并推送到私聊/通知目标")

        return _toast("error", f"未知操作: {action_type}")
    except PermissionError as exc:
        return _toast("info", str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("handle_card_action failed")
        return _toast("error", f"执行失败: {exc}")


def _toast(toast_type: str, content: str) -> dict:
    return {
        "toast": {
            "type": toast_type,
            "content": content,
            "i18n": {"zh_cn": content},
        }
    }


def build_submit_card_value(
    *,
    app_id: str,
    platform: str,
    artifact_path: str | None = None,
    artifact_url: str | None = None,
    whats_new: str | None = None,
    version: str | None = None,
    track: str = _DEFAULT_CARD_TRACK,
    allow_production: bool = False,
    preview_only: bool = False,
) -> dict[str, Any]:
    """Build contract-compatible button value for debug cards / CLI."""
    value: dict[str, Any] = {
        "type": ACTION_UPLOAD_SUBMIT,
        "app_id": app_id,
        "platform": platform.lower(),
        "track": track or _DEFAULT_CARD_TRACK,
    }
    if artifact_path:
        value["artifact_path"] = artifact_path
    if artifact_url:
        value["artifact_url"] = artifact_url
    if whats_new:
        value["whats_new"] = whats_new
    if version:
        value["version"] = version
    if allow_production:
        value["allow_production"] = True
    if preview_only:
        value["preview_only"] = True
    return value
