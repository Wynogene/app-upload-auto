"""提审前：仅为「已存在的」App Store 本地化补全 what's New。

不会新建语言本地化；不会改构建 / 发布方式 / 分批；默认由 CLI dry-run，
只有显式 --apply 才 PATCH ASC。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# 通常仍可改元数据（含 what's New）的版本状态；其它状态只读列出，拒绝 apply
EDITABLE_VERSION_STATES = frozenset(
    {
        "PREPARE_FOR_SUBMISSION",
        "DEVELOPER_REJECTED",
        "REJECTED",
        "METADATA_REJECTED",
        "INVALID_BINARY",
    }
)


@dataclass(frozen=True)
class LocalizationRow:
    id: str
    locale: str
    whats_new: str


@dataclass(frozen=True)
class WhatsNewPlanItem:
    localization_id: str
    locale: str
    action: str  # fill | skip_has_text | skip_same | skip_no_text
    current: str
    planned: str


def parse_localization_rows(payload: dict[str, Any] | None) -> list[LocalizationRow]:
    rows: list[LocalizationRow] = []
    for item in (payload or {}).get("data") or []:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes") or {}
        loc_id = str(item.get("id") or "").strip()
        locale = str(attrs.get("locale") or "").strip()
        if not loc_id or not locale:
            continue
        rows.append(
            LocalizationRow(
                id=loc_id,
                locale=locale,
                whats_new=str(attrs.get("whatsNew") or ""),
            )
        )
    return rows


def resolve_text_for_locale(
    locale: str,
    *,
    text_by_locale: dict[str, str],
    fallback_text: str,
) -> str:
    """优先精确语言；否则用统一 fallback（运营常用：各语言先填同一英文说明）。"""
    if locale in text_by_locale and text_by_locale[locale].strip():
        return text_by_locale[locale].strip()
    # en-US / en 等宽松匹配
    lower = {k.lower(): v for k, v in text_by_locale.items() if (v or "").strip()}
    if locale.lower() in lower:
        return lower[locale.lower()].strip()
    return (fallback_text or "").strip()


def plan_whats_new_updates(
    rows: list[LocalizationRow],
    *,
    text_by_locale: dict[str, str],
    fallback_text: str,
    fill_empty_only: bool = True,
) -> list[WhatsNewPlanItem]:
    """只针对已有本地化行生成计划；绝不暗示「去新建未本地化语言」。"""
    planned: list[WhatsNewPlanItem] = []
    for row in rows:
        desired = resolve_text_for_locale(
            row.locale,
            text_by_locale=text_by_locale,
            fallback_text=fallback_text,
        )
        current = (row.whats_new or "").strip()
        if not desired:
            planned.append(
                WhatsNewPlanItem(
                    localization_id=row.id,
                    locale=row.locale,
                    action="skip_no_text",
                    current=current,
                    planned="",
                )
            )
            continue
        if fill_empty_only and current:
            planned.append(
                WhatsNewPlanItem(
                    localization_id=row.id,
                    locale=row.locale,
                    action="skip_has_text",
                    current=current,
                    planned=desired,
                )
            )
            continue
        if current == desired:
            planned.append(
                WhatsNewPlanItem(
                    localization_id=row.id,
                    locale=row.locale,
                    action="skip_same",
                    current=current,
                    planned=desired,
                )
            )
            continue
        planned.append(
            WhatsNewPlanItem(
                localization_id=row.id,
                locale=row.locale,
                action="fill",
                current=current,
                planned=desired,
            )
        )
    return planned


def summarize_plan(items: list[WhatsNewPlanItem]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item.action] = out.get(item.action, 0) + 1
    return out
