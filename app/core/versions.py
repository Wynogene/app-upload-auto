"""版本号比较工具（通用，不限于某个商店）。

注意：版本号**不能**只按「整数部分」比较，也不能假设固定分段数。
真实例子：
  * blurams  线上 5.1049.125   → 3 段
  * easelife 历史 5.1054.48.1  → 4 段
  * easelife 构建号 5.1054.51.1 → 4 段
所以按 '.' 切分后逐段比，段数多的补 0。
"""

from __future__ import annotations

import re

_NUM_RE = re.compile(r"^\d+$")


def parse_version_tuple(value: str | None) -> tuple[int, ...]:
    """把 '5.1049.125' 解析为 (5, 1049, 125)。

    非数字段（如 'beta'）按 0 处理，保证不抛异常——只用于「是否往前走了」的判断，
    不做严格语义校验。
    """
    if not value:
        return ()
    parts = str(value).strip().split(".")
    out: list[int] = []
    for p in parts:
        p = p.strip()
        if _NUM_RE.match(p):
            out.append(int(p))
        else:
            # 形如 '1a' / 'beta'：取前导数字，取不到则记 0
            m = re.match(r"^(\d+)", p)
            out.append(int(m.group(1)) if m else 0)
    return tuple(out)


def compare_versions(a: str | None, b: str | None) -> int:
    """返回值语义同 cmp：a<b 返回 -1，相等 0，a>b 返回 1。"""
    ta, tb = parse_version_tuple(a), parse_version_tuple(b)
    n = max(len(ta), len(tb))
    ta = ta + (0,) * (n - len(ta))
    tb = tb + (0,) * (n - len(tb))
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


def is_version_greater(candidate: str | None, baseline: str | None) -> bool:
    """candidate 是否严格大于 baseline。baseline 为空视为通过。"""
    if not baseline:
        return True
    return compare_versions(candidate, baseline) > 0
