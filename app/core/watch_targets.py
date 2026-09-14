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

CONSOLE_HINT = (
    "进度以 Play Console 为准（测试轨道 / 正式版 / 发布概览均可）。"
    "请到 Console → 监控与改进 → 政策和计划 → 政策状态，查看是否有待办与期限；"
    "政策原因与倒计时以 Console 页面和邮件为准（本工具无法通过 API 读取）。"
    "是否自动对用户上线由运营在控制台发布设置决定；本工具只负责上传与送审，不会代运营点「发布」。"
)


@dataclass
class WatchTarget:
    app_id: str
    platform: str = "android"
    version_code: str | None = None
    track: str = "production"
    submitted_at: float = field(default_factory=time.time)
    last_fingerprint: str = ""
    last_heartbeat_at: float = 0.0
    heartbeat_hours: float = 12.0
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
                heartbeat_hours=float(item.get("heartbeat_hours") or 12.0),
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


def upsert_target(
    *,
    app_id: str,
    platform: str = "android",
    version_code: str | None = None,
    track: str = "production",
    heartbeat_hours: float = 12.0,
    note: str = "",
) -> WatchTarget:
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
    save_targets(targets)
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
