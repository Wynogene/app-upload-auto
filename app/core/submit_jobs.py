"""提审作业队列：落盘 + 推进（零影响商店成功写路径）。

只编排现有 ``run_upload_submit_job`` / ``AppReleaseService.submit``，
不修改 Build Upload / Play 上传成功逻辑。见 docs/SUBMIT_JOB_QUEUE.md。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from loguru import logger

from app.config import ROOT_DIR, get_settings

DATA_DIR = ROOT_DIR / "data"
SUBMIT_JOBS_PATH = DATA_DIR / "submit_jobs.json"

STAGE_QUEUED = "queued"
STAGE_RUNNING = "running"
STAGE_AWAIT_RETRY = "await_retry"
STAGE_SUBMIT_ONLY = "submit_only"
STAGE_WAIT_VALID = "wait_valid"
STAGE_SUCCEEDED = "succeeded"
STAGE_FAILED = "failed"
STAGE_CANCELLED = "cancelled"

_TERMINAL = {STAGE_SUCCEEDED, STAGE_FAILED, STAGE_CANCELLED}
_ACTIVE_STAGES = {
    STAGE_QUEUED,
    STAGE_RUNNING,
    STAGE_AWAIT_RETRY,
    STAGE_SUBMIT_ONLY,
    STAGE_WAIT_VALID,
}
_DEFAULT_MAX_ATTEMPTS = 3
_STALE_RUNNING_SECONDS = 2 * 3600
_BASE_BACKOFF_SECONDS = 60
_WAIT_VALID_MAX_ATTEMPTS = 12  # serve 周期推进，约数小时量级


@dataclass
class SubmitJob:
    id: str
    app_id: str
    platform: str
    value: dict[str, Any]
    operator_open_id: str | None = None
    stage: str = STAGE_QUEUED
    attempts: int = 0
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS
    last_error: str = ""
    artifact_path: str | None = None
    build_id: str | None = None
    version_name: str | None = None
    version_code: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    next_run_at: float = field(default_factory=time.time)
    last_results_ok: bool | None = None
    # 落盘标记：下次应走 submit_only（有 build_id）
    resume_submit_only: bool = False
    # iOS：COMPLETE 后等待 VALID 用的构建号
    cf_bundle_version: str | None = None
    # 入队时是否复用了已有作业（不落盘，仅返回给调用方）
    deduped: bool = False

    @property
    def terminal(self) -> bool:
        return self.stage in _TERMINAL


def submit_jobs_enabled() -> bool:
    settings = get_settings()
    return bool(getattr(settings, "submit_jobs_enabled", True))


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_jobs() -> list[SubmitJob]:
    if not SUBMIT_JOBS_PATH.exists():
        return []
    try:
        raw = json.loads(SUBMIT_JOBS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = raw.get("jobs") if isinstance(raw, dict) else raw
    out: list[SubmitJob] = []
    for item in items or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        out.append(
            SubmitJob(
                id=str(item["id"]),
                app_id=str(item.get("app_id") or ""),
                platform=str(item.get("platform") or ""),
                value=dict(item.get("value") or {}),
                operator_open_id=item.get("operator_open_id"),
                stage=str(item.get("stage") or STAGE_QUEUED),
                attempts=int(item.get("attempts") or 0),
                max_attempts=int(item.get("max_attempts") or _DEFAULT_MAX_ATTEMPTS),
                last_error=str(item.get("last_error") or ""),
                artifact_path=item.get("artifact_path"),
                build_id=item.get("build_id"),
                version_name=item.get("version_name"),
                version_code=(
                    str(item["version_code"])
                    if item.get("version_code") is not None
                    else None
                ),
                created_at=float(item.get("created_at") or time.time()),
                updated_at=float(item.get("updated_at") or time.time()),
                next_run_at=float(item.get("next_run_at") or time.time()),
                last_results_ok=item.get("last_results_ok"),
                resume_submit_only=bool(item.get("resume_submit_only")),
                cf_bundle_version=(
                    str(item["cf_bundle_version"])
                    if item.get("cf_bundle_version")
                    else None
                ),
            )
        )
    return out


def save_jobs(jobs: list[SubmitJob]) -> None:
    _ensure_dir()
    payload = {
        "updated_at": time.time(),
        "jobs": [
            {k: v for k, v in asdict(j).items() if k != "deduped"} for j in jobs
        ],
    }
    tmp = SUBMIT_JOBS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(SUBMIT_JOBS_PATH)


def get_job(job_id: str) -> SubmitJob | None:
    for j in load_jobs():
        if j.id == job_id:
            return j
    return None


def upsert_job(job: SubmitJob) -> SubmitJob:
    job.updated_at = time.time()
    jobs = load_jobs()
    for i, existing in enumerate(jobs):
        if existing.id == job.id:
            jobs[i] = job
            save_jobs(jobs)
            return job
    jobs.append(job)
    save_jobs(jobs)
    return job


def _normalize_platform(raw: str) -> str:
    platform = str(raw or "").strip().lower()
    if platform in {"iphone", "apple"}:
        return "ios"
    if platform == "google":
        return "android"
    return platform


def find_active_submit_job(app_id: str, platform: str) -> SubmitJob | None:
    """同 app+platform 的非终态作业（若有多条取最新更新的一条）。"""
    app_id = str(app_id or "").strip()
    platform = _normalize_platform(platform)
    if not app_id or not platform:
        return None
    candidates = [
        j
        for j in load_jobs()
        if j.app_id == app_id
        and j.platform == platform
        and j.stage in _ACTIVE_STAGES
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda j: j.updated_at, reverse=True)
    return candidates[0]


def enqueue_submit_job(
    value: dict[str, Any],
    operator_open_id: str | None = None,
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    force_new: bool = False,
) -> SubmitJob:
    """受理卡片/CLI 提审：落盘为 queued。不写商店。

    默认对同 ``app_id+platform`` 去重：已有非终态作业则复用（``deduped=True``）。
    ``force_new=True`` 或 ``value.force_new_job`` 可强制新开。
    """
    app_id = str(value.get("app_id") or "").strip()
    platform = _normalize_platform(str(value.get("platform") or ""))
    force = force_new or _truthy(value.get("force_new_job"))
    if not force:
        existing = find_active_submit_job(app_id, platform)
        if existing is not None:
            existing.deduped = True
            logger.info(
                "submit job deduped reuse id={} app={} platform={} stage={}",
                existing.id,
                existing.app_id,
                existing.platform,
                existing.stage,
            )
            return existing
    job = SubmitJob(
        id=str(uuid.uuid4()),
        app_id=app_id,
        platform=platform,
        value=dict(value),
        operator_open_id=operator_open_id,
        stage=STAGE_QUEUED,
        max_attempts=max_attempts,
        version_name=value.get("version") or value.get("version_name"),
        next_run_at=time.time(),
        deduped=False,
    )
    upsert_job(job)
    logger.info(
        "submit job enqueued id={} app={} platform={}",
        job.id,
        job.app_id,
        job.platform,
    )
    return job


def _truthy(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _extract_partial_success(results: list[Any]) -> dict[str, Any]:
    """从 OperationResult 列表抽出可续跑字段（上传已成功 / 包已在 ASC）。"""
    out: dict[str, Any] = {
        "all_ok": bool(results),
        "build_id": None,
        "version_name": None,
        "version_code": None,
        "cf_bundle_version": None,
        "upload_ok_submit_fail": False,
        "build_upload_complete": False,
        "forbid_reupload": False,
        "android_binary_likely_done": False,
    }
    if not results:
        out["all_ok"] = False
        return out

    meaningful = [
        r
        for r in results
        if not (getattr(r, "details", None) or {}).get("notify_skip")
        and not (getattr(r, "details", None) or {}).get("synthetic_follow")
    ]
    if not meaningful:
        meaningful = list(results)

    out["all_ok"] = all(getattr(r, "ok", False) for r in meaningful)

    if len(meaningful) >= 2:
        first, second = meaningful[0], meaningful[-1]
        d0 = getattr(first, "details", None) or {}
        if (
            getattr(first, "ok", False)
            and d0.get("build_id")
            and not getattr(second, "ok", False)
        ):
            out["upload_ok_submit_fail"] = True
            out["build_id"] = str(d0["build_id"])
            out["forbid_reupload"] = True
            vn = (
                d0.get("version_name")
                or d0.get("version_string")
                or d0.get("cfBundleShortVersionString")
            )
            if vn:
                out["version_name"] = str(vn)

    for r in meaningful:
        details = getattr(r, "details", None) or {}
        plat = getattr(r, "platform", None)
        plat_s = str(getattr(plat, "value", None) or plat or "").lower()
        if details.get("build_id") and not out["build_id"]:
            out["build_id"] = str(details["build_id"])
        vc = details.get("version_code")
        if vc is None:
            vc = details.get("aab_version_code")
        if vc is not None and not out["version_code"]:
            out["version_code"] = str(vc)
        vn = (
            details.get("version_name")
            or details.get("version_string")
            or details.get("cfBundleShortVersionString")
            or details.get("aab_version_name")
        )
        if vn and not out["version_name"]:
            out["version_name"] = str(vn)
        cfb = details.get("cfBundleVersion")
        if cfb and not out["cf_bundle_version"]:
            out["cf_bundle_version"] = str(cfb)
        if details.get("build_upload_complete"):
            out["build_upload_complete"] = True
        stuck = str(details.get("failure_stuck_at") or "")
        if stuck in {"wait_valid", "build_not_valid", "upload_timeout"} or details.get(
            "build_upload_complete"
        ):
            out["forbid_reupload"] = True
        if plat_s == "android":
            if details.get("skipped_binary_upload") or details.get("recovered_from_stall"):
                out["android_binary_likely_done"] = True
            if details.get("version_code") is not None and not getattr(r, "ok", False):
                msg = str(getattr(r, "message", "") or "").lower()
                if any(
                    k in msg
                    for k in (
                        "commit",
                        "track",
                        "发布",
                        "already",
                    )
                ):
                    out["android_binary_likely_done"] = True

    # 单结果：上传失败但已有 build_id → 只允许 submit_only
    if (
        not out["all_ok"]
        and not out["upload_ok_submit_fail"]
        and out.get("build_id")
        and len(meaningful) == 1
        and not getattr(meaningful[0], "ok", False)
    ):
        out["upload_ok_submit_fail"] = True
        out["forbid_reupload"] = True

    if out.get("build_id") or out.get("build_upload_complete"):
        out["forbid_reupload"] = True

    # Android：二进制很可能已在库 → 用 version_code 走 submit_only，勿整包重传
    if (
        not out["all_ok"]
        and out.get("android_binary_likely_done")
        and out.get("version_code")
    ):
        out["upload_ok_submit_fail"] = True
        out["forbid_reupload"] = True

    return out


def _backoff_seconds(attempts: int) -> float:
    return float(_BASE_BACKOFF_SECONDS * (2 ** max(0, attempts - 1)))


def _reclaim_stale_running(jobs: list[SubmitJob], *, now: float) -> bool:
    changed = False
    for j in jobs:
        if j.stage != STAGE_RUNNING:
            continue
        if now - j.updated_at < _STALE_RUNNING_SECONDS:
            continue
        if j.build_id and j.resume_submit_only:
            j.stage = STAGE_SUBMIT_ONLY
        elif j.cf_bundle_version and not j.build_id:
            j.stage = STAGE_WAIT_VALID
        elif j.attempts > 0:
            j.stage = STAGE_AWAIT_RETRY
        else:
            j.stage = STAGE_QUEUED
        j.last_error = (
            f"running 超时回收（>{_STALE_RUNNING_SECONDS}s）；将重试或从半成功续跑"
        )
        j.next_run_at = now
        j.updated_at = now
        changed = True
        logger.warning("submit job reclaim stale id={} app={}", j.id, j.app_id)
    return changed


def list_runnable_jobs(*, now: float | None = None) -> list[SubmitJob]:
    now = now if now is not None else time.time()
    jobs = load_jobs()
    if _reclaim_stale_running(jobs, now=now):
        save_jobs(jobs)
    runnable = []
    for j in jobs:
        if j.terminal or j.stage == STAGE_RUNNING:
            continue
        if j.stage not in {
            STAGE_QUEUED,
            STAGE_AWAIT_RETRY,
            STAGE_SUBMIT_ONLY,
            STAGE_WAIT_VALID,
        }:
            continue
        if j.next_run_at > now:
            continue
        runnable.append(j)
    return runnable


def _mark_running(job_id: str) -> SubmitJob | None:
    jobs = load_jobs()
    for i, j in enumerate(jobs):
        if j.id != job_id:
            continue
        if j.terminal or j.stage == STAGE_RUNNING:
            return None
        if j.stage not in {
            STAGE_QUEUED,
            STAGE_AWAIT_RETRY,
            STAGE_SUBMIT_ONLY,
            STAGE_WAIT_VALID,
        }:
            return None
        j.stage = STAGE_RUNNING
        j.updated_at = time.time()
        j.attempts = int(j.attempts) + 1
        jobs[i] = j
        save_jobs(jobs)
        return j
    return None


def _run_submit_only(job: SubmitJob) -> list[Any]:
    """半成功续跑：只 submit，不 upload。"""
    from app.core.service import AppReleaseService
    from app.models import Platform, SubmitRequest
    from app.notify.feishu_notify import Notifier

    plat = Platform.IOS if job.platform == "ios" else Platform.ANDROID
    value = job.value or {}
    track = (value.get("track") or "internal").strip().lower()
    if track in {"production", "prod"}:
        track = "production"
    req = SubmitRequest(
        app_id=job.app_id,
        platform=plat,
        version_name=job.version_name or value.get("version") or value.get("version_name"),
        build_id=job.build_id,
        version_code=job.version_code,
        whats_new=value.get("whats_new"),
        track=track if plat == Platform.ANDROID else None,
        allow_production=_truthy(value.get("allow_production")),
        execute=_truthy(value.get("execute")),
        operator_open_id=job.operator_open_id,
    )
    logger.info(
        "submit job submit_only id={} app={} build_id={} version_code={} version={}",
        job.id,
        job.app_id,
        job.build_id,
        job.version_code,
        job.version_name,
    )
    result = AppReleaseService().submit(req)
    try:
        Notifier().notify_operation_results(job.app_id, [result])
    except Exception:  # noqa: BLE001
        logger.exception("notify after submit_only failed")
    return [result]


def _run_wait_valid(job: SubmitJob) -> tuple[str, str | None]:
    """轮询 ASC 一次：返回 (status, build_id)。status: valid|invalid|pending|error"""
    from app.config import get_app_by_id
    from app.stores.apple import AppleStoreClient
    from app.stores.apple_build_upload import lookup_build_processing

    app = get_app_by_id(job.app_id) or {}
    ios = app.get("ios") or {}
    app_store_app_id = str(ios.get("app_store_app_id") or "")
    cfb = str(job.cf_bundle_version or "").strip()
    if not app_store_app_id or not cfb:
        return "error", None
    apple = AppleStoreClient()
    try:
        with apple._client() as client:
            headers = apple._headers(app)
            build_id, proc = lookup_build_processing(
                client=client,
                headers=headers,
                app_store_app_id=app_store_app_id,
                cf_bundle_version=cfb,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("wait_valid lookup failed id={}: {}", job.id, exc)
        return "error", None
    if proc == "VALID" and build_id:
        return "valid", str(build_id)
    if proc in {"INVALID", "FAILED"}:
        return "invalid", str(build_id) if build_id else None
    return "pending", str(build_id) if build_id else None


def process_submit_job(job_id: str) -> SubmitJob | None:
    """推进单条作业。成功写商店仍走现有 service / card worker。"""
    if not submit_jobs_enabled():
        return None

    job = get_job(job_id)
    if not job or job.terminal:
        return job

    use_submit_only = bool(
        (job.build_id or job.version_code)
        and (job.stage == STAGE_SUBMIT_ONLY or job.resume_submit_only)
    )
    use_wait_valid = job.stage == STAGE_WAIT_VALID

    claimed = _mark_running(job_id)
    if not claimed:
        logger.info("submit job skip (not claimable) id={}", job_id)
        return get_job(job_id)

    job = claimed

    try:
        if use_wait_valid:
            status, build_id = _run_wait_valid(job)
            if status == "valid" and build_id:
                job.build_id = build_id
                job.resume_submit_only = True
                job.stage = STAGE_SUBMIT_ONLY
                job.last_error = ""
                job.next_run_at = time.time()
                upsert_job(job)
                logger.info(
                    "submit job wait_valid → submit_only id={} build_id={}",
                    job.id,
                    build_id,
                )
                return process_submit_job(job.id)
            if status == "invalid":
                job.stage = STAGE_FAILED
                job.last_error = (
                    f"构建 processingState=INVALID/FAILED "
                    f"cfBundleVersion={job.cf_bundle_version} build_id={build_id}"
                )
                upsert_job(job)
                return job
            max_wv = max(int(job.max_attempts), _WAIT_VALID_MAX_ATTEMPTS)
            job.last_error = (
                f"等待 VALID 中 cfBundleVersion={job.cf_bundle_version} "
                f"status={status} attempts={job.attempts}/{max_wv}"
            )
            if job.attempts >= max_wv:
                job.stage = STAGE_FAILED
                job.last_error += "｜已超时，请 ASC 核对后 release（勿再整包 upload）"
            else:
                job.stage = STAGE_WAIT_VALID
                job.next_run_at = time.time() + _backoff_seconds(job.attempts)
            upsert_job(job)
            return job

        if use_submit_only:
            results = _run_submit_only(job)
        else:
            from app.feishu.actions import run_upload_submit_job

            results = run_upload_submit_job(job.value, job.operator_open_id)

        partial = _extract_partial_success(results)
        job.last_results_ok = partial["all_ok"]

        if partial["all_ok"]:
            job.stage = STAGE_SUCCEEDED
            job.last_error = ""
            job.resume_submit_only = False
            if partial.get("build_id"):
                job.build_id = str(partial["build_id"])
            if partial.get("version_name"):
                job.version_name = str(partial["version_name"])
            if partial.get("version_code"):
                job.version_code = str(partial["version_code"])
            if partial.get("cf_bundle_version"):
                job.cf_bundle_version = str(partial["cf_bundle_version"])
            upsert_job(job)
            logger.info("submit job succeeded id={} app={}", job.id, job.app_id)
            return job

        if partial.get("upload_ok_submit_fail") and (
            partial.get("build_id") or partial.get("version_code")
        ):
            if partial.get("build_id"):
                job.build_id = str(partial["build_id"])
            if partial.get("version_code"):
                job.version_code = str(partial["version_code"])
            if partial.get("version_name"):
                job.version_name = str(partial["version_name"])
            job.resume_submit_only = True
            job.stage = STAGE_SUBMIT_ONLY
            job.last_error = "; ".join(
                getattr(r, "message", "")[:200]
                for r in results
                if not getattr(r, "ok", False)
            )[:500]
            job.next_run_at = time.time() + _backoff_seconds(job.attempts)
            upsert_job(job)
            logger.warning(
                "submit job → submit_only id={} build_id={} version_code={}",
                job.id,
                job.build_id,
                job.version_code,
            )
            return job

        if partial.get("forbid_reupload") and not partial.get("build_id"):
            cfb = partial.get("cf_bundle_version")
            if cfb:
                job.cf_bundle_version = str(cfb)
                if partial.get("version_name"):
                    job.version_name = str(partial["version_name"])
                job.stage = STAGE_WAIT_VALID
                job.resume_submit_only = False
                job.last_error = "; ".join(
                    getattr(r, "message", "")[:200]
                    for r in results
                    if not getattr(r, "ok", False)
                )[:500]
                job.next_run_at = time.time() + _backoff_seconds(job.attempts)
                upsert_job(job)
                logger.warning(
                    "submit job → wait_valid id={} cfBundleVersion={}",
                    job.id,
                    job.cf_bundle_version,
                )
                return job
            job.last_error = (
                "; ".join(
                    getattr(r, "message", str(r))[:200]
                    for r in (results or ["无结果"])
                )[:700]
                + "｜包多半已在 ASC，已禁止作业整包重传；请 status 拿到 build_id 后 release"
            )
            job.stage = STAGE_FAILED
            job.resume_submit_only = False
            upsert_job(job)
            logger.warning(
                "submit job forbid reupload (no build_id/cfb) id={} app={}",
                job.id,
                job.app_id,
            )
            return job

        if partial.get("forbid_reupload") and partial.get("build_id"):
            job.build_id = str(partial["build_id"])
            if partial.get("version_name"):
                job.version_name = str(partial["version_name"])
            job.resume_submit_only = True
            job.stage = STAGE_SUBMIT_ONLY
            job.last_error = "; ".join(
                getattr(r, "message", "")[:200]
                for r in results
                if not getattr(r, "ok", False)
            )[:500]
            job.next_run_at = time.time() + _backoff_seconds(job.attempts)
            upsert_job(job)
            logger.warning(
                "submit job → submit_only (forbid_reupload) id={} build_id={}",
                job.id,
                job.build_id,
            )
            return job

        job.last_error = "; ".join(
            getattr(r, "message", str(r))[:200] for r in (results or ["无结果"])
        )[:800]
        if job.attempts >= job.max_attempts:
            job.stage = STAGE_FAILED
        else:
            job.stage = STAGE_AWAIT_RETRY
            job.next_run_at = time.time() + _backoff_seconds(job.attempts)
        upsert_job(job)
        return job

    except Exception as exc:  # noqa: BLE001
        logger.exception("submit job process failed id={}", job_id)
        job.last_error = str(exc)[:800]
        if job.attempts >= job.max_attempts:
            job.stage = STAGE_FAILED
        else:
            if job.cf_bundle_version and not job.build_id:
                job.stage = STAGE_WAIT_VALID
            else:
                job.stage = STAGE_AWAIT_RETRY
            job.next_run_at = time.time() + _backoff_seconds(job.attempts)
        upsert_job(job)
        try:
            from app.notify.feishu_notify import Notifier

            Notifier().notify_owner(
                title=f"提审作业失败 · {job.app_id}",
                markdown=(
                    f"job=`{job.id}` stage=`{job.stage}` attempts={job.attempts}\n"
                    f"`{job.last_error}`"
                ),
                template="red",
            )
        except Exception:  # noqa: BLE001
            logger.exception("notify_owner after submit job failure failed")
        return job


def advance_submit_jobs(*, limit: int = 3) -> list[str]:
    """serve / CLI：推进若干可运行作业。返回处理过的 job id。"""
    if not submit_jobs_enabled():
        return []
    done: list[str] = []
    for job in list_runnable_jobs()[:limit]:
        process_submit_job(job.id)
        done.append(job.id)
    return done
