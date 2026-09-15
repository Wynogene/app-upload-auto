"""多 App 就绪检查（离线部分，不碰商店）。"""

from __future__ import annotations

from app.core.apps_ready import (
    check_android_config,
    check_ios_config,
    format_apps_ready,
    run_apps_ready,
)


def test_android_sa_present_for_blurams() -> None:
    app = {
        "id": "blurams",
        "android": {
            "enabled": True,
            "package_name": "com.blurams.ipc",
            "service_account_json": "secrets/google-play-sa-blurams-easelife.json",
        },
    }
    items = check_android_config(app)
    assert any(i.code == "sa_ok" and i.ok for i in items)


def test_boykeep_ios_without_override_still_has_env_cred_check() -> None:
    app = {
        "id": "boykeep",
        "play_console_account": "boykeep_separate",
        "ios": {
            "enabled": True,
            "bundle_id": "com.boykeep.ipc1",
            "app_store_app_id": "6472993326",
            "key_id": "",
            "issuer_id": "",
            "private_key_path": "",
        },
    }
    items = check_ios_config(app)
    assert any(i.code == "bundle_ok" for i in items)
    assert any(i.code == "apple_needs_team_override" and not i.ok for i in items)


def test_format_mentions_boykeep_hint_on_fail() -> None:
    from app.core.apps_ready import CheckItem

    text = format_apps_ready(
        [
            CheckItem(
                "boykeep",
                "ios",
                False,
                "asc_app_not_visible",
                "not visible",
            )
        ]
    )
    assert "boykeep" in text
    assert "FAIL" in text


def test_run_apps_ready_no_probe_offline() -> None:
    items = run_apps_ready(probe_asc=False, app_ids=["easelife", "anxinkan"])
    # anxinkan 双端 disabled，默认不包含；easelife 应有多项
    assert any(i.app_id == "easelife" for i in items)
    assert not any(i.app_id == "anxinkan" for i in items)
