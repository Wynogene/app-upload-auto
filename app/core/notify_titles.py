"""状态变化飞书标题：过审 / 待发布 / 被拒 / 合规 / 放量 / iOS 分批。

全部基于只读 status 指纹差异，不写 Google Play / App Store。
政策状态页（Play「政策状态」）API 读不到，不在此覆盖。
"""

from __future__ import annotations

import re

from app.core.rollout import describe_rollout
from app.models import ReviewState
from app.stores.apple_phased import ios_phased_notify_title
from app.stores.google_lifecycle import approval_notify_title

_ANDROID_TRACK_LINE_RE = re.compile(
    r"(?P<track>production)\s*:\s*[^\n]*?"
    r"status=(?P<status>\w+)"
    r"(?:\s+rollout=(?P<frac>[0-9.]+))?"
    r"(?P<tgt>\s*←\s*target)?",
    re.IGNORECASE,
)


def _state_value(state: ReviewState | str | None) -> str:
    if isinstance(state, ReviewState):
        return state.value
    return (state or "").lower()


def parse_android_production_rollout(
    message: str | None,
) -> tuple[str | None, float | None]:
    """从 Android status message 抽出 production 的 status 与 userFraction。

    优先带 ``← target`` 的行。
    """
    if not message:
        return None, None
    fallback: tuple[str | None, float | None] | None = None
    for m in _ANDROID_TRACK_LINE_RE.finditer(message):
        status = (m.group("status") or "").lower()
        frac_raw = m.group("frac")
        frac = float(frac_raw) if frac_raw is not None else None
        item = (status or None, frac)
        if m.group("tgt"):
            return item
        if fallback is None:
            fallback = item
    return fallback if fallback else (None, None)


def publish_action_notify_title(
    previous_state: ReviewState | str | None,
    current_state: ReviewState | str | None,
    *,
    current_message: str | None = None,
) -> str | None:
    """过审但还需你点发布（自管式 / iOS 手动发布）。

    仅在 message 带明确标记时触发，避免把 iOS ``ACCEPTED`` /
    ``PENDING_APPLE_RELEASE`` 误报成「需你操作」。
    """
    del previous_state  # 指纹变化时才会调用；是否需操作看 message 标记
    cur = _state_value(current_state)
    msg = current_message or ""

    if cur != ReviewState.APPROVED.value:
        return None

    if "PENDING_DEVELOPER_RELEASE" in msg or (
        "手动发布" in msg and ("ASC" in msg or "等待你手动" in msg or "需你到 ASC" in msg)
    ):
        return "【需你操作】iOS 已过审，请到 ASC 手动发布"

    if "APPROVED_NOT_PUBLISHED" in msg or "自管式待发布" in msg:
        return "【需你操作】Android 已过审，请到 Play Console 发布"

    return None


def ios_blocker_notify_title(
    previous_message: str | None,
    current_message: str | None,
) -> str | None:
    """合规 / 合同 / 构建失效 / 构建长时间 PROCESSING。"""
    prev = previous_message or ""
    new = current_message or ""
    if not new:
        return None

    if "等待出口合规" in new and "等待出口合规" not in prev:
        return "【需你操作】iOS 等待出口合规确认"
    if "等待合同" in new and "等待合同" not in prev:
        return "【需你操作】iOS 等待合同生效"

    build_bad = ("构建" in new) and (
        "无效" in new or "处理失败" in new
    )
    build_bad_prev = ("构建" in prev) and (
        "无效" in prev or "处理失败" in prev
    )
    if build_bad and not build_bad_prev:
        return "【需处理】iOS 构建无效或处理失败"

    if "构建处理超时" in new and "构建处理超时" not in prev:
        return "【需排查】iOS 构建长时间仍在处理中"

    return None


def android_rollout_notify_title(
    previous_message: str | None,
    current_message: str | None,
) -> str | None:
    """Android production 放量比例或 halted 变化。"""
    prev_status, prev_frac = parse_android_production_rollout(previous_message)
    new_status, new_frac = parse_android_production_rollout(current_message)
    if new_status is None and new_frac is None:
        return None
    if prev_status == new_status and prev_frac == new_frac:
        return None

    if new_status == "halted" and prev_status != "halted":
        return "Android 分批已停发（halted）"
    if prev_status == "halted" and new_status and new_status != "halted":
        pct = describe_rollout(new_frac)
        return f"Android 分批已恢复放量（{pct}）"

    if prev_frac != new_frac and new_frac is not None:
        if prev_frac is not None:
            return (
                f"Android 放量比例变更"
                f"（{describe_rollout(prev_frac)} → {describe_rollout(new_frac)}）"
            )
        return f"Android 放量比例更新（{describe_rollout(new_frac)}）"

    return None


def review_change_notify_title(
    previous_state: ReviewState | str | None,
    current_state: ReviewState | str | None,
    *,
    previous_message: str | None = None,
    current_message: str | None = None,
) -> str:
    """标题优先级：阻塞项 → 待发布 → 过审/被拒 → 放量 → 分批 → 通用。"""
    return (
        ios_blocker_notify_title(previous_message, current_message)
        or publish_action_notify_title(
            previous_state,
            current_state,
            current_message=current_message,
        )
        or approval_notify_title(previous_state, current_state)
        or android_rollout_notify_title(previous_message, current_message)
        or ios_phased_notify_title(previous_message, current_message)
        or "审核/发布状态变化"
    )
