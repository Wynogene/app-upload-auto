"""App Store Connect 分阶段发布（Phased Release）辅助。

与 Google Play 不同：iOS **不能自定义百分比**。提审前设定「是否开启分批」；
开启后按 Apple 固定 7 天曲线自动抬升（仅对开启自动更新的用户）：

    Day1 1% → Day2 2% → Day3 5% → Day4 10% → Day5 20% → Day6 50% → Day7 100%

本工具提审时会 ``POST /v1/appStoreVersionPhasedReleases``（状态 INACTIVE），
确保过审/手动发布后走分批，而不是接近全量。只读查询仍用 GET。

API::

    GET  /v1/appStoreVersions/{id}/appStoreVersionPhasedRelease
    POST /v1/appStoreVersionPhasedReleases

字段：``phasedReleaseState`` / ``currentDayNumber`` / ``startDate`` /
``totalPauseDuration``。
"""

from __future__ import annotations

import re
from typing import Any

# Apple 官方固定曲线（自动更新用户）
PHASED_DAY_PERCENT: dict[int, int] = {
    1: 1,
    2: 2,
    3: 5,
    4: 10,
    5: 20,
    6: 50,
    7: 100,
}

_PHASED_STATE_LABEL: dict[str, str] = {
    "INACTIVE": "未启用/未开始",
    "ACTIVE": "分批进行中",
    "PAUSED": "分批已暂停",
    "COMPLETE": "分批已结束（全量）",
}

_PHASED_IN_MSG_RE = re.compile(
    r"分批[：:]\s*(?P<state>\w+)(?:\s+第(?P<day>\d+)天)?"
)


def phased_percent_for_day(day: int | None) -> int | None:
    if day is None:
        return None
    try:
        d = int(day)
    except (TypeError, ValueError):
        return None
    return PHASED_DAY_PERCENT.get(d)


def describe_phased_release(attrs: dict[str, Any] | None) -> str | None:
    """把 phasedRelease attributes 收成一句中文；无数据则 None。"""
    if not attrs:
        return None
    state = (attrs.get("phasedReleaseState") or "").upper()
    if not state:
        return None
    label = _PHASED_STATE_LABEL.get(state, state)
    day = attrs.get("currentDayNumber")
    pct = phased_percent_for_day(day if day is not None else None)
    # 格式需与 parse_phased_fingerprint 一致：`分批：STATE 第N天`
    head = f"分批：{state}"
    if day is not None:
        head += f" 第{day}天"
        if pct is not None:
            head += f"≈{pct}%"
    head += f"（{label}）"
    extras: list[str] = []
    start = attrs.get("startDate")
    if start:
        extras.append(f"开始于{start}")
    pause = attrs.get("totalPauseDuration")
    if pause:
        extras.append(f"已暂停累计{pause}")
    if extras:
        return head + "；" + "；".join(extras)
    return head


def parse_phased_fingerprint(message: str | None) -> tuple[str | None, int | None]:
    """从 status message 里抽出 (phasedState, day)，供通知标题判断。"""
    if not message:
        return None, None
    m = _PHASED_IN_MSG_RE.search(message)
    if not m:
        return None, None
    day_raw = m.group("day")
    day = int(day_raw) if day_raw else None
    return m.group("state"), day


def ios_phased_notify_title(
    prev_message: str | None,
    new_message: str | None,
) -> str | None:
    """分批进度/暂停/完成变化时的专用标题；无关则 None。"""
    prev_state, prev_day = parse_phased_fingerprint(prev_message)
    new_state, new_day = parse_phased_fingerprint(new_message)
    if not new_state:
        return None
    if prev_state == new_state and prev_day == new_day:
        return None

    if new_state == "PAUSED" and prev_state != "PAUSED":
        return "iOS 分批发布已暂停"
    if new_state == "COMPLETE" and prev_state != "COMPLETE":
        return "iOS 分批发布已结束（已全量）"
    if new_state == "ACTIVE":
        if prev_state in {None, "INACTIVE"} and new_day is not None:
            pct = phased_percent_for_day(new_day)
            pct_note = f"，约{pct}%" if pct is not None else ""
            return f"iOS 已开始分批发布（第{new_day}天{pct_note}）"
        if prev_day is not None and new_day is not None and new_day != prev_day:
            pct = phased_percent_for_day(new_day)
            pct_note = f"，约{pct}%" if pct is not None else ""
            return f"iOS 分批进度更新（第{new_day}天{pct_note}）"
        if prev_state == "PAUSED":
            return "iOS 分批发布已恢复"
    return None


def extract_phased_attrs(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """从 relationship GET 或 include 响应里抽出 attributes。"""
    if not payload:
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        attrs = data.get("attributes")
        return attrs if isinstance(attrs, dict) else None
    if isinstance(data, list) and data:
        attrs = data[0].get("attributes")
        return attrs if isinstance(attrs, dict) else None
    # include 形态
    for item in payload.get("included") or []:
        if item.get("type") == "appStoreVersionPhasedReleases":
            attrs = item.get("attributes")
            return attrs if isinstance(attrs, dict) else None
    return None
