"""personal-only 通知名单：正式 ops vs 调试 debug（离线）。"""

from __future__ import annotations

from app.config import (
    get_settings,
    parse_user_id_list,
    personal_allowed_user_ids,
    resolve_notify_targets,
)


def _clear() -> None:
    get_settings.cache_clear()


def test_parse_user_id_list_dedupe() -> None:
    assert parse_user_id_list("c59ce84g, 56798dag;c59ce84g,,") == [
        "c59ce84g",
        "56798dag",
    ]


def test_ops_vs_debug_audiences(monkeypatch) -> None:
    monkeypatch.setenv("SAFETY_PERSONAL_ONLY", "true")
    monkeypatch.setenv("FEISHU_OWNER_USER_ID", "56798dag")
    monkeypatch.setenv("FEISHU_NOTIFY_USER_IDS", "c59ce84g,56798dag")
    monkeypatch.setenv("FEISHU_OWNER_OPEN_ID", "")
    _clear()
    try:
        assert resolve_notify_targets(audience="ops") == [
            ("user_id", "c59ce84g"),
            ("user_id", "56798dag"),
        ]
        assert resolve_notify_targets(audience="debug") == [
            ("user_id", "56798dag"),
        ]
        allowed = personal_allowed_user_ids()
        assert allowed == {"c59ce84g", "56798dag"}
    finally:
        _clear()


def test_debug_never_uses_group_when_personal_only_off(monkeypatch) -> None:
    """关掉 PERSONAL_ONLY 后，正式可进群，但健康检查/调试卡仍只能私聊 OWNER。"""
    monkeypatch.setenv("SAFETY_PERSONAL_ONLY", "false")
    monkeypatch.setenv("FEISHU_OWNER_USER_ID", "56798dag")
    monkeypatch.setenv("FEISHU_NOTIFY_USER_IDS", "c59ce84g,56798dag")
    monkeypatch.setenv("FEISHU_DEFAULT_CHAT_ID", "oc_group_for_ops")
    monkeypatch.setenv("FEISHU_RECEIVE_ID_TYPE", "chat_id")
    _clear()
    try:
        assert resolve_notify_targets(audience="debug") == [
            ("user_id", "56798dag"),
        ]
        assert resolve_notify_targets(audience="ops") == [
            ("chat_id", "oc_group_for_ops"),
        ]
    finally:
        _clear()
