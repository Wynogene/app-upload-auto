"""submitted=true 失败后严格复核是否已进审。"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.stores.apple_review_submit import verify_submission_already_in_review


def test_verify_already_in_review_ok() -> None:
    client = MagicMock()

    def _get(url, headers=None, params=None):  # noqa: ANN001
        r = MagicMock()
        r.status_code = 200
        if "reviewSubmissions/" in url:
            r.json.return_value = {
                "data": {
                    "attributes": {
                        "state": "WAITING_FOR_REVIEW",
                        "submittedDate": "2026-01-01",
                    }
                }
            }
        else:
            r.json.return_value = {
                "data": {
                    "attributes": {
                        "versionString": "1.2.3",
                        "appVersionState": "WAITING_FOR_REVIEW",
                    }
                }
            }
        return r

    client.get.side_effect = _get
    out = verify_submission_already_in_review(
        client,
        {},
        submission_id="sub-1",
        version_id="ver-1",
        version_string="1.2.3",
    )
    assert out is not None
    assert out["verified_already_in_review"] is True


def test_verify_rejects_version_mismatch() -> None:
    client = MagicMock()

    def _get(url, headers=None, params=None):  # noqa: ANN001
        r = MagicMock()
        r.status_code = 200
        if "reviewSubmissions/" in url:
            r.json.return_value = {
                "data": {"attributes": {"state": "WAITING_FOR_REVIEW"}}
            }
        else:
            r.json.return_value = {
                "data": {
                    "attributes": {
                        "versionString": "9.9.9",
                        "appVersionState": "WAITING_FOR_REVIEW",
                    }
                }
            }
        return r

    client.get.side_effect = _get
    assert (
        verify_submission_already_in_review(
            client,
            {},
            submission_id="sub-1",
            version_id="ver-1",
            version_string="1.2.3",
        )
        is None
    )


def test_verify_rejects_ready_for_review() -> None:
    client = MagicMock()
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        "data": {"attributes": {"state": "READY_FOR_REVIEW"}}
    }
    client.get.return_value = r
    assert (
        verify_submission_already_in_review(
            client,
            {},
            submission_id="sub-1",
            version_id="ver-1",
            version_string="1.2.3",
        )
        is None
    )
