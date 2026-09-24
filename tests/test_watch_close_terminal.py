"""全量终态后关闭本地盯盘（不写商店）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import app.core.watch_targets as wt
import app.schedule.jobs as jobs
from app.models import Platform, ReviewState, ReviewStatus


def test_close_already_terminal_watch(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "watch_targets.json"
    monkeypatch.setattr(wt, "WATCH_TARGETS_PATH", path)
    monkeypatch.setattr(wt, "DATA_DIR", tmp_path)
    jobs._last_fingerprint.clear()

    wt.upsert_target(
        app_id="easelife",
        platform="ios",
        version_code=None,
        track="production",
        heartbeat_hours=0.0,
        note="keep-me",
    )
    # 已是全量指纹且 active（与当前生成规则一致）
    wt.update_target_fields(
        "easelife:ios:-",
        last_fingerprint="ops_v1|released|ios|COMPLETE|100",
        active=True,
    )

    status = ReviewStatus(
        app_id="easelife",
        platform=Platform.IOS,
        state=ReviewState.RELEASED,
        message="分批：COMPLETE（分批已结束（全量））",
    )
    service = MagicMock()
    service.status_watch_targets.return_value = [("easelife:ios:-", status)]
    service.status_all_apps.return_value = []
    monkeypatch.setattr(jobs, "AppReleaseService", lambda: service)
    monkeypatch.setattr(jobs, "load_apps_config", lambda: {"schedule": {}})
    notifier = MagicMock()
    monkeypatch.setattr(jobs, "Notifier", lambda: notifier)

    jobs.poll_review_status_job()

    t = {x.key: x for x in wt.load_targets()}["easelife:ios:-"]
    assert t.active is False
    assert t.note == "keep-me"  # 不覆盖备注
    assert t.last_fingerprint == "ops_v1|released|ios|COMPLETE|100"
    notifier.notify_review_statuses.assert_not_called()


def test_ios_complete_fingerprint_normalized() -> None:
    from app.core.watch_fingerprint import ops_watch_fingerprint

    a = ReviewStatus(
        app_id="x",
        platform=Platform.IOS,
        state=ReviewState.RELEASED,
        message="分批：COMPLETE",
    )
    b = ReviewStatus(
        app_id="x",
        platform=Platform.IOS,
        state=ReviewState.RELEASED,
        message="分批：COMPLETE 第7天≈100%",
    )
    assert ops_watch_fingerprint(a) == ops_watch_fingerprint(b)
    assert ops_watch_fingerprint(a).endswith("|COMPLETE|100")


def test_notify_then_close_on_transition_to_terminal(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "watch_targets.json"
    monkeypatch.setattr(wt, "WATCH_TARGETS_PATH", path)
    monkeypatch.setattr(wt, "DATA_DIR", tmp_path)
    jobs._last_fingerprint.clear()

    wt.upsert_target(
        app_id="boykeep",
        platform="android",
        version_code="2191",
        track="production",
        heartbeat_hours=0.0,
    )
    wt.update_target_fields(
        "boykeep:android:2191",
        last_fingerprint="ops_v1|released|android|inprogress|0.05",
        active=True,
    )

    status = ReviewStatus(
        app_id="boykeep",
        platform=Platform.ANDROID,
        state=ReviewState.RELEASED,
        message="production: x codes=[2191] status=completed ← target",
    )
    service = MagicMock()
    service.status_watch_targets.return_value = [("boykeep:android:2191", status)]
    service.status_all_apps.return_value = []
    monkeypatch.setattr(jobs, "AppReleaseService", lambda: service)
    monkeypatch.setattr(jobs, "load_apps_config", lambda: {"schedule": {}})
    notifier = MagicMock()
    monkeypatch.setattr(jobs, "Notifier", lambda: notifier)

    jobs.poll_review_status_job()

    notifier.notify_review_statuses.assert_called_once()
    t = {x.key: x for x in wt.load_targets()}["boykeep:android:2191"]
    assert t.active is False
    assert t.last_fingerprint == "ops_v1|released|android|completed|-"
