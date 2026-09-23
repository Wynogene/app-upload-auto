"""运营向飞书通知正文：风格 D（字段对齐）。

盯盘「是否通知」指纹见 ``watch_fingerprint.ops_watch_fingerprint``（状态+放量）。
本模块只负责给人看的卡片正文。
不写商店。
"""

from __future__ import annotations

import re

from app.config import get_app_by_id
from app.core.notify_titles import parse_android_production_rollout
from app.models import OperationResult, Platform, ReviewState, ReviewStatus
from app.stores.apple_phased import parse_phased_fingerprint, phased_percent_for_day

_HINT_START_RE = re.compile(
    r"(?:进度以 Play Console|进度以 App Store Connect|请到 Play Console).*$",
    re.DOTALL,
)
_LIFECYCLE_RE = re.compile(
    r"lifecycle\[[^\]]+\]:\s*(?P<key>\w+)\s*\((?P<label>[^)]+)\)",
    re.IGNORECASE,
)
_ANDROID_NAME_RE = re.compile(
    r"production:\s+(?P<head>[^\n]*?)\s+codes=\[(?P<code>[^\]]+)\]",
    re.IGNORECASE,
)
_IOS_HEAD_RE = re.compile(
    r"^(?P<ver>[^：:]+)[：:]\s*(?P<label>[^；;]+)",
)
_IOS_BUILD_RE = re.compile(r"构建\s+(?P<build>[0-9.]+)")


def _strip_trailing_hint(message: str | None) -> str:
    if not message:
        return ""
    return _HINT_START_RE.sub("", message).strip()


def _row(label: str, value: str, width: int = 4) -> str:
    pad = max(0, width - len(label))
    return f"{label}{'　' * pad}　{value}"


def _block(rows: list[tuple[str, str]]) -> str:
    return "\n".join(_row(k, v) for k, v in rows if v and v != "-")


def _md_link(text: str, url: str) -> str:
    return f"[{text}]({url})"


def _android_entry(app_id: str) -> str:
    """Play Console 深链需要开发者/应用数字 ID；有自定义 URL 则用之，否则控制台首页。"""
    app = get_app_by_id(app_id) or {}
    android = app.get("android") or {}
    custom = (android.get("console_url") or "").strip()
    if custom:
        return _md_link("打开 Play Console", custom)
    return _md_link("打开 Play Console", "https://play.google.com/console/")


def _ios_entry(app_id: str) -> str:
    app = get_app_by_id(app_id) or {}
    ios = app.get("ios") or {}
    custom = (ios.get("console_url") or "").strip()
    if custom:
        return _md_link("打开 App Store Connect", custom)
    asc_id = str(ios.get("app_store_app_id") or "").strip()
    if asc_id:
        return _md_link(
            "打开 App Store Connect",
            f"https://appstoreconnect.apple.com/apps/{asc_id}",
        )
    return _md_link("打开 App Store Connect", "https://appstoreconnect.apple.com/")


def _android_rollout_text(frac: float | None, *, halted: bool = False) -> str:
    if frac is None:
        return "-"
    pct = f"{frac * 100:g}%"
    if halted:
        return f"约{pct}（已停发；是否恢复请到 Play Console）"
    if frac < 1:
        return f"约{pct}（调整放量请到 Play Console）"
    return "100%"


def _ios_rollout_text(
    day: int | None,
    pct: int | None,
    *,
    paused: bool = False,
    complete: bool = False,
) -> str:
    if complete:
        return "全量100%"
    day_note = f"第{day}天" if day is not None else "进行中"
    pct_note = f"≈{pct}%" if pct is not None else ""
    head = f"{day_note}{pct_note}" if pct_note else day_note
    if paused:
        return f"{head}（已暂停；是否恢复请到 ASC）"
    return f"{head}（Apple 按天自动抬升，不可自定义）"


