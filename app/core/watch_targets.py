"""Persist production submit watch targets for CLI watch / serve scheduler."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.config import ROOT_DIR

DATA_DIR = ROOT_DIR / "data"
WATCH_TARGETS_PATH = DATA_DIR / "watch_targets.json"

ANDROID_HINT = (
    "进度以 Play Console（正式版 / 发布概览）为准；政策待办与期限请到"
    "「监控与改进 → 政策和计划 → 政策状态」查看（本工具无法通过 API 读取）。"
    "本工具可：上传 AAB、设定分批比例（如默认 5%）、送审并盯盘通知；"
    "不会代点「发布」或把分批改成全量。若开启自管式发布，过审后仍需运营在控制台发布后才对用户可见。"
)

IOS_HINT = (
    "进度以 App Store Connect 为准；政策/拒信以 ASC 与邮件为准。"
    "本工具可：上传 IPA、读取审核与分批进度（Apple 固定 7 天曲线，不可自定义比例）、盯盘通知；"
    "不会代点「发布到全部用户」或暂停/恢复分批。若选择手动发布，过审后仍需在 ASC 确认上线。"
)

# 兼容旧引用：默认按 Android（历史调用点多为 Play）
CONSOLE_HINT = ANDROID_HINT


def console_hint_for(platform: str | None) -> str:
    """按平台返回控制台提示文案。"""
    p = (platform or "").strip().lower()
    if p in {"ios", "iphone", "ipad"}:
        return IOS_HINT
    return ANDROID_HINT


def console_hints_for(platforms: list[str] | None) -> str:
    """多平台合并提示（去重，顺序：先出现的平台优先）。"""
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in platforms or []:
        hint = console_hint_for(raw)
        if hint in seen:
            continue
        seen.add(hint)
        ordered.append(hint)
    return "\n".join(ordered) if ordered else ANDROID_HINT


@dataclass
class WatchTarget:
    app_id: str
    platform: str = "android"
    version_code: str | None = None
    track: str = "production"
    submitted_at: float = field(default_factory=time.time)
    last_fingerprint: str = ""
    last_heartbeat_at: float = 0.0
    heartbeat_hours: float = 0.0
    active: bool = True
    note: str = ""

    @property
    def key(self) -> str:
        vc = self.version_code or "-"
        return f"{self.app_id}:{self.platform}:{vc}"


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_targets() -> list[WatchTarget]:
    if not WATCH_TARGETS_PATH.exists():
        return []
    try:
        raw = json.loads(WATCH_TARGETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = raw.get("targets") if isinstance(raw, dict) else raw
    out: list[WatchTarget] = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("app_id"):
            continue
        out.append(
            WatchTarget(
                app_id=str(item["app_id"]),
                platform=str(item.get("platform") or "android"),
                version_code=(str(item["version_code"]) if item.get("version_code") else None),
                track=str(item.get("track") or "production"),
                submitted_at=float(item.get("submitted_at") or time.time()),
                last_fingerprint=str(item.get("last_fingerprint") or ""),
                last_heartbeat_at=float(item.get("last_heartbeat_at") or 0.0),
                # 注意：0 表示关闭心跳，不能用 `or 12`（0 会被当成假值）
                heartbeat_hours=(
                    0.0
                    if item.get("heartbeat_hours") is None
                    else float(item.get("heartbeat_hours"))
                ),
                active=bool(item.get("active", True)),
                note=str(item.get("note") or ""),
            )
        )
    return out


def save_targets(targets: list[WatchTarget]) -> None:
    _ensure_dir()
    payload: dict[str, Any] = {
        "updated_at": time.time(),
        "targets": [asdict(t) for t in targets],
    }
    WATCH_TARGETS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _version_code_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text == "-":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _deactivate_older_android_production(
    targets: list[WatchTarget],
    *,
    app_id: str,
    new_version_code: str,
) -> list[str]:
    """Mark older production Android watches inactive. Returns deactivated keys."""
    new_vc = _version_code_int(new_version_code)
    if new_vc is None:
        return []
    deactivated: list[str] = []
    for t in targets:
        if not t.active:
            continue
        if t.app_id != app_id or (t.platform or "").lower() != "android":
            continue
        track = (t.track or "production").lower()
        if track not in {"production", "prod"}:
            continue
        old_vc = _version_code_int(t.version_code)
        if old_vc is None or old_vc >= new_vc:
            continue
        t.active = False
        if t.note and "superseded" not in t.note:
            t.note = f"{t.note}; superseded by {new_version_code}"
        elif not t.note:
            t.note = f"superseded by {new_version_code}"
        deactivated.append(t.key)
    return deactivated


def upsert_target(
    *,
    app_id: str,
    platform: str = "android",
    version_code: str | None = None,
    track: str = "production",
    heartbeat_hours: float = 0.0,
    note: str = "",
) -> WatchTarget:
    """Insert or replace a watch target.

    For Android production with a numeric versionCode: also deactivate other
    active production watches on the same app whose versionCode is strictly older.
    """
    from loguru import logger

    targets = load_targets()
    target = WatchTarget(
        app_id=app_id,
        platform=platform,
        version_code=str(version_code) if version_code else None,
        track=track or "production",
        submitted_at=time.time(),
        last_fingerprint="",
        last_heartbeat_at=0.0,
        heartbeat_hours=heartbeat_hours,
        active=True,
        note=note,
    )
    replaced = False
    for i, existing in enumerate(targets):
        if (
            existing.app_id == target.app_id
            and existing.platform == target.platform
            and (existing.version_code or "-") == (target.version_code or "-")
        ):
            targets[i] = target
            replaced = True
            break
    if not replaced:
        targets.append(target)

    deactivated: list[str] = []
    plat = (platform or "").lower()
    track_l = (target.track or "production").lower()
    if (
        plat == "android"
        and target.version_code
        and track_l in {"production", "prod"}
    ):
        deactivated = _deactivate_older_android_production(
            targets,
            app_id=app_id,
            new_version_code=target.version_code,
        )

    save_targets(targets)
    if deactivated:
        logger.info(
            "watch deactivated older android targets app={} new={} old={}",
            app_id,
            target.version_code,
            deactivated,
        )
    return target


def update_target_fields(key: str, **fields: Any) -> WatchTarget | None:
    targets = load_targets()
    for i, t in enumerate(targets):
        if t.key != key:
            continue
        data = asdict(t)
        data.update(fields)
        targets[i] = WatchTarget(**data)
        save_targets(targets)
        return targets[i]
    return None


def active_targets() -> list[WatchTarget]:
    return [t for t in load_targets() if t.active]


def watch_hint_command(app_id: str, version_code: str | None, platform: str = "android") -> str:
    vc = f" --version-code {version_code}" if version_code else ""
    return (
        f'python cli.py watch --app-id {app_id} --platform {platform}{vc} '
        f"--interval 30 --heartbeat-hours 12 --notify"
    )
