"""Google Play 版本说明（releaseNotes）的解析与校验。

设计要点：
  * API 里 `releaseNotes` 是**可选**的。省略 = 不显示版本说明；绝不能替用户编一句话，
    否则正式版会把工具生成的文案直接展示给真实用户。
  * 正式轨道（production）强制要求显式提供，内测轨可省略。
  * 支持多语言：`zh-CN=...` / `en-US=...`；纯文本则套用该 App 配置的默认语言列表。
  * 语言标签必须与该 App 在 Play 上支持的语言一致，否则会被拒或备注不显示，
    因此默认语言做成「每个 App 可配置」而不是写死。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import get_settings

# Google Play 单语言版本说明上限
MAX_CHARS_PER_LOCALE = 500

# 形如 zh-CN=文本 / en-US=文本；左侧是语言标签
_LOCALE_PREFIX_RE = re.compile(r"^\s*([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?)\s*=\s*(.*)$", re.S)

DEFAULT_LOCALES = ["en-US"]

ERROR = "error"
WARN = "warn"


@dataclass(frozen=True)
class NotesIssue:
    code: str
    level: str
    message: str

    @property
    def is_error(self) -> bool:
        return self.level == ERROR


class NotesSpecError(ValueError):
    """--whats-new 传参本身有问题（格式/歧义），在调用 API 前就应拦下。"""


def parse_notes_spec(
    raw_values: list[str] | None,
    default_locales: list[str] | None = None,
) -> list[dict[str, str]] | None:
    """把 CLI 传入的多个 --whats-new 解析成 releaseNotes 数组。

    规则：
      * 全部为 `locale=文本` → 按各自语言; 出现重复语言视为冲突
      * 单个纯文本 → 套用到 default_locales 的每一种语言
      * 混用两者、或给多个纯文本 → 语义有歧义，直接报错
    返回 None 表示「用户没提供」，调用方应据此决定是否要求必填 / 是否省略字段。
    """
    values = [v for v in (raw_values or []) if v is not None and str(v).strip()]
    if not values:
        return None

    default_locales = [lo for lo in (default_locales or DEFAULT_LOCALES) if lo]
    if not default_locales:
        default_locales = list(DEFAULT_LOCALES)

    scoped: dict[str, str] = {}
    plain: list[str] = []

    for raw in values:
        m = _LOCALE_PREFIX_RE.match(raw)
        # 只有当「=」左边确实像语言标签时才当成分语言写法，
        # 否则 "修复 a=b 的问题" 这类正常文案会被误判。
        if m and _looks_like_locale(m.group(1)):
            locale = _normalize_locale(m.group(1))
            text = m.group(2).strip()
            if not text:
                raise NotesSpecError(f"`{locale}` 的版本说明为空")
            if locale in scoped:
                raise NotesSpecError(f"语言 `{locale}` 被重复指定")
            scoped[locale] = text
        else:
            plain.append(raw.strip())

    if scoped and plain:
        raise NotesSpecError(
            "不要混用「纯文本」与「语言=文本」两种写法。"
            "要么给一种纯文本（套用所有默认语言），要么逐个语言指定。"
        )

    if plain:
        if len(plain) > 1:
            raise NotesSpecError(
                "多个纯文本无法判断各自对应哪种语言，请改成 `zh-CN=文本` 形式"
            )
        return [{"language": lo, "text": plain[0]} for lo in default_locales]

    return [{"language": lo, "text": txt} for lo, txt in scoped.items()]


def _looks_like_locale(value: str) -> bool:
    """过滤掉把普通文案误判成语言标签的情况。

    语言标签形如 zh / zh-CN / en-US / pt-BR，长度很短且不含空格。
    """
    v = value.strip()
    if " " in v or len(v) > 10:
        return False
    return bool(re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", v))


def _normalize_locale(value: str) -> str:
    """zh-cn → zh-CN；en → en。Google 要求语言-地区的大小写规范。"""
    parts = value.strip().split("-")
    if len(parts) == 1:
        return parts[0].lower()
    return f"{parts[0].lower()}-{parts[1].upper()}"


def validate_notes(
    notes: list[dict[str, str]] | None,
    *,
    track: str,
    is_production: bool = False,
) -> list[NotesIssue]:
    """校验版本说明是否满足要求。notes 为 None 表示用户未提供。"""
    issues: list[NotesIssue] = []

    if not notes:
        if is_production or track in {"production"}:
            issues.append(
                NotesIssue(
                    "notes_required",
                    ERROR,
                    "正式版缺少版本说明：既没有传 --whats-new，也没有配置默认文案。"
                    "请传 --whats-new \"...\"，或在 apps.yaml 的 android.release_notes_default "
                    "（或 .env 的 RELEASE_NOTES_DEFAULT）配置运营确认过的默认文案。",
                )
            )
        return issues

    seen: set[str] = set()
    for item in notes:
        locale = item.get("language") or ""
        text = item.get("text") or ""
        if not locale:
            issues.append(
                NotesIssue("notes_missing_locale", ERROR, "版本说明缺少语言标签")
            )
            continue
        if locale in seen:
            issues.append(
                NotesIssue("notes_dup_locale", ERROR, f"语言 `{locale}` 重复")
            )
        seen.add(locale)
        if not text.strip():
            issues.append(
                NotesIssue("notes_empty_text", ERROR, f"语言 `{locale}` 的版本说明为空")
            )
        elif len(text) > MAX_CHARS_PER_LOCALE:
            issues.append(
                NotesIssue(
                    "notes_too_long",
                    ERROR,
                    f"语言 `{locale}` 的版本说明 {len(text)} 字符，"
                    f"超过 Google Play 上限 {MAX_CHARS_PER_LOCALE} 字符",
                )
            )

    return issues


def has_errors(issues: list[NotesIssue]) -> bool:
    return any(i.is_error for i in issues)


def format_issues(issues: list[NotesIssue]) -> str:
    if not issues:
        return ""
    lines = [f"[阻断] {i.message}" for i in issues if i.is_error]
    lines += [f"[提示] {i.message}" for i in issues if not i.is_error]
    return "\n".join(lines)


def resolve_default_locales(app_cfg: dict | None) -> list[str]:
    """取默认版本说明语言：apps.yaml > .env > 内置 en-US。"""
    android = (app_cfg or {}).get("android") or {}
    configured = android.get("release_notes_locales")
    if isinstance(configured, list):
        out = [str(x).strip() for x in configured if str(x).strip()]
        if out:
            return out
    # 全局兜底（.env: RELEASE_NOTES_LOCALES=en-US,zh-CN）
    global_raw = (get_settings().release_notes_locales or "").strip()
    if global_raw:
        out = [lo.strip() for lo in global_raw.split(",") if lo.strip()]
        if out:
            return [_normalize_locale(lo) for lo in out]
    return list(DEFAULT_LOCALES)


def resolve_default_text(app_cfg: dict | None) -> str | None:
    """取默认版本说明文案：apps.yaml > .env > 无。

    这是「运营已经确认过的默认文案」，与「工具自己编造」有本质区别：
    必须是显式配置进来的，才会被使用。
    运营手工上传时用的就是这条：
        -General: Bug fixes and system optimizations.
    """
    android = (app_cfg or {}).get("android") or {}
    configured = android.get("release_notes_default")
    if configured is not None and str(configured).strip():
        return str(configured).strip()
    global_raw = (get_settings().release_notes_default or "").strip()
    return global_raw or None


EXPLICIT = "explicit"
DEFAULT = "default"


def build_release_notes(
    raw_values: list[str] | None,
    app_cfg: dict | None,
) -> tuple[list[dict[str, str]] | None, str | None]:
    """产出最终要提交的 releaseNotes 与其来源。

    返回 (notes, source)：
      * 用户显式给了 --whats-new            → source="explicit"
      * 未给，但配置了默认文案              → source="default"（套用到默认语言）
      * 都未提供                            → (None, None)，调用方据此决定是否必填
    """
    notes = parse_notes_spec(raw_values, resolve_default_locales(app_cfg))
    if notes:
        return notes, EXPLICIT

    default_text = resolve_default_text(app_cfg)
    if default_text:
        return (
            [
                {"language": lo, "text": default_text}
                for lo in resolve_default_locales(app_cfg)
            ],
            DEFAULT,
        )

    return None, None
