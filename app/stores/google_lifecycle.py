"""Google Play releaseLifecycleState（只读）——抓住「过审」时刻。

背景：旧的 ``edits.tracks`` 只有 draft/inProgress/completed。分批上架后仍是
``inProgress``，无法区分「审核中」与「已对用户放量」。

新接口（只读 GET，不建 edit、不 commit）::

    GET /androidpublisher/v3/applications/{package}/tracks/{track}/releases

返回 ``releaseLifecycleState``，可覆盖两种运营模式：

1. **自管式开启**：``IN_REVIEW`` → ``APPROVED_NOT_PUBLISHED``
   （过审、尚未对用户开放）
2. **自管式关闭**：``IN_REVIEW`` → ``PUBLISHED``
   （过审并自动上架，含分批）

文档：
https://developers.google.com/android-publisher/api-ref/rest/v3/applications.tracks.releases
"""

from __future__ import annotations

from typing import Any

from app.models import ReviewState

_LIFECYCLE_PREFIX = "RELEASE_LIFECYCLE_STATE_"

# 归一化后的短名 → ReviewState
_LIFECYCLE_STATE_MAP: dict[str, ReviewState] = {
    "DRAFT": ReviewState.DRAFT,
    "NOT_SENT_FOR_REVIEW": ReviewState.DRAFT,
    "IN_REVIEW": ReviewState.IN_REVIEW,
    "APPROVED_NOT_PUBLISHED": ReviewState.APPROVED,
    "NOT_APPROVED": ReviewState.REJECTED,
    "PUBLISHED": ReviewState.RELEASED,
}

_LIFECYCLE_DESC: dict[str, str] = {
    "DRAFT": "草稿（未送审）",
    "NOT_SENT_FOR_REVIEW": "已就绪、尚未送审",
    "IN_REVIEW": "审核中",
    "APPROVED_NOT_PUBLISHED": "已过审、尚未对用户开放（自管式待发布）",
    "NOT_APPROVED": "审核未通过",
    "PUBLISHED": "已上架对用户开放（含分批放量）",
}

TRACKS_RELEASES_URL = (
    "https://androidpublisher.googleapis.com/androidpublisher/v3"
    "/applications/{package}/tracks/{track}/releases"
)


def normalize_lifecycle(raw: str | None) -> str | None:
    """``RELEASE_LIFECYCLE_STATE_IN_REVIEW`` → ``IN_REVIEW``。"""
    if not raw:
        return None
    text = str(raw).strip().upper()
    if text.startswith(_LIFECYCLE_PREFIX):
        text = text[len(_LIFECYCLE_PREFIX) :]
    return text or None


def map_lifecycle_to_review_state(raw: str | None) -> ReviewState:
    key = normalize_lifecycle(raw)
    if not key:
        return ReviewState.UNKNOWN
    return _LIFECYCLE_STATE_MAP.get(key, ReviewState.UNKNOWN)


def describe_lifecycle(raw: str | None) -> str:
    key = normalize_lifecycle(raw)
    if not key:
        return "生命周期未知"
    return _LIFECYCLE_DESC.get(key, f"生命周期 {key}")


def version_codes_of(release: dict[str, Any]) -> list[str]:
    """从 ReleaseSummary 提取 versionCode 列表。"""
    out: list[str] = []
    for art in release.get("activeArtifacts") or []:
        vc = art.get("versionCode")
        if vc is not None:
            out.append(str(vc))
    # 兼容万一将来字段变化
    for vc in release.get("versionCodes") or []:
        out.append(str(vc))
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def find_release_for_version(
    releases: list[dict[str, Any]],
    version_code: str | int | None,
) -> dict[str, Any] | None:
    if version_code is None:
        return releases[0] if releases else None
    target = str(version_code)
    for rel in releases:
        if target in version_codes_of(rel):
            return rel
    return None


def approval_notify_title(
    previous: ReviewState | str | None,
    current: ReviewState | str | None,
) -> str | None:
    """若本次变化属于「过审/上架」关键跳变，返回专用飞书标题；否则 None。"""
    prev = previous.value if isinstance(previous, ReviewState) else (previous or "")
    cur = current.value if isinstance(current, ReviewState) else (current or "")
    prev = (prev or "").lower()
    cur = (cur or "").lower()

    reviewing = {ReviewState.IN_REVIEW.value, ReviewState.WAITING_FOR_REVIEW.value}

    # 自管式：过审但不对用户开放
    if prev in reviewing and cur == ReviewState.APPROVED.value:
        return "审核已通过（自管式：尚未对用户开放）"

    # 自管式关闭：过审并自动上架；或自管式解除后点了发布
    if cur == ReviewState.RELEASED.value:
        if prev in reviewing:
            return "审核已通过并已上架（含分批放量）"
        if prev == ReviewState.APPROVED.value:
            return "已发布上架（自管式发布后对用户开放）"

    # 被拒
    if prev in reviewing and cur == ReviewState.REJECTED.value:
        return "审核未通过"

    return None


def list_track_releases(
    session: Any,
    *,
    package_name: str,
    track: str = "production",
    timeout: int = 60,
) -> list[dict[str, Any]]:
    """只读拉取轨道 releases（含 releaseLifecycleState）。不写任何 edit。"""
    url = TRACKS_RELEASES_URL.format(package=package_name, track=track)
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json() if resp.content else {}
    return list(data.get("releases") or [])
