"""飞书粉发部分失败时告警 OWNER（离线 mock）。"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.notify.feishu_notify import Notifier, _feishu_send_ok


class _FakeClient:
    def __init__(self, results: dict[str, dict[str, Any]]) -> None:
        self.results = results
        self.calls: list[tuple[str, str, str]] = []

    def send_interactive(
        self,
        receive_id: str,
        title: str,
        markdown: str,
        buttons=None,
        template: str = "blue",
        receive_id_type: str | None = None,
        *,
        force_callbacks: bool = False,
    ) -> dict[str, Any]:
        self.calls.append((receive_id_type or "", receive_id, title))
        return dict(self.results.get(receive_id, {"code": 0, "msg": "success"}))


def _clear() -> None:
    get_settings.cache_clear()


def test_feishu_send_ok_helper() -> None:
    assert _feishu_send_ok({"code": 0})
    assert _feishu_send_ok({"code": "0"})
    assert not _feishu_send_ok({"code": 230013, "msg": "no"})


def test_partial_ops_failure_alerts_owner(monkeypatch) -> None:
    monkeypatch.setenv("SAFETY_PERSONAL_ONLY", "true")
    monkeypatch.setenv("FEISHU_OWNER_USER_ID", "56798dag")
    monkeypatch.setenv("FEISHU_NOTIFY_USER_IDS", "c59ce84g,56798dag")
    _clear()
    try:
        client = _FakeClient(
            {
                "c59ce84g": {"code": 230013, "msg": "Bot has NO availability to this user."},
                "56798dag": {"code": 0, "msg": "success"},
            }
        )
        n = Notifier(client=client)  # type: ignore[arg-type]
        out = n._send_interactive_all(
            app_id=None,
            title="发版助手 · 进入审核",
            markdown="test",
            audience="ops",
        )
        assert out.get("code") == 0
        titles = [c[2] for c in client.calls]
        assert titles.count("发版助手 · 进入审核") == 2
        assert any("部分失败" in t for t in titles)
        # owner alert is debug audience → only 56798dag
        alert_calls = [c for c in client.calls if "部分失败" in c[2]]
        assert alert_calls == [("user_id", "56798dag", "[个人调试] 飞书通知部分失败")]
    finally:
        _clear()


def test_all_ops_failure_raises(monkeypatch) -> None:
    monkeypatch.setenv("SAFETY_PERSONAL_ONLY", "true")
    monkeypatch.setenv("FEISHU_OWNER_USER_ID", "56798dag")
    monkeypatch.setenv("FEISHU_NOTIFY_USER_IDS", "c59ce84g,56798dag")
    _clear()
    try:
        client = _FakeClient(
            {
                "c59ce84g": {"code": 230013, "msg": "no"},
                "56798dag": {"code": 230013, "msg": "no"},
            }
        )
        n = Notifier(client=client)  # type: ignore[arg-type]
        try:
            n._send_interactive_all(
                app_id=None,
                title="发版助手 · 进入审核",
                markdown="test",
                audience="ops",
            )
            assert False, "expected RuntimeError"
        except RuntimeError as exc:
            assert "全部失败" in str(exc)
        assert any("部分失败" in c[2] for c in client.calls)
    finally:
        _clear()
