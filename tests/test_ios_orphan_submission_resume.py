"""空 / 已有本版本 item 的 READY_FOR_REVIEW 审核会话可续跑。"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.stores.apple_review_submit import (
    classify_open_submission_resume,
    match_submission_item_for_version,
    pick_resumable_empty_submission,
)


def test_pick_resumable_empty_ready_for_review() -> None:
    client = MagicMock()
    headers = {"Authorization": "Bearer x"}
    open_subs = [
        {
            "id": "sub-empty",
            "attributes": {"state": "READY_FOR_REVIEW"},
        }
    ]
    client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"data": []},
    )
    sid, states = pick_resumable_empty_submission(client, headers, open_subs)
    assert sid == "sub-empty"
    assert states == [("READY_FOR_REVIEW", "sub-empty")]


def test_classify_pending_version_with_item() -> None:
    client = MagicMock()
    open_subs = [
        {
            "id": "sub-full",
            "attributes": {"state": "READY_FOR_REVIEW"},
        }
    ]
    item = {
        "id": "item-1",
        "relationships": {
            "appStoreVersion": {"data": {"type": "appStoreVersions", "id": "ver-9"}}
        },
    }
    client.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"data": [item]},
    )
    info = classify_open_submission_resume(client, {}, open_subs)
    assert info["mode"] == "pending_version"
    assert info["submission_id"] == "sub-full"
    assert match_submission_item_for_version(info["items"], "ver-9") == "item-1"
    assert match_submission_item_for_version(info["items"], "ver-other") is None


def test_classify_unreadable_items() -> None:
    client = MagicMock()
    open_subs = [
        {
            "id": "sub-x",
            "attributes": {"state": "READY_FOR_REVIEW"},
        }
    ]
    client.get.return_value = MagicMock(status_code=500, text="boom")
    info = classify_open_submission_resume(client, {}, open_subs)
    assert info["mode"] == "unreadable"


def test_pick_rejects_waiting_for_review() -> None:
    client = MagicMock()
    open_subs = [
        {
            "id": "sub-waiting",
            "attributes": {"state": "WAITING_FOR_REVIEW"},
        }
    ]
    sid, _ = pick_resumable_empty_submission(client, {}, open_subs)
    assert sid is None
    client.get.assert_not_called()


def test_match_rejects_multi_items() -> None:
    items = [
        {
            "id": "a",
            "relationships": {
                "appStoreVersion": {"data": {"id": "ver-1"}},
            },
        },
        {
            "id": "b",
            "relationships": {
                "appStoreVersion": {"data": {"id": "ver-1"}},
            },
        },
    ]
    assert match_submission_item_for_version(items, "ver-1") is None
