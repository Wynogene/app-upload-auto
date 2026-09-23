from __future__ import annotations

from typing import Any, Literal

from loguru import logger

from app.config import get_settings, resolve_notify_target, resolve_notify_targets
from app.feishu.client import FeishuClient, action_buttons_for_app
from app.models import OperationResult, ReviewStatus

NotifyAudience = Literal["ops", "debug"]


def _feishu_send_ok(data: dict[str, Any] | None) -> bool:
    if not data:
        return False
    code = data.get("code")
    return code in (0, "0", None)


class Notifier:
    def __init__(self, client: FeishuClient | None = None) -> None:
        self.client = client or FeishuClient()

    def _target(
        self, app_id: str | None = None, *, audience: NotifyAudience = "ops"
    ) -> tuple[str, str]:
        return resolve_notify_target(app_id, audience=audience)

    def _targets(
        self, app_id: str | None = None, *, audience: NotifyAudience = "ops"
    ) -> list[tuple[str, str]]:
        return resolve_notify_targets(app_id, audience=audience)

    def _buttons(self, app_id: str) -> list[dict] | None:
        if get_settings().feishu_enable_card_callbacks:
            return action_buttons_for_app(app_id)
        return None

    def _alert_owner_delivery_issues(
        self,
        *,
        original_title: str,
        failed: list[tuple[str, str, dict[str, Any]]],
        succeeded: list[tuple[str, str]],
    ) -> None:
        """私聊 OWNER：正式通知有人没送到。不再递归告警。"""
        lines = [
            f"原通知标题：`{original_title}`",
            f"成功 {len(succeeded)} / 失败 {len(failed)}",
            "",
            "**失败收件人：**",
        ]
        for id_type, receive_id, data in failed:
            lines.append(
                f"- `{id_type}` `{receive_id}` → code=`{data.get('code')}` "
                f"{data.get('msg') or ''}"
            )
        if succeeded:
            lines.append("")
            lines.append("**已成功：**")
            for id_type, receive_id in succeeded:
                lines.append(f"- `{id_type}` `{receive_id}`")
        lines.append("")
        lines.append(
            "常见原因：应用可用范围未包含该用户，或对方尚未与机器人建立会话"
            "（飞书 `230013 Bot has NO availability`）。"
        )
        try:
            self._send_interactive_all(
                app_id=None,
                title="[个人调试] 飞书通知部分失败",
                markdown="\n".join(lines),
                buttons=None,
                template="red",
                audience="debug",
                alert_owner_on_failure=False,
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to alert OWNER about partial Feishu delivery")

    def _send_interactive_all(
        self,
        *,
        app_id: str | None,
        title: str,
        markdown: str,
        buttons: list[dict] | None = None,
        template: str = "blue",
        force_callbacks: bool = False,
        audience: NotifyAudience = "ops",
        alert_owner_on_failure: bool = True,
    ) -> dict[str, Any]:
        """Fan-out interactive card to ops/debug user_id list (or chat when not personal-only).

        正式名单部分失败时私聊 OWNER；若全部失败则抛错（便于盯盘不落指纹、可重试）。
        """
        last: dict[str, Any] = {}
        succeeded: list[tuple[str, str]] = []
        failed: list[tuple[str, str, dict[str, Any]]] = []

        for id_type, receive_id in self._targets(app_id, audience=audience):
            data = self.client.send_interactive(
                receive_id=receive_id,
                receive_id_type=id_type,
                title=title,
                markdown=markdown,
                buttons=buttons,
                template=template,
                force_callbacks=force_callbacks,
            )
            last = data
            if _feishu_send_ok(data):
                succeeded.append((id_type, receive_id))
            else:
                failed.append((id_type, receive_id, data))

        if failed and alert_owner_on_failure and audience == "ops":
            self._alert_owner_delivery_issues(
                original_title=title,
                failed=failed,
                succeeded=succeeded,
            )

        if failed and not succeeded:
            detail = "; ".join(
                f"{rid}:{d.get('code')}:{d.get('msg')}" for _, rid, d in failed
            )
            raise RuntimeError(f"飞书通知全部失败: {detail}")

        return last

    def send_app_panel(self, app_id: str, note: str = "") -> dict:
        """调试面板：仅本人。"""
        markdown = f"**App:** `{app_id}`\n{note}".strip()
        return self._send_interactive_all(
            app_id=app_id,
            title=f"[个人调试] APP 发布助手 · {app_id}",
            markdown=markdown,
            buttons=self._buttons(app_id),
            template="blue",
            audience="debug",
        )

    def notify_operation_results(self, app_id: str, results: list[OperationResult]) -> dict:
        """传包 / 提审等操作结果：正式名单（成功短字段，不加长 Console HINT）。"""
        from app.core.notify_copy import format_operation_results_ops

        markdown = format_operation_results_ops(results)
        return self._send_interactive_all(
            app_id=app_id,
            title=f"发版助手 · 操作结果 · {app_id}",
            markdown=markdown or "无结果",
            buttons=self._buttons(app_id),
            template="green" if all(r.ok for r in results) else "red",
            audience="ops",
        )

    def notify_review_statuses(
        self,
        statuses: list[ReviewStatus],
        title: str = "审核状态更新",
        footer: str | None = None,
        *,
        ops_copy: bool = True,
        title_prefix: str | None = None,
    ) -> dict | None:
        """盯盘 / 审核状态：正式名单。

        ops_copy=True（默认）：运营向摘要，不堆 API 原文，也不再重复长 footer。
        footer 仅在 ops_copy=False 时作为附加段；ops 模式下忽略传入的长 CONSOLE_HINT，
        避免与 message 内提示重复。
        """
        if not statuses:
            return None
        app_id = statuses[0].app_id if statuses else None

        if ops_copy:
            from app.core.notify_copy import format_review_statuses_ops

            markdown = format_review_statuses_ops(statuses)
        else:
            from app.core.watch_targets import console_hints_for

            lines = []
            for s in statuses:
                lines.append(
                    f"- **{s.app_id}** / **{s.platform.value}** "
                    f"`{s.version_name or '-'}` → `{s.state.value}`\n  {s.message}"
                )
            hint = (
                footer
                if footer is not None
                else console_hints_for([s.platform.value for s in statuses])
            )
            if hint:
                lines.append(f"\n_{hint}_")
            markdown = "\n".join(lines)

        if title_prefix is None:
            title_prefix = "发版助手 · "
        full_title = f"{title_prefix}{title}" if title_prefix else title

        return self._send_interactive_all(
            app_id=app_id,
            title=full_title,
            markdown=markdown,
            buttons=None,
            template="orange",
            audience="ops",
        )

    def notify_owner(self, *, title: str, markdown: str, template: str = "orange") -> dict:
        """调试/运维告警：仅本人。不写商店。"""
        return self._send_interactive_all(
            app_id=None,
            title=f"[个人调试] {title}",
            markdown=markdown,
            buttons=None,
            template=template,
            audience="debug",
        )

    def send_submit_debug_card(
        self,
        value: dict,
        *,
        note: str = "",
        with_callbacks: bool = False,
    ) -> dict:
        """私聊本人一张提审调试说明卡。默认无按钮；--with-callbacks 才附带可点按钮。"""
        from app.feishu.client import submit_debug_buttons

        settings = get_settings()
        if not settings.safety_personal_only:
            raise PermissionError(
                "submit-card 仅在 SAFETY_PERSONAL_ONLY=true 下可用，避免误发业务群"
            )

        app_id = value.get("app_id", "?")
        lines = [
            "**安全调试卡（仅你可见）**",
            f"- app_id: `{app_id}`",
            f"- platform: `{value.get('platform')}`",
            f"- track: `{value.get('track', 'internal')}`（默认 internal）",
            f"- allow_production: `{bool(value.get('allow_production'))}`",
        ]
        if value.get("artifact_path"):
            lines.append(f"- artifact_path: `{value.get('artifact_path')}`")
        if value.get("artifact_url"):
            lines.append(f"- artifact_url: `{value.get('artifact_url')}`")
        if value.get("whats_new"):
            lines.append(f"- whats_new: {value.get('whats_new')}")
        if value.get("version"):
            lines.append(f"- version: `{value.get('version')}`")
        if value.get("preview_only"):
            lines.append("- **preview_only: true（点提审也不会写商店）**")
        lines.append("")
        lines.append(
            "本机执行（推荐，不依赖飞书回调）:\n"
            "`python cli.py card-run`（参数与本卡一致；可加 `--dry-resolve` 只选型不传商店）。"
        )
        if note:
            lines.append(f"\n{note}")

        buttons = submit_debug_buttons(value) if with_callbacks else None
        return self._send_interactive_all(
            app_id=app_id if isinstance(app_id, str) else None,
            title=f"[个人调试] 提审卡 · {app_id}",
            markdown="\n".join(lines),
            buttons=buttons,
            template="blue",
            force_callbacks=bool(with_callbacks and buttons),
            audience="debug",
        )
