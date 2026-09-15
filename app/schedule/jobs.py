from __future__ import annotations

import time

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from app.config import get_settings, load_apps_config
from app.core.service import AppReleaseService
from app.core.watch_targets import (
    active_targets,
    console_hints_for,
    update_target_fields,
)
from app.notify.feishu_notify import Notifier

_scheduler: BackgroundScheduler | None = None
_last_fingerprint: dict[str, str] = {}


def _fingerprint(app_id: str, platform: str, state: str, message: str) -> str:
    # 与 cli watch 保持一致：state|message（避免 serve/cli 格式不一致导致误报）
    return f"{state}|{message}"


def poll_review_status_job() -> None:
    cfg = load_apps_config().get("schedule") or {}
    if cfg.get("poll_review_status") is False:
        return

    service = AppReleaseService()
    notifier = Notifier()
    changed = []
    heartbeats = []
    change_titles: dict[str, str] = {}

    # 1) 优先扫已登记的盯盘目标（含 versionCode，prefer production）
    targets_by_key = {t.key: t for t in active_targets()}
    for key, status in service.status_watch_targets():
        target = targets_by_key.get(key)
        fp = _fingerprint(
            status.app_id,
            status.platform.value,
            status.state.value,
            status.message,
        )
        mem_key = f"watch:{key}"
        prev = (target.last_fingerprint if target else "") or _last_fingerprint.get(mem_key, "")
        if prev != fp:
            _last_fingerprint[mem_key] = fp
            if target:
                update_target_fields(key, last_fingerprint=fp)
            # 首次建档只记指纹不刷屏；已有 prev 才算变化
            if prev:
                changed.append(status)
                prev_state, prev_msg = (
                    prev.split("|", 1) if "|" in prev else (prev, "")
                )
                from app.core.notify_titles import review_change_notify_title

                change_titles[key] = review_change_notify_title(
                    prev_state,
                    status.state,
                    previous_message=prev_msg,
                    current_message=status.message,
                )

        if target and target.heartbeat_hours > 0:
            now = time.time()
            elapsed_h = (now - (target.last_heartbeat_at or target.submitted_at)) / 3600.0
            if elapsed_h >= target.heartbeat_hours:
                update_target_fields(key, last_heartbeat_at=now)
                heartbeats.append(status)

    # 2) 全量 app status（无盯盘目标时仍可感知轨道变化）
    if not targets_by_key:
        for status in service.status_all_apps():
            key = f"{status.app_id}:{status.platform.value}"
            fp = _fingerprint(
                status.app_id,
                status.platform.value,
                status.state.value,
                status.message,
            )
            if _last_fingerprint.get(key) != fp:
                prev = _last_fingerprint.get(key, "")
                _last_fingerprint[key] = fp
                if prev:
                    changed.append(status)

    if changed:
        logger.info("review status changed: {} items", len(changed))
        try:
            title = "审核/发布状态变化"
            if len(changed) == 1 and change_titles:
                title = next(iter(change_titles.values()))
            notifier.notify_review_statuses(
                changed,
                title=title,
                footer=console_hints_for([s.platform.value for s in changed]),
            )
        except Exception:  # noqa: BLE001
            logger.exception("notify review status failed")

    if heartbeats:
        logger.info("review heartbeat: {} items", len(heartbeats))
        try:
            notifier.notify_review_statuses(
                heartbeats,
                title="审核盯盘心跳提醒",
                footer=console_hints_for([s.platform.value for s in heartbeats]),
            )
        except Exception:  # noqa: BLE001
            logger.exception("notify heartbeat failed")


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler:
        return _scheduler

    settings = get_settings()
    cfg = load_apps_config().get("schedule") or {}
    minutes = int(cfg.get("interval_minutes") or settings.status_poll_interval_minutes)

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        poll_review_status_job,
        trigger="interval",
        minutes=minutes,
        id="poll_review_status",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("scheduler started, poll every {} minutes", minutes)
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
