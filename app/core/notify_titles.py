"""状态变化飞书标题：过审 / 待发布 / 被拒 / 合规 / 放量 / iOS 分批。

全部基于只读 status 指纹差异，不写 Google Play / App Store。
政策状态页（Play「政策状态」）API 读不到，不在此覆盖。
"""

from __future__ import annotations

import re

from app.core.rollout import describe_rollout
from app.models import ReviewState
from app.stores.apple_phased import (
    ios_phased_notify_title,
    parse_phased_fingerprint,
    phased_percent_for_day,
)
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
        return "Android 分批已停发 · 放量冻结（尚未全量）"
    if prev_status == "halted" and new_status and new_status != "halted":
        pct = describe_rollout(new_frac)
        return f"Android 分批已恢复 · 继续放量（{pct}，尚未全量）"

    if new_status == "completed" and prev_status != "completed":
        return "Android 分批结束 · 已全量 100%"

    if prev_frac != new_frac and new_frac is not None:
        if new_frac >= 0.999:
            return "Android 分批结束 · 已全量 100%"
        if prev_frac is not None:
            return (
                f"Android 分批进行中 · 放量 "
                f"{describe_rollout(prev_frac)} → {describe_rollout(new_frac)}"
            )
        return f"Android 分批进行中 · 放量 {describe_rollout(new_frac)}"

    return None


def ios_watch_notify_title(
    previous_state: ReviewState | str | None,
    current_state: ReviewState | str | None,
    *,
    previous_message: str | None = None,
    current_message: str | None = None,
) -> str | None:
    """iOS 盯盘标题：把「刚上线·分批中」和「分批结束·全量」说开。

    仅在正文像 iOS（含分批/构建/已上线等）时生效，避免误伤 Android。
    """
    new_msg = current_message or ""
    prev_msg = previous_message or ""
    # Android production 行特征
    if re.search(r"production\s*:", new_msg, re.I) and "rollout=" in new_msg.lower():
        return None
    looks_ios = any(
        k in new_msg
        for k in ("分批", "构建", "ASC", "TestFlight", "已上线", "等待出口合规", "手动发布")
    )
    if not looks_ios and "分批" not in prev_msg:
        return None

    prev = _state_value(previous_state)
    cur = _state_value(current_state)
    reviewing = {
        ReviewState.IN_REVIEW.value,
        ReviewState.WAITING_FOR_REVIEW.value,
    }
    new_ph, new_day = parse_phased_fingerprint(new_msg)
    prev_ph, _prev_day = parse_phased_fingerprint(prev_msg)

    # 审核中 → 已上线：按分批阶段给不同标题
    if cur == ReviewState.RELEASED.value and prev in reviewing:
        if new_ph == "ACTIVE":
            pct = phased_percent_for_day(new_day)
            day_bit = f"第{new_day}天" if new_day is not None else "进行中"
            pct_bit = f"≈{pct}%" if pct is not None else ""
            return f"iOS 已上线 · 分批放量进行中（{day_bit}{pct_bit}，尚未全量）"
        if new_ph == "COMPLETE":
            return "iOS 已上线 · 当前已是全量（分批已结束）"
        if new_ph == "PAUSED":
            return "iOS 已上线 · 分批已暂停（尚未全量）"
        if new_ph in {None, "INACTIVE"}:
            return "iOS 已上线 · 分批尚未开始或未开启"

    # 已在线：分批状态变化（含 ACTIVE→COMPLETE）
    phased_title = ios_phased_notify_title(prev_msg, new_msg)
    if phased_title:
        return phased_title

    # 已 released，正文只有「已上线」且分批未解析到，避免落到「审核/发布状态变化」
    if (
        cur == ReviewState.RELEASED.value
        and prev == ReviewState.RELEASED.value
        and "已上线" in new_msg
        and not new_ph
        and prev_ph
    ):
        return "iOS 上线状态刷新 · 分批信息暂缺"

    return None


def review_change_notify_title(
    previous_state: ReviewState | str | None,
    current_state: ReviewState | str | None,
    *,
    previous_message: str | None = None,
    current_message: str | None = None,
) -> str:
    """标题优先级：阻塞项 → 待发布 → iOS 分批/上线分层 → 过审/被拒 → Android 放量 → 通用。"""
    return (
        ios_blocker_notify_title(previous_message, current_message)
        or publish_action_notify_title(
            previous_state,
            current_state,
            current_message=current_message,
        )
        or ios_watch_notify_title(
            previous_state,
            current_state,
            previous_message=previous_message,
            current_message=current_message,
        )
        or approval_notify_title(previous_state, current_state)
        or android_rollout_notify_title(previous_message, current_message)
        or "审核/发布状态变化"
    )