def _android_fields(status: ReviewStatus) -> list[tuple[str, str]]:
    msg = _strip_trailing_hint(status.message)
    life = _LIFECYCLE_RE.search(msg)
    track_status, frac = parse_android_production_rollout(msg)

    version_name = ""
    mname = _ANDROID_NAME_RE.search(msg)
    if mname:
        head = (mname.group("head") or "").strip()
        mver = re.search(r"\(([^)]+)\)", head)
        if mver:
            version_name = mver.group(1).strip()
        elif head and not head.isdigit():
            version_name = head
    if not version_name:
        version_name = status.version_name or "-"

    state = "未知"
    rollout = "-"
    # 仅在「需要人处理」时写动作
    action = ""
    key = (life.group("key") or "").upper() if life else ""

    if key == "IN_REVIEW":
        state = "审核中"
    elif key == "APPROVED_NOT_PUBLISHED":
        state = "已过审，尚未对用户开放"
        action = "请到 Play Console 点「发布」"
    elif key == "NOT_APPROVED":
        state = "审核未通过"
        action = "请到 Play Console 查看原因"
    elif key == "PUBLISHED":
        if frac is not None and frac < 1:
            state = "已上架（分批）"
            rollout = _android_rollout_text(frac)
        else:
            state = "已上架（已全量）"
            rollout = "100%"
    elif track_status == "halted":
        state = "分批已停发"
        rollout = _android_rollout_text(frac, halted=True)
    elif frac is not None and frac < 1:
        state = "已上架（分批）"
        rollout = _android_rollout_text(frac)
    else:
        raw = status.state.value if status.state else ""
        state = {
            "in_review": "审核中",
            "waiting_for_review": "等待审核",
            "approved": "已过审",
            "rejected": "审核未通过",
            "released": "已上架",
            "halted": "已停发",
        }.get(raw, raw or "未知")
        if raw == "rejected":
            action = "请到 Play Console 查看原因"
        elif raw == "approved":
            action = "请确认是否需在 Console 手动发布"

    rows = [
        ("App", status.app_id),
        ("平台", "Android"),
        ("版本", version_name),
        ("状态", state),
        ("放量", rollout),
    ]
    if action:
        rows.append(("动作", action))
    rows.append(("入口", _android_entry(status.app_id)))
    return rows


def _ios_build(status: ReviewStatus, msg: str) -> str | None:
    raw = status.raw or {}
    build = raw.get("build") if isinstance(raw, dict) else None
    if isinstance(build, dict) and build.get("version"):
        return str(build.get("version")).strip()
    m = _IOS_BUILD_RE.search(msg)
    if m:
        return m.group("build").strip()
    return None


def _ios_fields(status: ReviewStatus) -> list[tuple[str, str]]:
    msg = _strip_trailing_hint(status.message)
    store_ver = status.version_name or "-"
    label = ""
    head_m = _IOS_HEAD_RE.match(msg)
    if head_m:
        store_ver = head_m.group("ver").strip() or store_ver
        label = head_m.group("label").strip()
    build = _ios_build(status, msg) or store_ver

    phased_state, day = parse_phased_fingerprint(msg)
    pct = phased_percent_for_day(day)

    state = label or "未知"
    rollout = "-"
    action = ""

    if "等待出口合规" in msg:
        state = "等待出口合规确认"
        action = "请到 ASC 完成出口合规"
    elif "等待合同" in msg:
        state = "等待合同/协议生效"
        action = "请到 ASC 处理合同"
    elif "构建处理超时" in msg:
        state = "构建长时间仍在处理"
        action = "请到 TestFlight 排查"
    elif "构建" in msg and ("无效" in msg or "处理失败" in msg):
        state = "构建无效或处理失败"
        action = "请检查签名/权限后重新上传"
    elif "手动发布" in msg and ("ASC" in msg or "等待你手动" in msg):
        state = "已过审，待手动发布"
        action = "请到 ASC 点发布"
    elif phased_state == "ACTIVE":
        state = "已上线（分批中）"
        rollout = _ios_rollout_text(day, pct)
    elif phased_state == "PAUSED":
        state = "分批已暂停"
        rollout = _ios_rollout_text(day, pct, paused=True)
    elif phased_state == "COMPLETE":
        state = "已上线（分批已结束）"
        rollout = _ios_rollout_text(day, pct, complete=True)
    elif status.state == ReviewState.REJECTED:
        state = "审核未通过"
        action = "请到 ASC 查看拒信"

    rows = [
        ("App", status.app_id),
        ("平台", "iOS"),
        ("版本", build),
        ("状态", state),
        ("放量", rollout),
    ]
    if action:
        rows.append(("动作", action))
    rows.append(("入口", _ios_entry(status.app_id)))
    return rows


