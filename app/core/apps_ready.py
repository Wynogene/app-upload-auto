"""多 App 真上传前就绪检查（只读：查配置/密钥/可选 status，不写商店）。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.config import ROOT_DIR, get_settings, load_apps_config
from app.core.watch_targets import active_targets


@dataclass
class CheckItem:
    app_id: str
    platform: str
    ok: bool
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _sa_abs(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _apple_key_path(ios: dict[str, Any]) -> Path | None:
    raw = (ios.get("private_key_path") or "").strip()
    if not raw:
        raw = (get_settings().apple_private_key_path or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def check_android_config(app: dict[str, Any]) -> list[CheckItem]:
    app_id = str(app.get("id") or "")
    android = app.get("android") or {}
    if not android.get("enabled", True):
        return [
            CheckItem(
                app_id,
                "android",
                True,
                "disabled",
                "android.enabled=false，跳过",
            )
        ]
    items: list[CheckItem] = []
    pkg = (android.get("package_name") or "").strip()
    if not pkg:
        items.append(
            CheckItem(app_id, "android", False, "missing_package", "缺少 package_name")
        )
    else:
        items.append(
            CheckItem(
                app_id,
                "android",
                True,
                "package_ok",
                f"package_name={pkg}",
                {"package_name": pkg},
            )
        )
    sa_raw = str(android.get("service_account_json") or "").strip()
    if not sa_raw:
        items.append(
            CheckItem(app_id, "android", False, "missing_sa", "缺少 service_account_json")
        )
    else:
        sa_path = _sa_abs(sa_raw)
        if not sa_path.exists():
            items.append(
                CheckItem(
                    app_id,
                    "android",
                    False,
                    "sa_missing_file",
                    f"SA 文件不存在: {sa_raw}",
                )
            )
        else:
            items.append(
                CheckItem(
                    app_id,
                    "android",
                    True,
                    "sa_ok",
                    f"SA 文件存在: {sa_raw}",
                    {"path": sa_raw},
                )
            )
    return items


def check_ios_config(app: dict[str, Any]) -> list[CheckItem]:
    app_id = str(app.get("id") or "")
    ios = app.get("ios") or {}
    if not ios.get("enabled", True):
        return [
            CheckItem(app_id, "ios", True, "disabled", "ios.enabled=false，跳过")
        ]
    items: list[CheckItem] = []
    bundle = (ios.get("bundle_id") or "").strip()
    asc_id = str(ios.get("app_store_app_id") or "").strip()
    if not bundle:
        items.append(
            CheckItem(app_id, "ios", False, "missing_bundle", "缺少 bundle_id")
        )
    else:
        items.append(
            CheckItem(
                app_id,
                "ios",
                True,
                "bundle_ok",
                f"bundle_id={bundle}",
                {"bundle_id": bundle},
            )
        )
    if not asc_id:
        items.append(
            CheckItem(
                app_id,
                "ios",
                False,
                "missing_asc_id",
                "缺少 app_store_app_id",
            )
        )
    else:
        items.append(
            CheckItem(
                app_id,
                "ios",
                True,
                "asc_id_ok",
                f"app_store_app_id={asc_id}",
                {"app_store_app_id": asc_id},
            )
        )

    key_id = (ios.get("key_id") or "").strip() or (get_settings().apple_key_id or "")
    issuer = (ios.get("issuer_id") or "").strip() or (
        get_settings().apple_issuer_id or ""
    )
    key_path = _apple_key_path(ios)
    has_app_override = bool(
        (ios.get("key_id") or "").strip()
        or (ios.get("issuer_id") or "").strip()
        or (ios.get("private_key_path") or "").strip()
    )
    # 独立 Play/Apple 主体：不得静默复用 .env 全局密钥（易 404）
    separate_subject = str(app.get("play_console_account") or "") in {
        "boykeep_separate",
    } or app_id == "boykeep"
    if separate_subject and not has_app_override:
        items.append(
            CheckItem(
                app_id,
                "ios",
                False,
                "apple_needs_team_override",
                "独立 Apple 团队：请在 apps.yaml 的 ios.key_id / issuer_id / "
                "private_key_path 填本团队凭据，勿复用 blurams/easelife 的 .env 全局密钥。"
                "见 docs/MULTI_APP_READY.md。",
            )
        )
    elif not key_id or not issuer or not key_path or not key_path.exists():
        items.append(
            CheckItem(
                app_id,
                "ios",
                False,
                "apple_cred_incomplete",
                "Apple 凭据不完整（key_id / issuer_id / .p8）。"
                + (
                    " boykeep 等独立主体必须在 apps.yaml ios.* 填本团队凭据。"
                    if app_id == "boykeep"
                    else " 请检查 .env 全局 APPLE_*。"
                ),
                {"has_app_override": has_app_override},
            )
        )
    else:
        src = "app" if has_app_override else "env"
        items.append(
            CheckItem(
                app_id,
                "ios",
                True,
                "apple_cred_present",
                f"Apple 凭据文件齐全（source={src}, key={key_path.name}）",
                {"cred_source": src, "key_file": key_path.name},
            )
        )
    return items


def check_ios_visibility(app: dict[str, Any]) -> CheckItem:
    """只读调用 ASC /v1/apps，确认目标 App 对当前密钥可见。"""
    from app.stores.apple import AppleStoreClient

    app_id = str(app.get("id") or "")
    ios = app.get("ios") or {}
    if not ios.get("enabled", True):
        return CheckItem(app_id, "ios", True, "disabled", "ios.enabled=false，跳过")
    info = AppleStoreClient().check_connectivity(app)
    if not info.get("ok"):
        return CheckItem(
            app_id,
            "ios",
            False,
            "asc_connect_fail",
            str(info.get("message") or "ASC 连通失败"),
            {"raw": info},
        )
    visible = info.get("target_app_visible")
    if visible is False:
        return CheckItem(
            app_id,
            "ios",
            False,
            "asc_app_not_visible",
            str(
                info.get("warning")
                or "app_store_app_id 不在当前密钥可见列表（多为另一 Apple 团队）"
            ),
            {
                "app_store_app_id": ios.get("app_store_app_id"),
                "visible_count": len(info.get("apps") or []),
            },
        )
    return CheckItem(
        app_id,
        "ios",
        True,
        "asc_app_visible",
        "目标 App 对当前 Apple 密钥可见",
        {"visible_count": len(info.get("apps") or [])},
    )


def check_watch_coverage(app_ids: list[str]) -> list[CheckItem]:
    active = active_targets()
    by_app: dict[str, set[str]] = {}
    for t in active:
        by_app.setdefault(t.app_id, set()).add(t.platform)
    items: list[CheckItem] = []
    for app_id in app_ids:
        plats = by_app.get(app_id) or set()
        if not plats:
            items.append(
                CheckItem(
                    app_id,
                    "watch",
                    False,
                    "no_active_watch",
                    "无活跃盯盘目标（可用 watch --once 登记，只读）",
                )
            )
        else:
            items.append(
                CheckItem(
                    app_id,
                    "watch",
                    True,
                    "watch_ok",
                    f"活跃盯盘平台: {', '.join(sorted(plats))}",
                    {"platforms": sorted(plats)},
                )
            )
    return items


def run_apps_ready(
    *,
    include_disabled: bool = False,
    probe_asc: bool = True,
    app_ids: list[str] | None = None,
) -> list[CheckItem]:
    """汇总配置 +（可选）ASC 可见性 + 盯盘覆盖。不写商店。"""
    wanted = {a.strip() for a in (app_ids or []) if a and a.strip()}
    items: list[CheckItem] = []
    enabled_ids: list[str] = []
    for app in load_apps_config().get("apps") or []:
        app_id = str(app.get("id") or "")
        if wanted and app_id not in wanted:
            continue
        android_on = bool((app.get("android") or {}).get("enabled", True))
        ios_on = bool((app.get("ios") or {}).get("enabled", True))
        if not include_disabled and not android_on and not ios_on:
            continue
        enabled_ids.append(app_id)
        items.extend(check_android_config(app))
        items.extend(check_ios_config(app))
        if probe_asc and ios_on:
            items.append(check_ios_visibility(app))
    items.extend(check_watch_coverage(enabled_ids))
    return items


def format_apps_ready(items: list[CheckItem]) -> str:
    lines: list[str] = ["# 多 App 就绪检查（只读，不写商店）", ""]
    by_app: dict[str, list[CheckItem]] = {}
    for it in items:
        by_app.setdefault(it.app_id, []).append(it)
    fail = 0
    for app_id, group in by_app.items():
        lines.append(f"## {app_id}")
        for it in group:
            mark = "OK" if it.ok else "FAIL"
            if not it.ok:
                fail += 1
            lines.append(f"  [{mark}] {it.platform}/{it.code}: {it.message}")
        lines.append("")
    lines.append(f"合计失败项: {fail}")
    if fail:
        lines.append(
            "boykeep iOS 若 FAIL：切到 boykeep 主体生成 .p8，填入 config/apps.yaml "
            "的 ios.key_id / issuer_id / private_key_path。"
            "详见 docs/MULTI_APP_READY.md / docs/IOS_ASC_SETUP.md。"
        )
    return "\n".join(lines).rstrip() + "\n"


def items_to_dicts(items: list[CheckItem]) -> list[dict[str, Any]]:
    return [asdict(i) for i in items]
