"""分阶段发布（staged rollout）参数解析与校验。

背景：Play Console 用「百分比」（10% / 50%），而 Google Play API 用
``userFraction``（0~1 的小数）。为了不让人两头换算，这里统一约定：

* CLI / 配置层：一律用**百分比**（``--rollout 10`` 表示 10%）
* 请求模型层：一律用**小数比例**（``rollout_fraction=0.1``）
* Google API 层：``userFraction`` = 小数比例

语义边界（对齐 Play Console）：

* 不传 / 传 100 -> 全面发布（``status=completed``，不带 ``userFraction``）
* 传 0 < pct < 100 -> 分阶段发布（``status=inProgress`` + ``userFraction``）

注意：分阶段发布只有 **production** 轨道支持，测试轨道传 ``userFraction``
会被 Google 拒绝，所以这里做前置校验，避免传完 200MB 才失败。
"""

from __future__ import annotations

# Google 要求 userFraction 严格落在 (0, 1) 开区间内。
MIN_FRACTION = 0.0001
MAX_FRACTION = 0.9999

STAGED_TRACKS = frozenset({"production"})


class RolloutSpecError(ValueError):
    """分阶段发布参数不合法。"""


def parse_rollout_percent(value: str | int | float | None) -> float | None:
    """把百分比解析成小数比例。

    * ``None`` / 空串 -> ``None``（未启用，走全面发布）
    * ``100`` / ``"100%"`` -> ``1.0``（显式全量，语义等同全面发布）
    * ``10`` / ``"10%"`` -> ``0.1``

    非法输入抛 :class:`RolloutSpecError`，由调用方转成友好文案。
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().rstrip("%").strip()
        if not text:
            return None
    else:
        text = str(value).strip()
        if not text:
            return None

    try:
        percent = float(text)
    except (TypeError, ValueError) as exc:
        raise RolloutSpecError(
            f"分阶段比例无法识别：{value!r}。请用百分比，例如 --rollout 10 表示 10%"
        ) from exc

    if percent <= 0:
        raise RolloutSpecError(f"分阶段比例必须大于 0：{value!r}")
    if percent > 100:
        raise RolloutSpecError(f"分阶段比例不能超过 100：{value!r}")

    if percent >= 100:
        return 1.0
    return round(percent / 100.0, 6)


def is_staged(fraction: float | None) -> bool:
    """是否是「真正的分阶段发布」（严格介于 0 与 100% 之间）。"""
    if fraction is None:
        return False
    return MIN_FRACTION <= fraction <= MAX_FRACTION


def release_status_for(fraction: float | None) -> str:
    """根据比例推导 Google API 的 release status。"""
    return "inprogress" if is_staged(fraction) else "completed"


def validate_rollout_track(fraction: float | None, track: str) -> str | None:
    """校验轨道是否支持分阶段发布，返回错误文案（无错误则 None）。"""
    if not is_staged(fraction):
        return None
    if (track or "").strip().lower() not in STAGED_TRACKS:
        return (
            f"分阶段发布只支持正式版轨道 `production`，当前轨道是 `{track}`。"
            f"测试轨道（internal/alpha/beta）只能全量发布。"
        )
    return None


def describe_rollout(fraction: float | None) -> str:
    """给用户看的中文描述。"""
    if fraction is None:
        return "全面发布"
    if fraction >= 1:
        return "全面发布（100%，结束分批）"
    return f"分阶段发布 {fraction * 100:g}%"


def compare_rollout(current: float | None, desired: float | None) -> int:
    """比较两次放量比例，供 release 防呆判断。

    返回 1 表示「desired 更大（需要推进）」，0 表示相等，-1 表示更小（需拦截）。
    ``None`` 视为 1.0（全面发布）。
    """
    cur = 1.0 if current is None else float(current)
    des = 1.0 if desired is None else float(desired)
    if des > cur:
        return 1
    if des < cur:
        return -1
    return 0
