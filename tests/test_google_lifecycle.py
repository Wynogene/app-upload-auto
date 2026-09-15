"""releaseLifecycleState 映射与过审通知标题测试（纯本地，不碰 Play）。"""

from __future__ import annotations

import pytest

from app.models import ReviewState
from app.stores.google_lifecycle import (
    approval_notify_title,
    describe_lifecycle,
    find_release_for_version,
    map_lifecycle_to_review_state,
    normalize_lifecycle,
    version_codes_of,
)


class TestNormalizeLifecycle:
    def test_strips_prefix(self):
        assert normalize_lifecycle("RELEASE_LIFECYCLE_STATE_IN_REVIEW") == "IN_REVIEW"
        assert normalize_lifecycle("PUBLISHED") == "PUBLISHED"

    def test_empty(self):
        assert normalize_lifecycle(None) is None
        assert normalize_lifecycle("") is None


class TestMapLifecycle:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("RELEASE_LIFECYCLE_STATE_IN_REVIEW", ReviewState.IN_REVIEW),
            ("APPROVED_NOT_PUBLISHED", ReviewState.APPROVED),
            ("RELEASE_LIFECYCLE_STATE_APPROVED_NOT_PUBLISHED", ReviewState.APPROVED),
            ("PUBLISHED", ReviewState.RELEASED),
            ("RELEASE_LIFECYCLE_STATE_PUBLISHED", ReviewState.RELEASED),
            ("NOT_APPROVED", ReviewState.REJECTED),
            ("DRAFT", ReviewState.DRAFT),
            ("NOT_SENT_FOR_REVIEW", ReviewState.DRAFT),
            (None, ReviewState.UNKNOWN),
            ("SOMETHING_NEW", ReviewState.UNKNOWN),
        ],
    )
    def test_map(self, raw, expected):
        assert map_lifecycle_to_review_state(raw) == expected


class TestDescribeLifecycle:
    def test_managed_approved(self):
        text = describe_lifecycle("APPROVED_NOT_PUBLISHED")
        assert "过审" in text
        assert "尚未对用户开放" in text or "自管" in text

    def test_published(self):
        text = describe_lifecycle("PUBLISHED")
        assert "上架" in text or "开放" in text


class TestFindRelease:
    def test_by_version_code(self):
        releases = [
            {
                "releaseName": "10420",
                "activeArtifacts": [{"versionCode": 10420}],
                "releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_PUBLISHED",
            },
            {
                "releaseName": "10428",
                "activeArtifacts": [{"versionCode": 10428}],
                "releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_IN_REVIEW",
            },
        ]
        rel = find_release_for_version(releases, "10428")
        assert rel is not None
        assert version_codes_of(rel) == ["10428"]
        assert map_lifecycle_to_review_state(rel["releaseLifecycleState"]) == ReviewState.IN_REVIEW

    def test_missing(self):
        assert find_release_for_version([], "1") is None


class TestApprovalNotifyTitle:
    def test_managed_on_approval_moment(self):
        # 自管式开启：过审但不对用户开放
        title = approval_notify_title(ReviewState.IN_REVIEW, ReviewState.APPROVED)
        assert title is not None
        assert "过审" in title or "通过" in title
        assert "尚未对用户开放" in title or "自管" in title

    def test_managed_off_auto_publish(self):
        # 自管式关闭：过审并自动上架
        title = approval_notify_title(ReviewState.IN_REVIEW, ReviewState.RELEASED)
        assert title is not None
        assert "通过" in title or "上架" in title

    def test_managed_then_publish(self):
        title = approval_notify_title(ReviewState.APPROVED, ReviewState.RELEASED)
        assert title is not None
        assert "上架" in title or "开放" in title

    def test_rejected(self):
        title = approval_notify_title(ReviewState.IN_REVIEW, ReviewState.REJECTED)
        assert title is not None
        assert "未通过" in title

    def test_no_special_for_same_or_unrelated(self):
        assert approval_notify_title(ReviewState.RELEASED, ReviewState.RELEASED) is None
        assert approval_notify_title(ReviewState.DRAFT, ReviewState.IN_REVIEW) is None
