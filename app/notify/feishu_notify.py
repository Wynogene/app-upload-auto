from __future__ import annotations

from app.config import get_settings, resolve_notify_target
from app.feishu.client import FeishuClient, action_buttons_for_app
from app.models import OperationResult, ReviewStatus


class Notifier:
    def __init__(self, client: FeishuClient | None = None) -> None:
        self.client = client or FeishuClient()

    def _target(self, app_id: str | None = None) -> tuple[str, str]:
        return resolve_notify_target(app_id)

    def _buttons(self, app_id: str) -> list[dict] | None:
        if get_settings().feishu_enable_card_callbacks:
            return action_buttons_for_app(app_id)
        return None

    def send_app_panel(self, app_id: str, note: str = "") -> dict:
        id_type, receive_id = self._target(app_id)
        markdown = f"**App:** `{app_id}`\n{note}".strip()
        return self.client.send_interactive(
            receive_id=receive_id,
            receive_id_type=id_type,
            title=f"[个人调试] APP 发布助手 · {app_id}",
            markdown=markdown,
            buttons=self._buttons(app_id),
            template="blue",
        )

    def notify_operation_results(self, app_id: str, results: list[OperationResult]) -> dict:
        from app.core.watch_targets import console_hints_for

        id_type, receive_id = self._target(app_id)
        lines = []
        for r in results:
            mark = "✅" if r.ok else "❌"
            plat = r.platform.value if r.platform else "-"
            lines.append(f"{mark} **{plat}**: {r.message}")
        # 提审/上传结果附带对应商店控制台提示
        if any(r.ok for r in results):
            plats = [
                r.platform.value for r in results if r.ok and r.platform is not None
            ]
            hint = console_hints_for(plats)
            lines.append(f"\n_{hint}_")
        return self.client.send_interactive(
            receive_id=receive_id,
            receive_id_type=id_type,
            title=f"[个人调试] 操作结果 · {app_id}",
            markdown="\n".join(lines) or "无结果",
            buttons=self._buttons(app_id),
            template="green" if all(r.ok for r in results) else "red",
        )

    def notify_review_statuses(
        self,
        statuses: list[ReviewStatus],
        title: str = "审核状态更新",
        footer: str | None = None,
    ) -> dict | None:
        from app.core.watch_targets import console_hints_for

        if not statuses:
            return None
        # personal-only：合并成一条，只发给本人，避免按 app 误路由到群
        id_type, receive_id = self._target(statuses[0].app_id if statuses else None)
        lines = []
        for s in statuses:
            lines.append(
                f"- **{s.app_id}** / **{s.platform.value}** `{s.version_name or '-'}` → `{s.state.value}`\n  {s.message}"
            )
        if footer is not None:
            hint = footer
        else:
            hint = console_hints_for([s.platform.value for s in statuses])
        if hint:
            lines.append(f"\n_{hint}_")
        return self.client.send_interactive(
            receive_id=receive_id,
            receive_id_type=id_type,
            title=f"[个人调试] {title}",
            markdown="\n".join(lines),
            buttons=None,
            template="orange",
        )
