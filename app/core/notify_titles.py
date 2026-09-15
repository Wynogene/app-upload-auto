"""状态变化飞书标题：过审 / 上架 / iOS 分批进度。"""

from __future__ import annotations

from app.models import ReviewState
from app.stores.apple_phased import ios_phased_notify_title
from app.stores.google_lifecycle import approval_notify_title


def review_change_notify_title(
    previous_state: ReviewState | str | None,
    current_state: ReviewState | str | None,
    *,
    previous_message: str | None = None,
    current_message: str | None = None,
) -> str:
    """优先过审专用标题，其次 iOS 分批进度，否则通用文案。"""
    return (
        approval_notify_title(previous_state, current_state)
        or ios_phased_notify_title(previous_message, current_message)
        or "审核/发布状态变化"
    )
