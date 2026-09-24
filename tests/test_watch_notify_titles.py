"""盯盘专用飞书标题（只读指纹差异，不碰商店写接口）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.notify_titles import (
    android_rollout_notify_title,
    ios_blocker_notify_title,
    parse_android_production_rollout,
    publish_action_notify_title,
    review_change_notify_title,
)
from app.models import ReviewState
from app.stores.apple_states import (
    build_processing_stuck_note,
    build_uploaded_age_hours,
    map_version_state,
)


def test_parse_android_production_rollout_prefers_target() -> None:
    msg = (
        "lifecycle[production]: PUBLISHED (已上架) codes=[10428]\n"
        "production: 10428 codes=[10428] status=inProgress rollout=0.05 ← target\n"
        "beta: 1 codes=[1] status=completed"
    )
    status, frac = parse_android_production_rollout(msg)
    assert status == "inprogress"
    assert frac == 0.05


def test_android_rollout_notify_title_fraction_and_halt() -> None:
    prev = "production: x codes=[1] status=inProgress rollout=0.05 ← target"
    new = "production: x codes=[1] status=inProgress rollout=0.2 ← target"
    title = android_rollout_notify_title(prev, new)
    assert title is not None
    assert "放量" in title or "分批" in title
    assert "5%" in title
    assert "20%" in title

    halted = android_rollout_notify_title(
        prev,
        "production: x codes=[1] status=halted rollout=0.05 ← target",
    )
    assert halted is not None
    assert "停发" in halted

    done = android_rollout_notify_title(
        prev,
        "production: x codes=[1] status=completed ← target",
    )
    assert done is not None
    assert "全量" in done


def test_publish_action_ios_and_android() -> None:
    ios = publish_action_notify_title(
        ReviewState.IN_REVIEW,
        ReviewState.APPROVED,
        current_message="5.1：已通过，等待你手动发布（手动发布模式）；需你到 ASC 点发布",
    )
    assert ios is not None
    assert "ASC" in ios

    android = publish_action_notify_title(
        ReviewState.IN_REVIEW,
        ReviewState.APPROVED,
        current_message="lifecycle[production]: APPROVED_NOT_PUBLISHED (自管式待发布)",
    )
    assert android is not None
    assert "Play Console" in android

    # ACCEPTED / 无手动发布标记 → 不误报需操作
    assert (
        publish_action_notify_title(
            ReviewState.IN_REVIEW,
            ReviewState.APPROVED,
            current_message="5.1：审核通过",
        )
        is None
    )


def test_ios_blocker_titles() -> None:
    assert "出口合规" in (
        ios_blocker_notify_title("", "5.1：等待出口合规确认") or ""
    )
    assert "合同" in (ios_blocker_notify_title("", "5.1：等待合同生效") or "")
    assert "构建" in (
        ios_blocker_notify_title(
            "构建 1：可用",
            "构建 1：无效（多为签名或权限问题）",
        )
        or ""
    )
    assert "处理中" in (
        ios_blocker_notify_title(
            "构建 1：Apple 处理中",
            "构建 1：Apple 处理中；构建处理超时提示：已约3小时仍在 PROCESSING",
        )
        or ""
    )


def test_build_processing_stuck_note() -> None:
    past = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    note = build_processing_stuck_note("PROCESSING", past)
    assert note is not None
    assert "构建处理超时" in note
    assert build_processing_stuck_note("PROCESSING", past, threshold_hours=10) is None
    assert build_processing_stuck_note("VALID", past) is None
    assert build_uploaded_age_hours(past) is not None
    assert build_uploaded_age_hours(past) >= 2.9


def test_compliance_mapped() -> None:
    assert map_version_state("WAITING_FOR_EXPORT_COMPLIANCE") == (
        ReviewState.WAITING_FOR_REVIEW
    )
    assert map_version_state("PENDING_CONTRACT") == ReviewState.WAITING_FOR_REVIEW


def test_review_change_priority_chain() -> None:
    # 构建失效优先于分批
    t = review_change_notify_title(
        ReviewState.DRAFT,
        ReviewState.DRAFT,
        previous_message="构建 1：可用；分批：ACTIVE 第1天≈1%",
        current_message="构建 1：无效（多为签名或权限问题）；分批：ACTIVE 第1天≈1%",
    )
    assert "构建" in t

    # Android 放量
    t2 = review_change_notify_title(
        ReviewState.RELEASED,
        ReviewState.RELEASED,
        previous_message="production: a status=inProgress rollout=0.05 ← target",
        current_message="production: a status=inProgress rollout=0.5 ← target",
    )
    assert "放量" in t2

    # 过审待发布
    t3 = review_change_notify_title(
        ReviewState.IN_REVIEW,
        ReviewState.APPROVED,
        previous_message="lifecycle: IN_REVIEW",
        current_message="lifecycle[production]: APPROVED_NOT_PUBLISHED (自管式待发布)",
    )
    assert "需你操作" in t3
    assert "Play Console" in t3
