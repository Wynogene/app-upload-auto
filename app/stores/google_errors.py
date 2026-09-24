"""Map Google Play / network exceptions to short Chinese messages for CLI & Feishu."""

from __future__ import annotations

import re
from typing import Any

# 本地代理没开时最典型的几种报错（Windows / macOS / Linux 各自的措辞）
_PROXY_REFUSED_HINTS = (
    "unable to connect to proxy",
    "connection refused",
    "10061",
    "积极拒绝",
    "proxyerror",
)


def describe_transport_error(exc: BaseException) -> str | None:
    """网络 / 代理类故障 → 友好文案；不属于这类问题则返回 None。

    「代理没开」是本项目最常见的失败原因之一，所以单独识别出来，
    避免把一大段嵌套异常直接甩给用户。
    """
    text = str(exc)
    lower = text.lower()

    if any(h in lower for h in _PROXY_REFUSED_HINTS):
        return (
            "代理不可用（连接被拒绝）。请确认本地代理已开启；"
            "若当前不需要代理，可清空 .env 里的 HTTP_PROXY / HTTPS_PROXY 后重试。"
        )

    if "ssl" in lower or "ssleof" in lower or "eof occurred in violation" in lower:
        return (
            "网络 SSL 中断（直连 ASC/Play 时也可能因跨境链路抖动发生；"
            "与「必须开代理」无关）。稍后重试即可；持续失败再查本机网络。"
        )

    if (
        "10060" in text
        or "timed out" in lower
        or "timeout" in lower
        or "max retries exceeded" in lower
    ):
        return (
            "连接商店 API 超时（WinError 10060 / timeout）。"
            "iOS 默认直连 ASC，偶发跨境超时属网络抖动，稍后重试；"
            "Android 查 Play 仍依赖稳定代理。"
        )

    return None


def format_google_upload_error(exc: BaseException) -> str:
    text = str(exc)

    m = re.search(r"version code\s+(\d+)\s+has already been used", text, re.I)
    if m:
        return (
            f"versionCode {m.group(1)} 已被使用（该包已在 Play 应用库中）。"
            f"请换更高 versionCode 的新 AAB；若只需把已有版本推进轨道，用 "
            f"`release --version-code {m.group(1)}`。"
        )

    transport = describe_transport_error(exc)
    if transport:
        return transport

    lower = text.lower()

    if "403" in text and "permission" in lower:
        return f"权限不足（403）。请检查服务账号是否已在 Play Console 获得该应用的发布权限。详情: {_short(text)}"

    if "401" in text or "invalid_grant" in lower:
        return f"鉴权失败。请检查服务账号 JSON 是否有效。详情: {_short(text)}"

    return f"上传失败: {_short(text)}"


def format_google_status_error(exc: BaseException) -> str:
    """状态查询失败时的友好文案（不暴露原始异常堆栈）。"""
    transport = describe_transport_error(exc)
    if transport:
        return transport
    return f"状态查询失败: {_short(str(exc))}"


def is_transient_status_failure(
    *,
    state: str | None = None,
    message: str | None = None,
) -> bool:
    """盯盘用：代理/网络等瞬时查询失败，不应当成「发布状态变化」通知或落指纹。

    关闭/开启本机代理导致 Play 查不通时，message 会变成这类文案；若照常推送，
    会出现「比例没变却收到私聊」的误报。

    iOS 默认直连 ASC，仍可能因跨境链路超时 / SSL 抖动失败（与「必须开代理」无关）；
    这类 ``status 异常: WinError 10060`` / SSL EOF 同样应按瞬时失败处理。
    """
    del state  # 仅作扩展点；真实 UNKNOWN 与瞬时失败靠 message 区分
    msg = (message or "").strip()
    if not msg:
        return False
    markers = (
        "代理不可用",
        "网络/代理 SSL",
        "连接 Google Play API 失败",
        "状态查询失败:",
        "盯盘查询失败:",
        "status 异常:",
        "查询失败:",
        "ASC 查询瞬时失败",
    )
    if any(m in msg for m in markers):
        return True
    lower = msg.lower()
    if any(h in lower for h in _PROXY_REFUSED_HINTS):
        return True
    # 裸异常串（Apple status 尚未包装时）
    needles = (
        "10060",
        "10061",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "connection refused",
        "name or service not known",
        "getaddrinfo failed",
        "eof occurred in violation",
        "ssleof",
        "ssl:",
        "remote end closed",
        "broken pipe",
        "network is unreachable",
    )
    return any(n in lower for n in needles)


def _short(text: str, limit: int = 280) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def is_retryable_transport_error(exc: BaseException) -> bool:
    """True for transient proxy/SSL/connection failures worth retrying."""
    names = type(exc).__name__.lower()
    text = str(exc).lower()
    needles = (
        "ssl",
        "ssleof",
        "connection reset",
        "connection aborted",
        "remote end closed",
        "max retries exceeded",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "503",
        "502",
        "429",
    )
    if any(n in names for n in ("ssl", "connection", "timeout", "chunked")):
        return True
    return any(n in text for n in needles)


def collect_track_version_codes(tracks_payload: dict[str, Any] | None) -> set[str]:
    codes: set[str] = set()
    for track in (tracks_payload or {}).get("tracks") or []:
        for rel in track.get("releases") or []:
            for c in rel.get("versionCodes") or []:
                codes.add(str(c))
    return codes


def primary_release_on_track(
    tracks_payload: dict[str, Any] | None, track_name: str
) -> dict[str, Any] | None:
    """Return the first release dict on the named track, if any."""
    for track in (tracks_payload or {}).get("tracks") or []:
        if track.get("track") != track_name:
            continue
        releases = track.get("releases") or []
        return releases[0] if releases else None
    return None
