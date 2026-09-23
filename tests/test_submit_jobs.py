"""提审作业队列（落盘 / 半成功分流；不写商店成功路径）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import app.core.submit_jobs as sj
from app.models import OperationResult, Platform


def test_enqueue_and_list(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sj, "SUBMIT_JOBS_PATH", tmp_path / "submit_jobs.json")
    monkeypatch.setattr(sj, "DATA_DIR", tmp_path)
    job = sj.enqueue_submit_job(
        {"type": "app_upload_submit", "app_id": "blurams", "platform": "ios"},
        None,
    )
    assert job.stage == sj.STAGE_QUEUED
    loaded = sj.get_job(job.id)
    assert loaded is not None
    assert loaded.app_id == "blurams"


def test_enqueue_dedupes_same_app_platform(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sj, "SUBMIT_JOBS_PATH", tmp_path / "submit_jobs.json")
    monkeypatch.setattr(sj, "DATA_DIR", tmp_path)
    j1 = sj.enqueue_submit_job(
        {"type": "app_upload_submit", "app_id": "blurams", "platform": "ios"},
        None,
    )
    j2 = sj.enqueue_submit_job(
        {"type": "app_upload_submit", "app_id": "blurams", "platform": "ios"},
        None,
    )
    assert j1.id == j2.id
    assert j2.deduped is True
    j3 = sj.enqueue_submit_job(
        {
            "type": "app_upload_submit",
            "app_id": "blurams",
            "platform": "ios",
            "force_new_job": True,
        },
        None,
        force_new=True,
    )
    assert j3.id != j1.id


def test_extract_partial_upload_ok_submit_fail() -> None:
    results = [
        OperationResult(
            ok=True,
            app_id="blurams",
            platform=Platform.IOS,
            message="uploaded",
            details={"build_id": "b1", "version_name": "5.1.0"},
        ),
        OperationResult(
            ok=False,
            app_id="blurams",
            platform=Platform.IOS,
            message="submit failed",
            details={"execute": True},
        ),
    ]
    partial = sj._extract_partial_success(results)
    assert partial["all_ok"] is False
    assert partial["upload_ok_submit_fail"] is True
    assert partial["build_id"] == "b1"
    assert partial["forbid_reupload"] is True


def test_extract_partial_wait_valid_forbid_reupload() -> None:
    results = [
        OperationResult(
            ok=False,
            app_id="boykeep",
            platform=Platform.IOS,
            message="wait valid timeout",
            details={
                "build_upload_complete": True,
                "failure_stuck_at": "wait_valid",
                "cfBundleShortVersionString": "5.1.0",
                "cfBundleVersion": "5.1.0.1",
            },
        )
    ]
    partial = sj._extract_partial_success(results)
    assert partial["forbid_reupload"] is True
    assert partial["build_upload_complete"] is True
    assert partial["build_id"] is None
    assert partial["cf_bundle_version"] == "5.1.0.1"


def test_process_wait_valid_when_complete_no_build(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sj, "SUBMIT_JOBS_PATH", tmp_path / "submit_jobs.json")
    monkeypatch.setattr(sj, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sj, "submit_jobs_enabled", lambda: True)

    job = sj.enqueue_submit_job(
        {
            "type": "app_upload_submit",
            "app_id": "boykeep",
            "platform": "ios",
            "execute": True,
        },
        None,
    )

    def fake_run(value, operator_open_id=None):  # noqa: ANN001
        return [
            OperationResult(
                ok=False,
                app_id="boykeep",
                platform=Platform.IOS,
                message="wait valid",
                details={
                    "build_upload_complete": True,
                    "failure_stuck_at": "wait_valid",
                    "cfBundleVersion": "5.1055.92.1",
                    "cfBundleShortVersionString": "5.1055.92",
                },
            )
        ]

    monkeypatch.setattr("app.feishu.actions.run_upload_submit_job", fake_run)
    out = sj.process_submit_job(job.id)
    assert out is not None
    assert out.stage == sj.STAGE_WAIT_VALID
    assert out.cf_bundle_version == "5.1055.92.1"


def test_process_forbid_reupload_without_build_id(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sj, "SUBMIT_JOBS_PATH", tmp_path / "submit_jobs.json")
    monkeypatch.setattr(sj, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sj, "submit_jobs_enabled", lambda: True)

    job = sj.enqueue_submit_job(
        {
            "type": "app_upload_submit",
            "app_id": "boykeep",
            "platform": "ios",
            "execute": True,
        },
        None,
    )

    def fake_run(value, operator_open_id=None):  # noqa: ANN001
        return [
            OperationResult(
                ok=False,
                app_id="boykeep",
                platform=Platform.IOS,
                message="wait valid",
                details={
                    "build_upload_complete": True,
                    "failure_stuck_at": "wait_valid",
                },
            )
        ]

    monkeypatch.setattr("app.feishu.actions.run_upload_submit_job", fake_run)
    out = sj.process_submit_job(job.id)
    assert out is not None
    assert out.stage == sj.STAGE_FAILED
    assert "禁止" in (out.last_error or "") or "勿" in (out.last_error or "")
    assert out.resume_submit_only is False


def test_extract_android_binary_likely_done() -> None:
    results = [
        OperationResult(
            ok=False,
            app_id="boykeep",
            platform=Platform.ANDROID,
            message="commit track failed",
            details={
                "version_code": 2191,
                "skipped_binary_upload": True,
                "track": "production",
            },
        )
    ]
    partial = sj._extract_partial_success(results)
    assert partial["android_binary_likely_done"] is True
    assert partial["upload_ok_submit_fail"] is True
    assert partial["version_code"] == "2191"
    assert partial["forbid_reupload"] is True


def test_process_goes_submit_only_then_succeeds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sj, "SUBMIT_JOBS_PATH", tmp_path / "submit_jobs.json")
    monkeypatch.setattr(sj, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sj, "submit_jobs_enabled", lambda: True)

    job = sj.enqueue_submit_job(
        {
            "type": "app_upload_submit",
            "app_id": "blurams",
            "platform": "ios",
            "execute": True,
        },
        None,
    )

    calls = {"n": 0}

    def fake_run(value, operator_open_id=None):  # noqa: ANN001
        calls["n"] += 1
        return [
            OperationResult(
                ok=True,
                app_id="blurams",
                platform=Platform.IOS,
                message="up",
                details={"build_id": "build-1", "version_name": "1.0"},
            ),
            OperationResult(
                ok=False,
                app_id="blurams",
                platform=Platform.IOS,
                message="sub fail",
                details={},
            ),
        ]

    monkeypatch.setattr("app.feishu.actions.run_upload_submit_job", fake_run)

    out = sj.process_submit_job(job.id)
    assert out is not None
    assert out.stage == sj.STAGE_SUBMIT_ONLY
    assert out.build_id == "build-1"
    assert out.resume_submit_only is True

    def fake_submit_only(j):  # noqa: ANN001
        return [
            OperationResult(
                ok=True,
                app_id="blurams",
                platform=Platform.IOS,
                message="submitted",
                details={"build_id": "build-1", "execute": True},
            )
        ]

    monkeypatch.setattr(sj, "_run_submit_only", fake_submit_only)
    # 立即可跑
    out.next_run_at = 0
    sj.upsert_job(out)

    out2 = sj.process_submit_job(job.id)
    assert out2 is not None
    assert out2.stage == sj.STAGE_SUCCEEDED
    assert calls["n"] == 1  # 第二次未再走完整 upload
