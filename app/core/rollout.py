"""分阶段发布（staged rollout）参数解析与校验。

背景：Play Console 用「百分比」（10% / 50%），而 Google Play API 用
``userFraction``（0~1 的小数）。为了不让人两头换算，这里统一约定：

* CLI / 配置层：一律用**百分比**（``--rollout 10`` 表示 10%）
* 请求模型层：一律用**小数比例**（``rollout_fraction=0.1``）
* Google API 层：``userFraction`` = 小数比例

语义边界（对齐 Play Console）：

* 传 100 -> 全面发布（``status=completed``，不带 ``userFraction``）
* 传 0 < pct < 100 -> 分阶段发布（``status=inProgress`` + ``userFraction``）
* **正式版不传** -> 使用配置默认比例（默认 5%，见 ``resolve_rollout_fraction``）
* 测试轨道不传 -> 全量（测试轨不支持分批）

注意：分阶段发布只有 **production** 轨道支持，测试轨道传 ``userFraction``
会被 Google 拒绝，所以这里做前置校验，避免传完 200MB 才失败。
"""

from __future__ import annotations

# Google 要求 userFraction 严格落在 (0, 1) 开区间内。
MIN_FRACTION = 0.0001
MAX_FRACTION = 0.9999

STAGED_TRACKS = frozenset({"production"})

# 正式版未显式传 --rollout 时的内置默认百分比（可被 apps.yaml / .env 覆盖）。
BUILTIN_DEFAULT_PERCENT = 5.0


class RolloutSpecError(ValueError):
    """分阶段发布参数不合法。"""


def parse_rollout_percent(value: str | int | float | None) -> float | None:
    """把百分比解析成小数比例。

    * ``None`` / 空串 -> ``None``（未启用，由调用方决定是否套默认）
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


def resolve_default_rollout_percent(app_cfg: dict | None) -> float | None:
    """读取配置层的默认百分比。

    优先级：apps.yaml ``android.rollout_percent_default`` >
    ``.env`` ``ROLLOUT_PERCENT_DEFAULT`` > 内置 5。

    * 返回 ``None`` 表示「不要默认分批」（走全面发布），仅当配置显式留空字符串时。
    * 返回数字（如 ``5.0`` / ``100.0``）供 ``parse_rollout_percent`` 再转小数。
    """
    android = (app_cfg or {}).get("android") or {}
    raw = android.get("rollout_percent_default", None)
    if raw is None:
        from app.config import get_settings

        raw = get_settings().rollout_percent_default
    if raw is None:
        return BUILTIN_DEFAULT_PERCENT
    if isinstance(raw, str) and not raw.strip():
        # 显式空串 = 关闭默认分批，恢复「不传即全量」
        return None
    try:
        percent = float(str(raw).strip().rstrip("%"))
    except (TypeError, ValueError) as exc:
        raise RolloutSpecError(
            f"默认分批比例无法识别：{raw!r}。"
            f"请在 apps.yaml 的 android.rollout_percent_default "
            f"或 .env 的 ROLLOUT_PERCENT_DEFAULT 填入 1~100 的数字。"
        ) from exc
    if percent <= 0 or percent > 100:
        raise RolloutSpecError(
            f"默认分批比例必须在 (0, 100]：当前配置为 {raw!r}"
        )
    return percent


def resolve_rollout_fraction(
    *,
    explicit: float | None,
    track: str,
    app_cfg: dict | None = None,
    release_status: str | None = None,
) -> tuple[float | None, str | None]:
    """解析最终要用的放量比例。

    返回 ``(fraction, source)``：
    * source = ``explicit``：CLI / 请求显式传入
    * source = ``default``：套用配置默认（正式版）
    * source = ``None``：无分批（全面发布，或测试轨 / draft / halted）

    ``draft`` / ``halted`` 不套默认分批：它们有自己的语义，由调用方单独处理。
    """
    if explicit is not None:
        return explicit, "explicit"

    status = (release_status or "").strip().lower()
    if status in {"draft", "halted"}:
        return None, None

    if (track or "").strip().lower() not in STAGED_TRACKS:
        return None, None

    default_pct = resolve_default_rollout_percent(app_cfg)
    if default_pct is None:
        return None, None
    return parse_rollout_percent(default_pct), "default"


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
