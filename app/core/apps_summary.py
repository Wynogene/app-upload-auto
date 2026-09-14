"""Build a human-readable summary of configured apps (Android focus)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import ROOT_DIR, load_apps_config


# 文档用门槛（新 AAB 须严格大于）；有 --status 时以线上为准刷新展示
KNOWN_MIN_NEXT_VERSION_CODE: dict[str, int] = {
    "blurams": 1953,
    "easelife": 10421,
    "boykeep": 2185,
}

PLAY_ACCOUNT_LABEL: dict[str, str] = {
    "shared_blurams_easelife": "共用（blurams+easelife）GCP sd-gcp-2026-7-15",
    "boykeep_separate": "独立 Play / GCP boykeep-f3983",
    "ignored_cn_only": "国内停用，忽略",
}


def _sa_abs(raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _sa_meta(sa_path: Path) -> dict[str, str]:
    if not sa_path.exists():
        return {"sa_exists": "no", "project_id": "-", "client_email": "-"}
    try:
        data = json.loads(sa_path.read_text(encoding="utf-8"))
        return {
            "sa_exists": "yes",
            "project_id": str(data.get("project_id") or "-"),
            "client_email": str(data.get("client_email") or "-"),
        }
    except (OSError, json.JSONDecodeError):
        return {"sa_exists": "bad", "project_id": "-", "client_email": "-"}


def production_hint_from_raw(raw: dict[str, Any] | None) -> str:
    for track in (raw or {}).get("tracks") or []:
        if track.get("track") != "production":
            continue
        releases = track.get("releases") or []
        if not releases:
            return "(无正式版)"
        rel = releases[0]
        codes = ",".join(str(c) for c in (rel.get("versionCodes") or []))
        name = rel.get("name") or "-"
        status = rel.get("status") or "-"
        return f"{name} codes=[{codes}] status={status}"
    return "(无 production 轨道)"


def build_app_rows(*, include_disabled: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for app in load_apps_config().get("apps") or []:
        android = app.get("android") or {}
        enabled = bool(android.get("enabled", True))
        if not enabled and not include_disabled:
            continue
        sa_raw = str(android.get("service_account_json") or "")
        sa_path = _sa_abs(sa_raw) if sa_raw else Path("")
        meta = _sa_meta(sa_path) if sa_raw else {
            "sa_exists": "n/a",
            "project_id": "-",
            "client_email": "-",
        }
        app_id = str(app.get("id") or "")
        min_next = KNOWN_MIN_NEXT_VERSION_CODE.get(app_id)
        rows.append(
            {
                "app_id": app_id,
                "name": app.get("name") or app_id,
                "android_enabled": enabled,
                "package_name": android.get("package_name") or "-",
                "default_track": android.get("track") or "internal",
                "play_console_account": app.get("play_console_account") or "-",
                "play_account_label": PLAY_ACCOUNT_LABEL.get(
                    str(app.get("play_console_account") or ""),
                    str(app.get("play_console_account") or "-"),
                ),
                "service_account_json": sa_raw or "-",
                **meta,
                "new_aab_version_code_must_be_ge": min_next,
                "production_live": None,
            }
        )
    return rows


def enrich_rows_with_status(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from app.core.service import AppReleaseService
    from app.models import Platform, StatusRequest

    service = AppReleaseService()
    for row in rows:
        if not row.get("android_enabled"):
            row["production_live"] = "(disabled)"
            continue
        try:
            statuses = service.status(
                StatusRequest(app_id=row["app_id"], platform=Platform.ANDROID)
            )
            if not statuses:
                row["production_live"] = "(no status)"
                continue
            st = statuses[0]
            row["production_live"] = production_hint_from_raw(st.raw)
            # 从线上 codes 推一道「下一码须大于」提示
            for track in (st.raw or {}).get("tracks") or []:
                if track.get("track") != "production":
                    continue
                releases = track.get("releases") or []
                if not releases:
                    break
                codes = [int(c) for c in (releases[0].get("versionCodes") or []) if str(c).isdigit()]
                if codes:
                    row["new_aab_version_code_must_be_ge"] = max(codes) + 1
                break
        except Exception as exc:  # noqa: BLE001
            row["production_live"] = f"(status 失败: {exc})"
    return rows


def format_apps_table(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        lines.append(f"## {row['app_id']}  ({row['name']})")
        lines.append(f"  package:        {row['package_name']}")
        lines.append(f"  android:        {'enabled' if row['android_enabled'] else 'disabled'}")
        lines.append(f"  default track:  {row['default_track']}")
        lines.append(f"  play account:   {row['play_account_label']}")
        lines.append(f"  SA file:        {row['service_account_json']}  [{row['sa_exists']}]")
        lines.append(f"  GCP project:    {row['project_id']}")
        lines.append(f"  SA email:       {row['client_email']}")
        ge = row.get("new_aab_version_code_must_be_ge")
        if ge:
            lines.append(f"  new AAB need:   versionCode >= {ge}  (严格大于当前线上码)")
        live = row.get("production_live")
        if live:
            lines.append(f"  production:     {live}")
        lines.append(
            f"  upload example: python cli.py upload --app-id {row['app_id']} "
            f"--platform android --artifact \"F:\\upload-test\\xxx.aab\" --track internal --notify"
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
