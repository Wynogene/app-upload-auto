"""card action / submit-card 安全门闸（离线）。"""

from __future__ import annotations

from app.feishu.actions import (
    ACTION_UPLOAD_SUBMIT,
    _build_upload_request,
    _platforms,
    build_submit_card_value,
)
from app.models import Platform


def test_require_single_platform() -> None:
    err = _platforms(None, require_single=True)
    assert isinstance(err, str)
    assert "单一" in err
    assert _platforms("android", require_single=True) == [Platform.ANDROID]


def test_build_value_defaults_internal() -> None:
    v = build_submit_card_value(app_id="easelife", platform="android")
    assert v["type"] == ACTION_UPLOAD_SUBMIT
    assert v["track"] == "internal"
    assert "allow_production" not in v


def test_production_requires_allow_flag() -> None:
    err = _build_upload_request(
        {
            "app_id": "easelife",
            "track": "production",
            "allow_production": False,
        },
        Platform.ANDROID,
        artifact_path=r"F:\x.aab",
        operator_open_id=None,
    )
    assert isinstance(err, str)
    assert "禁止正式轨" in err or "allow_production" in err


def test_internal_upload_request_ok() -> None:
    req = _build_upload_request(
        {"app_id": "easelife", "track": "internal"},
        Platform.ANDROID,
        artifact_path=r"F:\x.aab",
        operator_open_id=None,
    )
    assert not isinstance(req, str)
    assert req.track == "internal"
    assert req.allow_production is False
    assert req.execute is False


def test_ios_card_execute_defaults_off() -> None:
    v = build_submit_card_value(app_id="boykeep", platform="ios")
    assert "execute" not in v
    req = _build_upload_request(
        {"app_id": "boykeep", "platform": "ios"},
        Platform.IOS,
        artifact_path=r"F:\x.ipa",
        operator_open_id=None,
    )
    assert not isinstance(req, str)
    assert req.execute is False
    req2 = _build_upload_request(
        {"app_id": "boykeep", "execute": True},
        Platform.IOS,
        artifact_path=r"F:\x.ipa",
        operator_open_id=None,
    )
    assert not isinstance(req2, str)
    assert req2.execute is True
