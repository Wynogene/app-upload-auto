"""Regression tests for App Store Connect JWT `sub` claim resolution (no network).

背景：Apple 有两类 API 密钥，JWT 要求互斥：
  * 团队密钥 AuthKey_<KEYID>.p8 → 不能带 sub
  * 个人密钥 ApiKey_<KEYID>.p8  → 必须带 sub="user"
填错会直接 401 NOT_AUTHORIZED，且报错文案与「密钥无效」一模一样，很容易误判。
"""

from __future__ import annotations

import pytest

from app.config import ROOT_DIR, resolve_apple_token_sub
from app.stores.apple import resolve_apple_cred


@pytest.fixture
def fake_settings(monkeypatch):
    """替换 get_settings，可控全局凭据。"""

    class _S:
        apple_token_sub = ""
        apple_key_id = "GLOBKEYID1"
        apple_issuer_id = "00000000-0000-0000-0000-000000000000"
        apple_private_key_path = "secrets/ApiKey_GLOBKEYID1.p8"

    s = _S()

    def _get(*args, **kwargs):
        return s

    monkeypatch.setattr("app.config.get_settings", _get)
    monkeypatch.setattr("app.stores.apple.get_settings", _get)
    return s


def test_individual_key_needs_user_sub(fake_settings) -> None:
    assert resolve_apple_token_sub("secrets/ApiKey_ABCDE12345.p8") == "user"


def test_team_key_has_no_sub(fake_settings) -> None:
    assert resolve_apple_token_sub("secrets/AuthKey_ABCDE12345.p8") is None


def test_explicit_sub_overrides_filename(fake_settings) -> None:
    assert resolve_apple_token_sub("secrets/AuthKey_ABCDE12345.p8", "user") == "user"


def test_explicit_none_disables_sub(fake_settings) -> None:
    assert resolve_apple_token_sub("secrets/ApiKey_ABCDE12345.p8", "none") is None


def test_cred_falls_back_to_env_globally(fake_settings) -> None:
    cred = resolve_apple_cred({"id": "blurams", "ios": {"app_store_app_id": "1"}})
    assert cred.key_id == fake_settings.apple_key_id
    assert cred.issuer_id == fake_settings.apple_issuer_id
    assert cred.key_path == ROOT_DIR / "secrets/ApiKey_GLOBKEYID1.p8"
    assert cred.source == "env"


def test_cred_per_app_overrides_env(fake_settings) -> None:
    """boykeep 属于另一个 Apple 团队时，用 apps.yaml 的独立凭据。"""
    app = {
        "id": "boykeep",
        "ios": {
            "app_store_app_id": "6472993326",
            "key_id": "BOYKEEPKEY1",
            "issuer_id": "11111111-1111-1111-1111-111111111111",
            "private_key_path": "secrets/ApiKey_BOYKEEPKEY1.p8",
        },
    }
    cred = resolve_apple_cred(app)
    assert cred.key_id == "BOYKEEPKEY1"
    assert cred.issuer_id == "11111111-1111-1111-1111-111111111111"
    assert cred.key_path == ROOT_DIR / "secrets/ApiKey_BOYKEEPKEY1.p8"
    assert cred.source == "app"
    assert cred.token_sub == "user"


def test_cred_absolute_path_kept(fake_settings) -> None:
    app = {"id": "x", "ios": {"private_key_path": r"F:\keys\ApiKey_ABS.p8"}}
    assert resolve_apple_cred(app).key_path == __import__("pathlib").Path(r"F:\keys\ApiKey_ABS.p8")
