from __future__ import annotations

from typing import Any, Literal

from app.config import get_settings, resolve_notify_target, resolve_notify_targets
from app.feishu.client import FeishuClient, action_buttons_for_app
from app.models import OperationResult, ReviewStatus

NotifyAudience = Literal["ops", "debug"]


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
    ) -> dict[str, Any]:
        """Fan-out interactive card to ops/debug user_id list (or chat when not personal-only)."""
        last: dict[str, Any] = {}
        for id_type, receive_id in self._targets(app_id, audience=audience):
            last = self.client.send_interactive(
                receive_id=receive_id,
                receive_id_type=id_type,
                title=title,
                markdown=markdown,
                buttons=buttons,
                template=template,
                force_callbacks=force_callbacks,
            )
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
        """传包 / 提审等操作结果：正式名单。"""
        from app.core.watch_targets import console_hints_for

        lines = []
        for r in results:
            mark = "✅" if r.ok else "❌"
            plat = r.platform.value if r.platform else "-"
            lines.append(f"{mark} **{plat}**: {r.message}")
        if any(r.ok for r in results):
            plats = [
                r.platform.value for r in results if r.ok and r.platform is not None
            ]
            hint = console_hints_for(plats)
            lines.append(f"\n_{hint}_")
        return self._send_interactive_all(
            app_id=app_id,
            title=f"发版助手 · 操作结果 · {app_id}",
            markdown="\n".join(lines) or "无结果",
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
