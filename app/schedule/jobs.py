from __future__ import annotations

import time

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from app.config import get_settings, load_apps_config
from app.core.service import AppReleaseService
from app.core.watch_fingerprint import (
    is_ops_fingerprint,
    ops_fp_state,
    ops_watch_fingerprint,
    synthesize_message_from_ops_fp,
)
from app.core.watch_targets import (
    active_targets,
    console_hints_for,
    update_target_fields,
)
from app.notify.feishu_notify import Notifier
from app.stores.google_errors import is_transient_status_failure

_scheduler: BackgroundScheduler | None = None
_last_fingerprint: dict[str, str] = {}


def _fingerprint_status(status) -> str:
    """运营向指纹：状态 + 放量；放量不变则不通知。"""
    return ops_watch_fingerprint(status)


def _is_transient_fp(fp: str) -> bool:
    if is_ops_fingerprint(fp):
        return False
    if "|" in fp:
        state, msg = fp.split("|", 1)
    else:
        state, msg = fp, ""
    return is_transient_status_failure(state=state, message=msg)


def poll_review_status_job() -> None:
    cfg = load_apps_config().get("schedule") or {}
    if cfg.get("poll_review_status") is False:
        return

    service = AppReleaseService()
    notifier = Notifier()
    changed = []
    heartbeats = []
    change_titles: dict[str, str] = {}
    # 变化指纹先挂起：飞书发送成功后再落盘，避免「已检测到但通知失败 → 指纹已更新 → 永不重试」
    pending_fps: list[tuple[str, str]] = []  # (mem_or_disk_key, fingerprint)

    # 1) 优先扫已登记的盯盘目标（含 versionCode，prefer production）
    targets_by_key = {t.key: t for t in active_targets()}
    for key, status in service.status_watch_targets():
        target = targets_by_key.get(key)
        fp = _fingerprint_status(status)
        mem_key = f"watch:{key}"
        prev = (target.last_fingerprint if target else "") or _last_fingerprint.get(mem_key, "")

        cur_transient = is_transient_status_failure(
            state=status.state.value,
            message=status.message,
        )
        if cur_transient:
            # 代理挂了等瞬时失败：不通知、不覆盖上次真实指纹
            logger.warning(
                "watch skip transient status failure key={} msg={}",
                key,
                (status.message or "")[:120],
            )
        elif prev != fp:
            if not prev:
                # 首次建档：直接落指纹、不通知
                _last_fingerprint[mem_key] = fp
                if target:
                    update_target_fields(key, last_fingerprint=fp)
            elif _is_transient_fp(prev):
                # 历史误把代理错误落成指纹：恢复真实状态时静默纠正，不刷屏
                _last_fingerprint[mem_key] = fp
                if target:
                    update_target_fields(key, last_fingerprint=fp)
                logger.info(
                    "watch healed transient fingerprint silently key={}",
                    key,
                )
            elif (not is_ops_fingerprint(prev)) and is_ops_fingerprint(fp):
                # 旧全文指纹 → 运营指纹：静默升级，避免一次刷屏
                _last_fingerprint[mem_key] = fp
                if target:
                    update_target_fields(key, last_fingerprint=fp)
                logger.info("watch migrated fingerprint to ops_v1 key={}", key)
            else:
                changed.append(status)
                pending_fps.append((mem_key, fp))
                from app.core.notify_titles import review_change_notify_title

                change_titles[key] = review_change_notify_title(
                    ops_fp_state(prev),
                    status.state,
                    previous_message=synthesize_message_from_ops_fp(prev),
                    current_message=status.message,
                )

        if target and target.heartbeat_hours > 0 and not cur_transient:
            now = time.time()
            elapsed_h = (now - (target.last_heartbeat_at or target.submitted_at)) / 3600.0
            if elapsed_h >= target.heartbeat_hours:
                update_target_fields(key, last_heartbeat_at=now)
                heartbeats.append(status)

    # 2) 全量 app status（无盯盘目标时仍可感知轨道变化）
    if not targets_by_key:
        for status in service.status_all_apps():
            key = f"{status.app_id}:{status.platform.value}"
            fp = _fingerprint_status(status)
            if is_transient_status_failure(
                state=status.state.value,
                message=status.message,
            ):
                logger.warning(
                    "watch skip transient status failure key={} msg={}",
                    key,
                    (status.message or "")[:120],
                )
                continue
            prev = _last_fingerprint.get(key, "")
            if prev != fp:
                if not prev:
                    _last_fingerprint[key] = fp
                elif _is_transient_fp(prev):
                    _last_fingerprint[key] = fp
                elif (not is_ops_fingerprint(prev)) and is_ops_fingerprint(fp):
                    _last_fingerprint[key] = fp
                else:
                    changed.append(status)
                    pending_fps.append((key, fp))

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
            # 通知成功后再提交指纹
            for mem_key, fp in pending_fps:
                _last_fingerprint[mem_key] = fp
                if mem_key.startswith("watch:"):
                    disk_key = mem_key[len("watch:") :]
                    if disk_key in targets_by_key:
                        update_target_fields(disk_key, last_fingerprint=fp)
        except Exception:  # noqa: BLE001
            logger.exception(
                "notify review status failed; fingerprints NOT advanced (will retry)"
            )

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
    # 启动后立刻扫一轮（只读）。放到后台线程，避免 Android 代理卡住阻塞 uvicorn 起服/health
    import threading

    def _initial_poll() -> None:
        try:
            poll_review_status_job()
        except Exception:  # noqa: BLE001
            logger.exception("initial poll_review_status_job failed")

    threading.Thread(
        target=_initial_poll,
        name="initial-review-poll",
        daemon=True,
    ).start()
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
