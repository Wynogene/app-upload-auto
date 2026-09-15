"""控制台提示文案（方案 B：按平台区分）。"""

from __future__ import annotations

from app.core.watch_targets import (
    ANDROID_HINT,
    CONSOLE_HINT,
    IOS_HINT,
    console_hint_for,
    console_hints_for,
)


def test_android_hint_mentions_rollout_and_managed() -> None:
    assert "分批比例" in ANDROID_HINT
    assert "自管式" in ANDROID_HINT
    assert "不会代点「发布」" in ANDROID_HINT
    assert "只负责上传与送审" not in ANDROID_HINT
    assert "Play Console" in ANDROID_HINT


def test_ios_hint_mentions_asc_and_phased() -> None:
    assert "App Store Connect" in IOS_HINT
    assert "7 天" in IOS_HINT
    assert "Play Console" not in IOS_HINT
    assert "手动发布" in IOS_HINT


def test_console_hint_for_platform() -> None:
    assert console_hint_for("android") == ANDROID_HINT
    assert console_hint_for("ios") == IOS_HINT
    assert console_hint_for(None) == ANDROID_HINT
    # 兼容旧名
    assert CONSOLE_HINT == ANDROID_HINT


def test_console_hints_for_mixed() -> None:
    text = console_hints_for(["android", "ios", "android"])
    assert ANDROID_HINT in text
    assert IOS_HINT in text
    assert text.index(ANDROID_HINT) < text.index(IOS_HINT)


def test_load_targets_keeps_zero_heartbeat(tmp_path, monkeypatch) -> None:
    from app.core import watch_targets as wt

    path = tmp_path / "watch_targets.json"
    monkeypatch.setattr(wt, "WATCH_TARGETS_PATH", path)
    monkeypatch.setattr(wt, "DATA_DIR", tmp_path)
    wt.upsert_target(
        app_id="easelife",
        platform="android",
        version_code="10428",
        heartbeat_hours=0.0,
    )
    loaded = wt.load_targets()
    assert loaded[0].heartbeat_hours == 0.0
