"""Google Play 版本说明（releaseNotes）解析与校验测试（离线）。"""

from __future__ import annotations

import pytest

from app.stores.release_notes import (
    DEFAULT_LOCALES,
    MAX_CHARS_PER_LOCALE,
    NotesSpecError,
    format_issues,
    has_errors,
    parse_notes_spec,
    resolve_default_locales,
    validate_notes,
)

# ---------------- 解析 ----------------


def test_no_notes_returns_none() -> None:
    assert parse_notes_spec(None) is None
    assert parse_notes_spec([]) is None
    assert parse_notes_spec(["", "   "]) is None


def test_plain_text_applies_to_default_locales() -> None:
    notes = parse_notes_spec(["修复卡顿"], ["zh-CN", "en-US"])
    assert notes == [
        {"language": "zh-CN", "text": "修复卡顿"},
        {"language": "en-US", "text": "修复卡顿"},
    ]


def test_plain_text_uses_builtin_default() -> None:
    assert parse_notes_spec(["x"]) == [{"language": DEFAULT_LOCALES[0], "text": "x"}]


def test_locale_scoped_multiple() -> None:
    notes = parse_notes_spec(["zh-CN=修复卡顿", "en-US=Fix lag"], None)
    assert notes == [
        {"language": "zh-CN", "text": "修复卡顿"},
        {"language": "en-US", "text": "Fix lag"},
    ]


def test_locale_normalized() -> None:
    # zh-cn → zh-CN；en → en
    assert parse_notes_spec(["zh-cn=x", "en=y"], None) == [
        {"language": "zh-CN", "text": "x"},
        {"language": "en", "text": "y"},
    ]


def test_plain_text_containing_equals_is_not_mistaken_for_locale() -> None:
    """正常文案里出现 '=' 不能被误判成语言标签。"""
    notes = parse_notes_spec(["修复 a=b 时崩溃"], ["zh-CN"])
    assert notes == [{"language": "zh-CN", "text": "修复 a=b 时崩溃"}]


def test_mixing_plain_and_scoped_is_rejected() -> None:
    with pytest.raises(NotesSpecError, match="混用"):
        parse_notes_spec(["修复卡顿", "en-US=Fix lag"], None)


def test_multiple_plain_texts_rejected() -> None:
    with pytest.raises(NotesSpecError, match="多个纯文本"):
        parse_notes_spec(["第一条", "第二条"], None)


def test_duplicate_locale_rejected() -> None:
    with pytest.raises(NotesSpecError, match="重复"):
        parse_notes_spec(["zh-CN=a", "zh-CN=b"], None)


def test_empty_locale_text_rejected() -> None:
    with pytest.raises(NotesSpecError, match="为空"):
        parse_notes_spec(["zh-CN="], None)


# ---------------- 校验 ----------------


def test_production_requires_notes() -> None:
    """正式版缺说明应阻断，且提示要指出「可配置默认文案」这条路。"""
    issues = validate_notes(None, track="production", is_production=True)
    assert has_errors(issues) is True
    assert any(i.code == "notes_required" for i in issues)
    msg = format_issues(issues)
    assert "--whats-new" in msg
    assert "release_notes_default" in msg


def test_internal_track_notes_optional() -> None:
    issues = validate_notes(None, track="internal", is_production=False)
    assert issues == []


def test_notes_ok() -> None:
    issues = validate_notes(
        [{"language": "zh-CN", "text": "修复卡顿"}],
        track="production",
        is_production=True,
    )
    assert issues == []


def test_notes_too_long() -> None:
    issues = validate_notes(
        [{"language": "zh-CN", "text": "x" * (MAX_CHARS_PER_LOCALE + 1)}],
        track="production",
        is_production=True,
    )
    assert has_errors(issues) is True
    assert any(i.code == "notes_too_long" for i in issues)


def test_notes_empty_text_is_error() -> None:
    issues = validate_notes([{"language": "zh-CN", "text": "  "}], track="internal")
    assert has_errors(issues) is True


def test_notes_missing_locale_is_error() -> None:
    issues = validate_notes([{"language": "", "text": "x"}], track="internal")
    assert has_errors(issues) is True


def test_resolve_default_locales() -> None:
    # 不传 app_cfg / 空配置时回退到全局（.env RELEASE_NOTES_LOCALES）或内置 en-US
    assert resolve_default_locales(None) in (DEFAULT_LOCALES, ["en-US"])
    assert resolve_default_locales({}) in (DEFAULT_LOCALES, ["en-US"])
    assert resolve_default_locales({"android": {}}) in (DEFAULT_LOCALES, ["en-US"])
    assert resolve_default_locales({"android": {"release_notes_locales": ["zh-CN"]}}) == [
        "zh-CN"
    ]
    # 空列表回退默认，避免出现「零个语言」导致 releaseNotes 为空数组
    assert resolve_default_locales({"android": {"release_notes_locales": []}}) in (
        DEFAULT_LOCALES,
        ["en-US"],
    )


# ---------------- 默认文案（运营配置，非工具编造） ----------------


def test_build_notes_prefers_explicit(monkeypatch) -> None:
    from app.stores import release_notes as rn

    app = {"android": {"release_notes_default": "默认文案", "release_notes_locales": ["en-US"]}}
    notes, source = rn.build_release_notes(["显式文案"], app)
    assert source == rn.EXPLICIT
    assert notes == [{"language": "en-US", "text": "显式文案"}]


def test_build_notes_falls_back_to_configured_default() -> None:
    from app.stores import release_notes as rn

    app = {"android": {"release_notes_default": "默认文案", "release_notes_locales": ["en-US"]}}
    notes, source = rn.build_release_notes(None, app)
    assert source == rn.DEFAULT
    assert notes == [{"language": "en-US", "text": "默认文案"}]


def test_build_notes_applies_default_to_all_locales() -> None:
    from app.stores import release_notes as rn

    app = {
        "android": {
            "release_notes_default": "默认文案",
            "release_notes_locales": ["zh-CN", "en-US"],
        }
    }
    notes, source = rn.build_release_notes(None, app)
    assert source == rn.DEFAULT
    assert notes == [
        {"language": "zh-CN", "text": "默认文案"},
        {"language": "en-US", "text": "默认文案"},
    ]


def test_build_notes_none_when_no_explicit_and_no_default(monkeypatch) -> None:
    from app.stores import release_notes as rn

    class _S:
        release_notes_default = ""
        release_notes_locales = ""

    monkeypatch.setattr(rn, "get_settings", lambda: _S())
    notes, source = rn.build_release_notes(None, {"android": {}})
    assert notes is None
    assert source is None


def test_configured_default_satisfies_production_requirement() -> None:
    """配了默认文案后，正式版不应再被阻断——这正是运营手工上传时的做法。"""
    from app.stores import release_notes as rn

    app = {
        "android": {
            "release_notes_default": "-General: Bug fixes and system optimizations.",
            "release_notes_locales": ["en-US"],
        }
    }
    notes, source = rn.build_release_notes(None, app)
    issues = validate_notes(notes, track="production", is_production=True)
    assert source == rn.DEFAULT
    assert issues == []
