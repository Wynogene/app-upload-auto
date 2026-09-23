"""ASC Build Upload API（WWDC25）：Windows 可直传 IPA，无需 Mac / altool.

默认由调用方决定是否 ``execute``；本模块在 execute=False 时只做计划，不写 ASC。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

ASC_BASE = "https://api.appstoreconnect.apple.com"


@dataclass
class BuildUploadPlan:
    app_store_app_id: str
    ipa_path: Path
    file_size: int
    cf_bundle_short_version: str
    cf_bundle_version: str
    platform: str = "IOS"
    file_name: str = ""
    md5_hex: str = ""
    steps: list[str] = field(default_factory=list)
    # 仅用于失败摘要 CLI 提示；不参与 ASC 写入
    app_id: str | None = None


@dataclass
class BuildUploadResult:
    ok: bool
    message: str
    build_upload_id: str | None = None
    build_upload_file_id: str | None = None
    build_id: str | None = None
    build_processing_state: str | None = None
    dry_run: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def format_ios_upload_failure_guide(
    plan: BuildUploadPlan,
    details: dict[str, Any],
    *,
    stuck_at: str,
    build_upload_id: str | None = None,
    build_upload_file_id: str | None = None,
    build_id: str | None = None,
    build_processing_state: str | None = None,
) -> str:
    """上传失败时追加的只读摘要。不自动续传、不写商店。"""
    app = (plan.app_id or "").strip() or "<app-id>"
    ver = plan.cf_bundle_short_version
    build_no = plan.cf_bundle_version
    uid = build_upload_id or details.get("build_upload_id")
    fid = build_upload_file_id or details.get("build_upload_file_id")
    bid = build_id or details.get("build_id")
    proc = build_processing_state or details.get("build_processing_state")
    state = details.get("build_upload_state") or ""

    done: list[str] = []
    if uid:
        done.append(f"已创建 buildUploads id={uid}" + (f" state={state}" if state else ""))
    else:
        done.append("尚未创建 buildUploads 会话")
    if fid:
        done.append(f"已创建 buildUploadFiles id={fid}")
    if details.get("parts_uploaded"):
        done.append("分片 PUT 已全部完成")
    elif uid and fid:
        done.append("分片 PUT 可能未完成")
    if details.get("marked_uploaded"):
        done.append("已标记 uploaded=true")
    if details.get("build_upload_complete") or state in {
        "COMPLETE",
        "COMPLETED",
        "SUCCESS",
    }:
        done.append("buildUploads 已 COMPLETE（包多半已在 ASC）")
    if bid:
        done.append(f"已出现 build_id={bid}" + (f" processingState={proc}" if proc else ""))
    elif details.get("build_upload_complete") or state in {
        "COMPLETE",
        "COMPLETED",
        "SUCCESS",
    }:
        done.append(f"尚未等到 VALID 构建（cfBundleVersion={build_no}）")

    stuck_labels = {
        "create_upload": "卡在：创建 buildUploads",
        "create_file": "卡在：创建 buildUploadFiles",
        "missing_operations": "卡在：缺少 uploadOperations",
        "bad_operation": "卡在：分片元数据无效",
        "read_part": "卡在：读取本地分片",
        "put_part": "卡在：分片 PUT（可查网络/代理后整包重传）",
        "mark_uploaded": "卡在：标记 uploaded",
        "poll_upload": "卡在：轮询 buildUploads",
        "upload_failed_state": "卡在：buildUploads 会话失败",
        "upload_timeout": "卡在：等待 buildUploads COMPLETE 超时",
        "wait_valid": "卡在：等待构建 VALID（上传多半已成功）",
        "build_not_valid": "卡在：构建状态非 VALID",
    }
    stuck_line = stuck_labels.get(stuck_at, f"卡在：{stuck_at}")

    tips = [
        "本工具不会自动断点续传；请按下面建议处理（成功路径未改）。",
        f"查看现状：python cli.py status --app-id {app} --platform ios --no-notify",
    ]
    nearly_done = stuck_at in {"wait_valid", "build_not_valid", "upload_timeout"} or bool(
        details.get("build_upload_complete")
    )
    if nearly_done:
        tips.append(
            "优先到 ASC → TestFlight 查该构建是否已出现；**不要立刻整包再 upload**。"
        )
        tips.append(
            f"若稍后已是 VALID，用 release 提审（勿再传 IPA）："
        )
        if bid:
            tips.append(
                f"  python cli.py release --app-id {app} --platform ios "
                f"--version-name {ver} --build-id {bid} --execute --no-notify"
            )
        else:
            tips.append(
                f"  python cli.py release --app-id {app} --platform ios "
                f"--version-name {ver} --build-id <ASC_BUILD_ID> --execute --no-notify"
            )
    elif stuck_at in {
        "put_part",
        "create_upload",
        "create_file",
        "missing_operations",
        "bad_operation",
        "read_part",
        "mark_uploaded",
        "poll_upload",
        "upload_failed_state",
    }:
        tips.append(
            "确认网络/代理与 IPA 无误后，可再执行 upload --execute（会新建上传会话）。"
        )
    else:
        tips.append("到 ASC 核对后再决定是否重传或改用 release。")

    lines = (
        [
            f"目标：version={ver} build={build_no} file={plan.file_name}",
            "进度：",
        ]
        + [f"  · {x}" for x in done]
        + ["", stuck_line, ""]
        + tips
    )
    return "\n".join(lines)


def _fail_upload(
    plan: BuildUploadPlan,
    message: str,
    *,
    details: dict[str, Any],
    stuck_at: str,
    build_upload_id: str | None = None,
    build_upload_file_id: str | None = None,
    build_id: str | None = None,
    build_processing_state: str | None = None,
) -> BuildUploadResult:
    guide = format_ios_upload_failure_guide(
        plan,
        details,
        stuck_at=stuck_at,
        build_upload_id=build_upload_id,
        build_upload_file_id=build_upload_file_id,
        build_id=build_id,
        build_processing_state=build_processing_state,
    )
    return BuildUploadResult(
        ok=False,
        message=f"{message}\n\n—— 失败摘要（未自动续传）——\n{guide}",
        build_upload_id=build_upload_id,
        build_upload_file_id=build_upload_file_id,
        build_id=build_id,
        build_processing_state=build_processing_state,
        details={**details, "failure_stuck_at": stuck_at},
    )


def file_md5_hex(path: Path, *, chunk: int = 1024 * 1024) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def plan_build_upload(
    *,
    app_store_app_id: str,
    ipa_path: str | Path,
    version_name: str,
    build_number: str,
    platform: str = "IOS",
) -> BuildUploadPlan:
    path = Path(ipa_path)
    if not path.is_file():
        raise FileNotFoundError(f"IPA 不存在: {path}")
    size = path.stat().st_size
    md5 = file_md5_hex(path)
    plan = BuildUploadPlan(
        app_store_app_id=str(app_store_app_id),
        ipa_path=path,
        file_size=size,
        cf_bundle_short_version=str(version_name),
        cf_bundle_version=str(build_number),
        platform=(platform or "IOS").upper(),
        file_name=path.name,
        md5_hex=md5,
        steps=[
            "POST /v1/buildUploads",
            "POST /v1/buildUploadFiles",
            "PUT 分片上传 uploadOperations",
            "PATCH buildUploadFiles uploaded=true (+ checksum)",
            "轮询 buildUploads → COMPLETE，再轮询 builds → VALID",
        ],
    )
    return plan


def _asc_error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        errs = body.get("errors") or []
        if errs:
            parts = []
            for e in errs[:5]:
                parts.append(
                    f"{e.get('code') or e.get('title')}: {e.get('detail') or e.get('title')}"
                )
            return "; ".join(parts)
    except Exception:  # noqa: BLE001
        pass
    return (resp.text or "")[:400]


def _headers_list_to_dict(raw: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            out[str(k)] = str(v)
        return out
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("header")
            value = item.get("value")
            if name is not None and value is not None:
                out[str(name)] = str(value)
    return out


def execute_build_upload(
    *,
    client: httpx.Client,
    headers: dict[str, str],
    plan: BuildUploadPlan,
    poll_seconds: float = 15.0,
    poll_timeout_seconds: float = 45 * 60,
    valid_timeout_seconds: float | None = None,
    put_timeout_seconds: float = 600.0,
    headers_provider: Any | None = None,
) -> BuildUploadResult:
    """真实写入 ASC：创建上传会话 → 分片 PUT → 标记完成 → 等到 Build VALID。

    ``headers_provider``：可选 ``() -> dict``，长传/轮询时刷新 JWT（ASC token ~20 分钟过期）。
    ``poll_timeout_seconds``：等到 buildUploads COMPLETE 的上限。
    ``valid_timeout_seconds``：COMPLETE 之后再等 processingState=VALID 的独立上限
    （默认与 poll 相同；二者不再共用同一 deadline，避免 COMPLETE 吃满后 VALID 立刻超时）。
    """

    def _hdrs() -> dict[str, str]:
        if headers_provider is not None:
            return headers_provider()
        return headers

    if valid_timeout_seconds is None:
        valid_timeout_seconds = poll_timeout_seconds

    details: dict[str, Any] = {
        "file_name": plan.file_name,
        "file_size": plan.file_size,
        "cfBundleShortVersionString": plan.cf_bundle_short_version,
        "cfBundleVersion": plan.cf_bundle_version,
        "md5": plan.md5_hex,
    }

    create_body = {
        "data": {
            "type": "buildUploads",
            "attributes": {
                "cfBundleShortVersionString": plan.cf_bundle_short_version,
                "cfBundleVersion": plan.cf_bundle_version,
                "platform": plan.platform,
            },
            "relationships": {
                "app": {
                    "data": {"type": "apps", "id": plan.app_store_app_id},
                }
            },
        }
    }
    resp = client.post(
        f"{ASC_BASE}/v1/buildUploads", headers=_hdrs(), json=create_body
    )
    if resp.status_code >= 400:
        return _fail_upload(
            plan,
            f"创建 buildUploads 失败 HTTP {resp.status_code}: {_asc_error_message(resp)}",
            details=details,
            stuck_at="create_upload",
        )
    upload_id = ((resp.json() or {}).get("data") or {}).get("id")
    if not upload_id:
        return _fail_upload(
            plan,
            "创建 buildUploads 成功但未返回 id",
            details={**details, "raw": (resp.text or "")[:500]},
            stuck_at="create_upload",
        )
    details["build_upload_id"] = upload_id
    from app.logging_setup import echo_upload_progress

    echo_upload_progress(f"[upload] ios buildUploads created id={upload_id}")
    logger.info("ASC buildUploads created id={}", upload_id)

    file_body = {
        "data": {
            "type": "buildUploadFiles",
            "attributes": {
                "fileName": plan.file_name,
                "fileSize": plan.file_size,
                "uti": "com.apple.ipa",
                "assetType": "ASSET",
            },
            "relationships": {
                "buildUpload": {
                    "data": {"type": "buildUploads", "id": upload_id},
                }
            },
        }
    }
    resp = client.post(
        f"{ASC_BASE}/v1/buildUploadFiles", headers=_hdrs(), json=file_body
    )
    if resp.status_code >= 400:
        return _fail_upload(
            plan,
            (
                f"创建 buildUploadFiles 失败 HTTP {resp.status_code}: "
                f"{_asc_error_message(resp)}"
            ),
            details=details,
            stuck_at="create_file",
            build_upload_id=upload_id,
        )
    file_data = (resp.json() or {}).get("data") or {}
    file_id = file_data.get("id")
    attrs = file_data.get("attributes") or {}
    operations = attrs.get("uploadOperations") or []
    details["build_upload_file_id"] = file_id
    details["upload_operations"] = len(operations)
    if not file_id or not operations:
        return _fail_upload(
            plan,
            "buildUploadFiles 未返回 uploadOperations",
            details=details,
            stuck_at="missing_operations",
            build_upload_id=upload_id,
            build_upload_file_id=file_id,
        )
    logger.info(
        "ASC buildUploadFiles id={} parts={}",
        file_id,
        len(operations),
    )

    # 分片 PUT：预签名 URL，通常不带 ASC JWT
    with plan.ipa_path.open("rb") as fp:
        for idx, op in enumerate(operations):
            if not isinstance(op, dict):
                continue
            url = op.get("url") or op.get("uri")
            method = (op.get("method") or "PUT").upper()
            offset = int(op.get("offset") or 0)
            length = int(op.get("length") or 0)
            req_headers = _headers_list_to_dict(
                op.get("requestHeaders") or op.get("headers")
            )
            if not url or length <= 0:
                return _fail_upload(
                    plan,
                    f"uploadOperations[{idx}] 缺少 url/length",
                    details=details,
                    stuck_at="bad_operation",
                    build_upload_id=upload_id,
                    build_upload_file_id=file_id,
                )
            fp.seek(offset)
            chunk = fp.read(length)
            if len(chunk) != length:
                return _fail_upload(
                    plan,
                    f"读取分片失败 part={idx} expect={length} got={len(chunk)}",
                    details=details,
                    stuck_at="read_part",
                    build_upload_id=upload_id,
                    build_upload_file_id=file_id,
                )
            from app.logging_setup import echo_upload_progress

            pct = 100.0 * (idx + 1) / max(len(operations), 1)
            echo_upload_progress(
                f"[upload] ios ASC PUT part {idx + 1}/{len(operations)} "
                f"({pct:.0f}%) ~{length / (1024 * 1024):.1f} MB"
            )
            put_resp = client.request(
                method,
                url,
                content=chunk,
                headers=req_headers,
                timeout=put_timeout_seconds,
            )
            if put_resp.status_code >= 400:
                return _fail_upload(
                    plan,
                    (
                        f"分片上传失败 part={idx + 1} HTTP {put_resp.status_code}: "
                        f"{(put_resp.text or '')[:300]}"
                    ),
                    details=details,
                    stuck_at="put_part",
                    build_upload_id=upload_id,
                    build_upload_file_id=file_id,
                )

    details["parts_uploaded"] = True

    patch_body = {
        "data": {
            "type": "buildUploadFiles",
            "id": file_id,
            "attributes": {
                "uploaded": True,
                "sourceFileChecksums": {
                    "file": {"algorithm": "MD5", "hash": plan.md5_hex},
                },
            },
        }
    }
    resp = client.patch(
        f"{ASC_BASE}/v1/buildUploadFiles/{file_id}",
        headers=_hdrs(),
        json=patch_body,
    )
    if resp.status_code >= 400:
        # 部分环境可能不接受 checksum 字段，降级再试一次
        logger.warning(
            "PATCH uploaded+checksum failed, retry without checksum: {}",
            _asc_error_message(resp),
        )
        patch_body["data"]["attributes"] = {"uploaded": True}
        resp = client.patch(
            f"{ASC_BASE}/v1/buildUploadFiles/{file_id}",
            headers=_hdrs(),
            json=patch_body,
        )
        if resp.status_code >= 400:
            return _fail_upload(
                plan,
                (
                    f"标记 uploaded 失败 HTTP {resp.status_code}: "
                    f"{_asc_error_message(resp)}"
                ),
                details=details,
                stuck_at="mark_uploaded",
                build_upload_id=upload_id,
                build_upload_file_id=file_id,
            )

    details["marked_uploaded"] = True
    from app.logging_setup import echo_upload_progress

    echo_upload_progress(
        f"[upload] ios waiting buildUploads COMPLETE id={upload_id}"
    )

    # 轮询 buildUpload 会话
    deadline = time.time() + poll_timeout_seconds
    upload_state = ""
    last_echo_state = ""
    last_echo_at = 0.0
    while time.time() < deadline:
        resp = client.get(
            f"{ASC_BASE}/v1/buildUploads/{upload_id}",
            headers=_hdrs(),
        )
        if resp.status_code >= 400:
            return _fail_upload(
                plan,
                (
                    f"轮询 buildUploads 失败 HTTP {resp.status_code}: "
                    f"{_asc_error_message(resp)}"
                ),
                details=details,
                stuck_at="poll_upload",
                build_upload_id=upload_id,
                build_upload_file_id=file_id,
            )
        uattrs = ((resp.json() or {}).get("data") or {}).get("attributes") or {}
        state_obj = uattrs.get("state") or {}
        if isinstance(state_obj, dict):
            upload_state = str(state_obj.get("state") or "")
            errors = state_obj.get("errors") or []
        else:
            upload_state = str(state_obj or uattrs.get("uploadState") or "")
            errors = []
        details["build_upload_state"] = upload_state
        now = time.time()
        if upload_state != last_echo_state or (now - last_echo_at) >= 30:
            echo_upload_progress(
                f"[upload] ios buildUploads state={upload_state or '?'}"
            )
            last_echo_state = upload_state
            last_echo_at = now
        if upload_state in {"COMPLETE", "COMPLETED", "SUCCESS"}:
            details["build_upload_complete"] = True
            break
        if upload_state in {"FAILED", "FAILURE", "ERROR"} or errors:
            return _fail_upload(
                plan,
                f"buildUploads 失败 state={upload_state} errors={errors!r}",
                details=details,
                stuck_at="upload_failed_state",
                build_upload_id=upload_id,
                build_upload_file_id=file_id,
            )
        time.sleep(poll_seconds)
    else:
        return _fail_upload(
            plan,
            f"等待 buildUploads 完成超时（最后 state={upload_state}）",
            details=details,
            stuck_at="upload_timeout",
            build_upload_id=upload_id,
            build_upload_file_id=file_id,
        )

    # 轮询出现对应 build 且 VALID（独立超时，不与 COMPLETE 共用 deadline）
    valid_deadline = time.time() + float(valid_timeout_seconds)
    echo_upload_progress(
        f"[upload] ios waiting build VALID "
        f"(timeout={int(valid_timeout_seconds)}s) "
        f"cfBundleVersion={plan.cf_bundle_version}"
    )
    build_id, proc = _wait_build_valid(
        client=client,
        headers=_hdrs(),
        headers_provider=headers_provider,
        app_store_app_id=plan.app_store_app_id,
        cf_bundle_version=plan.cf_bundle_version,
        poll_seconds=poll_seconds,
        deadline=valid_deadline,
    )
    details["build_id"] = build_id
    details["build_processing_state"] = proc
    if not build_id:
        return _fail_upload(
            plan,
            (
                "IPA 已上传且 buildUploads=COMPLETE，但在超时前未等到 "
                f"processingState=VALID 的构建（cfBundleVersion={plan.cf_bundle_version}）。"
            ),
            details=details,
            stuck_at="wait_valid",
            build_upload_id=upload_id,
            build_upload_file_id=file_id,
            build_processing_state=proc,
        )
    if proc != "VALID":
        return _fail_upload(
            plan,
            f"构建已出现但状态为 {proc}（期望 VALID）",
            details=details,
            stuck_at="build_not_valid",
            build_upload_id=upload_id,
            build_upload_file_id=file_id,
            build_id=build_id,
            build_processing_state=proc,
        )

    return BuildUploadResult(
        ok=True,
        message=(
            f"IPA 已上传到 ASC：buildUploads={upload_id}，"
            f"build_id={build_id} processingState=VALID"
        ),
        build_upload_id=upload_id,
        build_upload_file_id=file_id,
        build_id=build_id,
        build_processing_state=proc,
        details=details,
    )


def lookup_build_processing(
    *,
    client: httpx.Client,
    headers: dict[str, str],
    app_store_app_id: str,
    cf_bundle_version: str,
) -> tuple[str | None, str | None]:
    """单次查询：返回 ``(build_id, processingState)``；未找到则为 ``(None, None)``。"""
    resp = client.get(
        f"{ASC_BASE}/v1/builds",
        headers=headers,
        params={
            "filter[app]": app_store_app_id,
            "filter[version]": cf_bundle_version,
            "sort": "-uploadedDate",
            "limit": 5,
            "fields[builds]": "version,processingState,uploadedDate,expired",
        },
    )
    if resp.status_code >= 400:
        return None, None
    last_id: str | None = None
    last_proc: str | None = None
    for b in (resp.json() or {}).get("data") or []:
        attrs = b.get("attributes") or {}
        if str(attrs.get("version") or "") != str(cf_bundle_version):
            continue
        last_id = b.get("id")
        last_proc = attrs.get("processingState")
        if last_proc == "VALID":
            return last_id, last_proc
        if last_proc in {"INVALID", "FAILED"}:
            return last_id, last_proc
    return last_id, last_proc


def _wait_build_valid(
    *,
    client: httpx.Client,
    headers: dict[str, str],
    app_store_app_id: str,
    cf_bundle_version: str,
    poll_seconds: float,
    deadline: float,
    headers_provider: Any | None = None,
) -> tuple[str | None, str | None]:
    def _hdrs() -> dict[str, str]:
        if headers_provider is not None:
            return headers_provider()
        return headers

    from app.logging_setup import echo_upload_progress

    last_proc: str | None = None
    last_id: str | None = None
    last_echo_proc = ""
    last_echo_at = 0.0
    echo_upload_progress(
        f"[upload] ios waiting build VALID cfBundleVersion={cf_bundle_version}"
    )
    while time.time() < deadline:
        last_id, last_proc = lookup_build_processing(
            client=client,
            headers=_hdrs(),
            app_store_app_id=app_store_app_id,
            cf_bundle_version=cf_bundle_version,
        )
        now = time.time()
        proc_note = str(last_proc or ("pending" if last_id is None else "?"))
        if proc_note != last_echo_proc or (now - last_echo_at) >= 30:
            echo_upload_progress(
                f"[upload] ios build processingState={proc_note}"
                + (f" id={last_id}" if last_id else "")
            )
            last_echo_proc = proc_note
            last_echo_at = now
        if last_proc == "VALID":
            return last_id, last_proc
        if last_proc in {"INVALID", "FAILED"}:
            return last_id, last_proc
        time.sleep(poll_seconds)
    return last_id, last_proc
