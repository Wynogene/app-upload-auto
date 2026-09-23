"""iOS / Android 提审成功后自动登记盯盘。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import app.core.watch_targets as wt
from app.core import service as svc
from app.models import OperationResult, Platform


def test_maybe_register_watch_ios_execute(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "watch_targets.json"
    monkeypatch.setattr(wt, "WATCH_TARGETS_PATH", path)
    monkeypatch.setattr(wt, "DATA_DIR", tmp_path)

    hint = svc._maybe_register_watch(
        OperationResult(
            ok=True,
            app_id="blurams",
            platform=Platform.IOS,
            message="submitted",
            details={
                "execute": True,
                "version_name": "5.1049.127",
            },
        )
    )
    assert hint is not None
    assert "已登记 iOS 盯盘" in hint
    assert "5.1049.127" in hint
    assert "进度以 App Store Connect" not in hint

    by_key = {t.key: t for t in wt.load_targets()}
    t = by_key["blurams:ios:-"]
    assert t.active is True
    assert "5.1049.127" in t.note


def test_maybe_register_watch_ios_dry_run_skips(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "watch_targets.json"
    monkeypatch.setattr(wt, "WATCH_TARGETS_PATH", path)
    monkeypatch.setattr(wt, "DATA_DIR", tmp_path)

    hint = svc._maybe_register_watch(
        OperationResult(
            ok=True,
            app_id="blurams",
            platform=Platform.IOS,
            message="dry",
            details={"execute": False, "dry_run": True, "version_name": "5.1049.128"},
        )
    )
    assert hint is None
    assert wt.load_targets() == []


def test_maybe_register_watch_android_production(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "watch_targets.json"
    monkeypatch.setattr(wt, "WATCH_TARGETS_PATH", path)
    monkeypatch.setattr(wt, "DATA_DIR", tmp_path)

    hint = svc._maybe_register_watch(
        OperationResult(
            ok=True,
            app_id="blurams",
            platform=Platform.ANDROID,
            message="ok",
            details={"track": "production", "version_code": 1959},
        )
    )
    assert hint is not None
    assert "1959" in hint
    assert wt.load_targets()[0].version_code == "1959"


def test_maybe_register_watch_all_product_lines(tmp_path: Path, monkeypatch) -> None:
    """blurams / easelife / boykeep 同一登记路径，无产品线白名单。"""
    path = tmp_path / "watch_targets.json"
    monkeypatch.setattr(wt, "WATCH_TARGETS_PATH", path)
    monkeypatch.setattr(wt, "DATA_DIR", tmp_path)

    for app_id, vc in (("blurams", 1), ("easelife", 2), ("boykeep", 3)):
        assert (
            svc._maybe_register_watch(
                OperationResult(
                    ok=True,
                    app_id=app_id,
                    platform=Platform.ANDROID,
                    message="ok",
                    details={"track": "production", "version_code": vc},
                )
            )
            is not None
        )
        assert (
            svc._maybe_register_watch(
                OperationResult(
                    ok=True,
                    app_id=app_id,
                    platform=Platform.IOS,
                    message="ok",
                    details={"execute": True, "version_name": f"1.0.{vc}"},
                )
            )
            is not None
        )

    keys = {t.key for t in wt.load_targets() if t.active}
    assert keys == {
        "blurams:android:1",
        "easelife:android:2",
        "boykeep:android:3",
        "blurams:ios:-",
        "easelife:ios:-",
        "boykeep:ios:-",
    }


def test_submit_ios_registers_watch(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "watch_targets.json"
    monkeypatch.setattr(wt, "WATCH_TARGETS_PATH", path)
    monkeypatch.setattr(wt, "DATA_DIR", tmp_path)

    service = svc.AppReleaseService()
    monkeypatch.setattr(
        service,
        "_app",
        lambda _id: {"id": "blurams", "ios": {"enabled": True}, "allowed_open_ids": []},
    )
    monkeypatch.setattr(service, "_ensure_allowed", lambda *_a, **_k: None)
    service.apple = MagicMock()
    service.apple.submit.return_value = OperationResult(
        ok=True,
        app_id="blurams",
        platform=Platform.IOS,
        message="提审成功",
        details={"execute": True, "version_name": "5.1049.127"},
    )

    from app.models import SubmitRequest

    result = service.submit(
        SubmitRequest(
            app_id="blurams",
            platform=Platform.IOS,
            version_name="5.1049.127",
            execute=True,
        )
    )
    assert "已登记 iOS 盯盘" in result.message
    assert "blurams:ios:-" in {t.key for t in wt.load_targets()}
