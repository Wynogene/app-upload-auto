from __future__ import annotations

from loguru import logger

from app.core.service import AppReleaseService
from app.models import Platform, StatusRequest, UploadRequest
from app.notify.feishu_notify import Notifier

ACTION_UPLOAD_SUBMIT = "app_upload_submit"
ACTION_STATUS = "app_status"


def _platforms(raw: str | None) -> list[Platform]:
    value = (raw or "both").lower()
    if value in {"ios", "iphone", "apple"}:
        return [Platform.IOS]
    if value in {"android", "google"}:
        return [Platform.ANDROID]
    return [Platform.IOS, Platform.ANDROID]


def handle_card_action(value: dict, operator_open_id: str | None = None) -> dict:
    """Handle Feishu card.action.trigger button value.

    Returns toast payload for Feishu response.
    """
    action_type = value.get("type")
    app_id = value.get("app_id")
    if not app_id:
        return _toast("error", "缺少 app_id")

    service = AppReleaseService()
    notifier = Notifier()

    try:
        if action_type == ACTION_UPLOAD_SUBMIT:
            results = []
            for platform in _platforms(value.get("platform")):
                req = UploadRequest(
                    app_id=app_id,
                    platform=platform,
                    artifact_path=value.get("artifact_path"),
                    artifact_url=value.get("artifact_url"),
                    whats_new=value.get("whats_new"),
                    operator_open_id=operator_open_id,
                )
                results.extend(service.upload_and_submit(req))
            notifier.notify_operation_results(app_id, results)
            ok = all(r.ok for r in results)
            return _toast("success" if ok else "error", "上传并提审已触发，详情见群消息")

        if action_type == ACTION_STATUS:
            statuses = []
            for platform in _platforms(value.get("platform")):
                statuses.extend(
                    service.status(
                        StatusRequest(app_id=app_id, platform=platform)
                    )
                )
            notifier.notify_review_statuses(statuses, title="审核状态查询")
            return _toast("info", "状态已查询并推送到群")

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
