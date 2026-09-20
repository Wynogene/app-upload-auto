"""盯盘变化指纹：按运营关心的「状态 + 放量」去重，忽略 API 原文噪音。"""

from __future__ import annotations

from app.core.notify_titles import parse_android_production_rollout
from app.models import Platform, ReviewStatus
from app.stores.apple_phased import parse_phased_fingerprint, phased_percent_for_day

OPS_FP_PREFIX = "ops_v1|"


def ops_watch_fingerprint(status: ReviewStatus) -> str:
    """生成盯盘比对指纹。

    变化通知仅在 ReviewState / 轨道放量相关字段变化时触发。
    不含 lifecycle 报错原文、全轨道列表、控制台提示等噪音。
    """
    state = status.state.value if status.state else "unknown"
    plat = status.platform.value if status.platform else "?"
    msg = status.message or ""

    if status.platform == Platform.ANDROID:
        track_st, frac = parse_android_production_rollout(msg)
        # 归一化 status 大小写；比例用稳定小数串
        st = (track_st or "-").lower()
        frac_s = f"{frac:.6g}" if frac is not None else "-"
        return f"{OPS_FP_PREFIX}{state}|android|{st}|{frac_s}"

    if status.platform == Platform.IOS:
        phased, day = parse_phased_fingerprint(msg)
        pct = phased_percent_for_day(day) if day is not None else None
        # 用户约定：放量百分比不变不通知 → 用 Apple 曲线百分比，不用「第 N 天」原文噪音
        phased_s = (phased or "-").upper()
        pct_s = str(pct) if pct is not None else "-"
        return f"{OPS_FP_PREFIX}{state}|ios|{phased_s}|{pct_s}"

    return f"{OPS_FP_PREFIX}{state}|{plat}|-|-"


def is_ops_fingerprint(fp: str | None) -> bool:
    return bool(fp) and str(fp).startswith(OPS_FP_PREFIX)


def synthesize_message_from_ops_fp(fp: str) -> str:
    """从 ops 指纹还原一段可供标题函数解析的伪 message（仅用于标题）。"""
    if not is_ops_fingerprint(fp):
        # 旧指纹：state|full_message
        if fp and "|" in fp:
            return fp.split("|", 1)[1]
        return fp or ""

    parts = fp.split("|")
    # ops_v1 | state | platform | ...
    if len(parts) < 4:
        return ""
    platform = parts[2]
    if platform == "android" and len(parts) >= 5:
        track_st, frac_s = parts[3], parts[4]
        if frac_s != "-":
            return (
                f"production: x codes=[1] status={track_st} "
                f"rollout={frac_s} ← target"
            )
        return f"production: x codes=[1] status={track_st} ← target"
    if platform == "ios" and len(parts) >= 5:
        phased, pct_s = parts[3], parts[4]
        # 标题侧用 parse_phased_fingerprint；补一天号方便文案（按百分比反查）
        day = None
        if pct_s != "-":
            try:
                pct_i = int(pct_s)
                from app.stores.apple_phased import PHASED_DAY_PERCENT

                for d, p in PHASED_DAY_PERCENT.items():
                    if p == pct_i:
                        day = d
                        break
            except ValueError:
                day = None
        if day is not None:
            return f"分批：{phased} 第{day}天"
        return f"分批：{phased}"
    return ""


def ops_fp_state(fp: str | None) -> str:
    if not fp:
        return ""
    if is_ops_fingerprint(fp):
        parts = fp.split("|")
        return parts[1] if len(parts) > 1 else ""
    if "|" in fp:
        return fp.split("|", 1)[0]
    return fp