def format_review_status_ops(status: ReviewStatus) -> str:
    """单条 ReviewStatus → 风格 D 字段表。"""
    if status.platform == Platform.IOS:
        return _block(_ios_fields(status))
    return _block(_android_fields(status))


def format_review_statuses_ops(statuses: list[ReviewStatus]) -> str:
    blocks = [format_review_status_ops(s) for s in statuses]
    return "\n\n---\n\n".join(blocks)


def _first_result_line(message: str | None) -> str:
    """成功卡只用首句结果，去掉盯盘附言 / 续推 / 长 HINT / 默认文案括号。"""
    if not message:
        return ""
    text = _strip_trailing_hint(message)
    for sep in (
        "\n已登记盯盘",
        "\n已登记 iOS 盯盘",
        "\n\n—— 下一刀",
        "\n\n—— 失败摘要",
        "\n确认稳定后可续推",
        "\n全量则用",
        "（版本说明使用默认文案",
        "。已按 ",
        "。建议立刻执行",
    ):
        if sep in text:
            text = text.split(sep, 1)[0]
    text = text.strip().rstrip("。").strip()
    return f"{text}。" if text else ""


def format_operation_result_ops(result: "OperationResult") -> str | None:
    """单条操作结果 → 飞书短字段；``notify_skip`` 则返回 None。"""
    details = result.details or {}
    if details.get("notify_skip") or details.get("synthetic_follow"):
        return None

    app_id = result.app_id or "-"
    plat = result.platform.value if result.platform else "-"

    if not result.ok:
        # 失败保留完整摘要（含失败指引）
        body = (result.message or "").strip() or "失败"
        return _block(
            [
                ("App", app_id),
                ("平台", "iOS" if plat == "ios" else ("Android" if plat == "android" else plat)),
                ("结果", "失败"),
            ]
        ) + f"\n\n{body}"

    rows: list[tuple[str, str]] = [
        ("App", app_id),
    ]
    if result.platform == Platform.IOS:
        rows.append(("平台", "iOS"))
        ver = (
            details.get("version_name")
            or details.get("version_string")
            or details.get("cfBundleShortVersionString")
        )
        build_id = details.get("build_id")
        ver_s = str(ver) if ver else ""
        if build_id:
            ver_s = f"{ver_s} · build `{build_id}`" if ver_s else f"build `{build_id}`"
        if ver_s:
            rows.append(("版本", ver_s))
        state = details.get("submission_state") or (
            result.review_state.value if result.review_state else ""
        )
        head = _first_result_line(result.message) or "成功"
        if state and state not in head:
            rows.append(("结果", f"{head.rstrip('。')}（{state}）"))
        else:
            rows.append(("结果", head))
        if details.get("watch_registered"):
            rows.append(("盯盘", "已登记（serve 会扫）"))
        rows.append(("入口", _ios_entry(app_id)))
    elif result.platform == Platform.ANDROID:
        rows.append(("平台", "Android"))
        vc = details.get("version_code")
        track = details.get("track") or "—"
        ver_s = f"{vc} @ {track}" if vc is not None else str(track)
        rows.append(("版本", ver_s))
        rollout = details.get("rollout")
        frac = details.get("rollout_fraction")
        if rollout:
            rows.append(("放量", str(rollout)))
        elif frac is not None:
            try:
                rows.append(("放量", f"约{float(frac) * 100:g}%"))
            except (TypeError, ValueError):
                pass
        rows.append(("结果", _first_result_line(result.message) or "成功"))
        if details.get("watch_registered"):
            rows.append(("盯盘", "已登记（旧正式轨已停；serve 会扫）"))
        rows.append(("入口", _android_entry(app_id)))
    else:
        rows.append(("平台", plat))
        rows.append(("结果", _first_result_line(result.message) or "成功"))

    return _block(rows)


def format_operation_results_ops(results: list) -> str:
    """飞书「操作结果」正文：成功短字段；跳过 synthetic follow；不加长 HINT footer。"""
    blocks: list[str] = []
    for r in results:
        if not isinstance(r, OperationResult):
            continue
        block = format_operation_result_ops(r)
        if block:
            blocks.append(block)
    return "\n\n---\n\n".join(blocks) if blocks else "无结果"
