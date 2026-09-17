from __future__ import annotations

import json
import time
from typing import Any

import httpx
from loguru import logger

from app.config import get_settings, personal_allowed_user_ids

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


class FeishuClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._expire_at: float = 0

    def _settings(self):
        return get_settings()

    def _http(self) -> httpx.Client:
        """飞书默认直连（trust_env=False），避免被系统 HTTP_PROXY=127.0.0.1:7892 拖死。

        需要走代理时设 FEISHU_USE_PROXY=true。
        """
        settings = self._settings()
        return httpx.Client(timeout=20, trust_env=bool(settings.feishu_use_proxy))

    def get_tenant_access_token(self) -> str:
        settings = self._settings()
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            raise RuntimeError("未配置 FEISHU_APP_ID / FEISHU_APP_SECRET")
        if self._token and time.time() < self._expire_at - 60:
            return self._token

        with self._http() as client:
            resp = client.post(
                TOKEN_URL,
                json={
                    "app_id": settings.feishu_app_id,
                    "app_secret": settings.feishu_app_secret,
                },
            )
            data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 token 失败: {data}")
        self._token = data["tenant_access_token"]
        self._expire_at = time.time() + int(data.get("expire", 7200))
        return self._token

    def _assert_safe_target(self, receive_id_type: str, receive_id: str) -> None:
        settings = self._settings()
        if not settings.safety_personal_only:
            return
        if receive_id.startswith("oc_") or receive_id_type == "chat_id":
            raise PermissionError("SAFETY_PERSONAL_ONLY：禁止发送到群 chat_id")
        if receive_id_type != "user_id":
            raise PermissionError(
                "SAFETY_PERSONAL_ONLY：仅允许 receive_id_type=user_id 私聊"
            )

        allowed = personal_allowed_user_ids()
        if not allowed:
            raise PermissionError(
                "SAFETY_PERSONAL_ONLY：请配置 FEISHU_OWNER_USER_ID 或 FEISHU_NOTIFY_USER_IDS"
            )
        if receive_id not in allowed:
            raise PermissionError(
                "SAFETY_PERSONAL_ONLY：禁止发往非名单 user_id（群或其他用户）"
            )

    def send_interactive(
        self,
        receive_id: str,
        title: str,
        markdown: str,
        buttons: list[dict[str, Any]] | None = None,
        template: str = "blue",
        receive_id_type: str | None = None,
        *,
        force_callbacks: bool = False,
    ) -> dict[str, Any]:
        settings = self._settings()
        if receive_id_type:
            id_type = receive_id_type
        elif settings.safety_personal_only:
            id_type = "user_id"
        else:
            id_type = "chat_id"
        # personal-only 默认去掉 callback 按钮，避免点按进入现网 webhook
        use_buttons = buttons if settings.feishu_enable_card_callbacks else None
        if force_callbacks:
            if not settings.safety_personal_only:
                raise PermissionError("force_callbacks 仅允许在 SAFETY_PERSONAL_ONLY=true 时使用")
            if not settings.feishu_enable_card_callbacks:
                raise PermissionError(
                    "需要 FEISHU_ENABLE_CARD_CALLBACKS=true 才能附带可点按钮"
                )
            use_buttons = buttons
        elif buttons and not settings.feishu_enable_card_callbacks:
            markdown = (
                f"{markdown}\n\n"
                "> 当前为个人调试模式：未附带可回调按钮（避免影响现网）。"
                "请用本机 CLI `card-run` 触发同参数提审。"
            )
        card = self.build_card(title, markdown, buttons=use_buttons, template=template)
        return self.send_message(receive_id, msg_type="interactive", content=card, receive_id_type=id_type)

    def send_message(
        self,
        receive_id: str,
        msg_type: str,
        content: dict | str,
        receive_id_type: str = "open_id",
    ) -> dict[str, Any]:
        self._assert_safe_target(receive_id_type, receive_id)
        token = self.get_tenant_access_token()
        body = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
        }
        with self._http() as client:
            resp = client.post(
                f"{MESSAGE_URL}?receive_id_type={receive_id_type}",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            data = resp.json()
        logger.info(
            "feishu send_message type={} id={} => code={} msg={}",
            receive_id_type,
            receive_id[:12],
            data.get("code"),
            data.get("msg"),
        )
        return data

    @staticmethod
    def build_card(
        title: str,
        markdown: str,
        buttons: list[dict[str, Any]] | None = None,
        template: str = "blue",
    ) -> dict[str, Any]:
        elements: list[dict[str, Any]] = [
            {"tag": "markdown", "content": markdown, "text_align": "left"}
        ]
        if buttons:
            columns = []
            for button in buttons:
                btn: dict[str, Any] = {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": button.get("text", "按钮")},
                    "type": button.get("type", "primary"),
                    "complex_interaction": True,
                    "width": "fill",
                    "size": "medium",
                    "behaviors": [
                        {
                            "type": "callback",
                            "value": button.get("value") or {},
                        }
                    ],
                }
                if button.get("confirm"):
                    btn["confirm"] = {
                        "title": {"tag": "plain_text", "content": "请确认"},
                        "text": {"tag": "plain_text", "content": button["confirm"]},
                    }
                columns.append(
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "top",
                        "elements": [btn],
                    }
                )
            elements.append(
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "background_style": "default",
                    "horizontal_spacing": "8px",
                    "columns": columns,
                    "margin": "16px 0px 0px 0px",
                }
            )

        return {
            "config": {"update_multi": True},
            "i18n_header": {
                "zh_cn": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": template,
                }
            },
            "i18n_elements": {"zh_cn": elements},
        }


def action_buttons_for_app(app_id: str, platform: str | None = None) -> list[dict[str, Any]]:
    plat = platform or "both"
    return [
        {
            "text": "上传并提审",
            "type": "primary",
            "confirm": f"确认对 {app_id} ({plat}) 执行上传并提审？",
            "value": {"type": "app_upload_submit", "app_id": app_id, "platform": plat},
        },
        {
            "text": "仅查询状态",
            "type": "default",
            "value": {"type": "app_status", "app_id": app_id, "platform": plat},
        },
    ]


def submit_debug_buttons(value: dict[str, Any]) -> list[dict[str, Any]]:
    """Single safe submit button for personal debug cards (value already filled)."""
    app_id = value.get("app_id", "?")
    plat = value.get("platform", "?")
    track = value.get("track", "internal")
    return [
        {
            "text": "提审（调试）",
            "type": "primary",
            "confirm": (
                f"确认对 {app_id}/{plat} 提审到轨道 `{track}`？"
                "默认应为 internal，勿对正式轨误触。"
            ),
            "value": value,
        },
        {
            "text": "仅查状态",
            "type": "default",
            "value": {
                "type": "app_status",
                "app_id": app_id,
                "platform": plat,
            },
        },
    ]
