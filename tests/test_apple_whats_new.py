"""iOS what's New 补全计划（离线，不碰 ASC）。"""

from __future__ import annotations

from app.stores.apple_whats_new import (
    LocalizationRow,
    parse_localization_rows,
    plan_whats_new_updates,
    resolve_text_for_locale,
    summarize_plan,
)


def test_parse_localization_rows() -> None:
    payload = {
        "data": [
            {
                "id": "loc1",
                "attributes": {"locale": "en-US", "whatsNew": "hello"},
            },
            {
                "id": "loc2",
                "attributes": {"locale": "zh-Hans", "whatsNew": None},
            },
        ]
    }
    rows = parse_localization_rows(payload)
    assert len(rows) == 2
    assert rows[1].whats_new == ""


def test_plan_fills_only_empty_by_default() -> None:
    rows = [
        LocalizationRow("1", "en-US", "already"),
        LocalizationRow("2", "ja", ""),
        LocalizationRow("3", "de-DE", "  "),
    ]
    plan = plan_whats_new_updates(
        rows,
        text_by_locale={"en-US": "Bug fixes"},
        fallback_text="Bug fixes",
        fill_empty_only=True,
    )
    by_locale = {p.locale: p for p in plan}
    assert by_locale["en-US"].action == "skip_has_text"
    assert by_locale["ja"].action == "fill"
    assert by_locale["ja"].planned == "Bug fixes"
    assert by_locale["de-DE"].action == "fill"
    assert summarize_plan(plan)["fill"] == 2


def test_plan_force_overwrites() -> None:
    rows = [LocalizationRow("1", "en-US", "old")]
    plan = plan_whats_new_updates(
        rows,
        text_by_locale={"en-US": "new"},
        fallback_text="new",
        fill_empty_only=False,
    )
    assert plan[0].action == "fill"
    assert plan[0].planned == "new"


def test_resolve_text_falls_back() -> None:
    assert (
        resolve_text_for_locale(
            "fr-FR",
            text_by_locale={"en-US": "Hello"},
            fallback_text="Hello",
        )
        == "Hello"
    )
