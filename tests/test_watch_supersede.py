"""登记新正式版 Android 时自动停掉更旧盯盘。"""

from __future__ import annotations

from pathlib import Path

import app.core.watch_targets as wt


def test_upsert_deactivates_older_android_production(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "watch_targets.json"
    monkeypatch.setattr(wt, "WATCH_TARGETS_PATH", path)
    monkeypatch.setattr(wt, "DATA_DIR", tmp_path)

    wt.upsert_target(
        app_id="blurams",
        platform="android",
        version_code="1952",
        track="production",
        note="old",
    )
    wt.upsert_target(
        app_id="blurams",
        platform="android",
        version_code="1957",
        track="production",
        note="new",
    )
    # other app / ios / newer code must stay
    wt.upsert_target(
        app_id="easelife",
        platform="android",
        version_code="100",
        track="production",
    )
    wt.upsert_target(app_id="blurams", platform="ios", note="ios")

    by_key = {t.key: t for t in wt.load_targets()}
    assert by_key["blurams:android:1957"].active is True
    assert by_key["blurams:android:1952"].active is False
    assert "1957" in by_key["blurams:android:1952"].note
    assert by_key["easelife:android:100"].active is True
    assert by_key["blurams:ios:-"].active is True


def test_upsert_does_not_deactivate_newer_or_non_production(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "watch_targets.json"
    monkeypatch.setattr(wt, "WATCH_TARGETS_PATH", path)
    monkeypatch.setattr(wt, "DATA_DIR", tmp_path)

    wt.upsert_target(
        app_id="blurams",
        platform="android",
        version_code="2000",
        track="production",
    )
    wt.upsert_target(
        app_id="blurams",
        platform="android",
        version_code="1900",
        track="internal",
        note="test track",
    )
    # registering older production must not kill the newer production watch
    wt.upsert_target(
        app_id="blurams",
        platform="android",
        version_code="1950",
        track="production",
    )

    by_key = {t.key: t for t in wt.load_targets()}
    assert by_key["blurams:android:2000"].active is True
    assert by_key["blurams:android:1950"].active is True
    assert by_key["blurams:android:1900"].active is True
