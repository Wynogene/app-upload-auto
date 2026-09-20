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
    put_timeout_seconds: float = 600.0,
) -> BuildUploadResult:
    """真实写入 ASC：创建上传会话 → 分片 PUT → 标记完成 → 等到 Build VALID。"""
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
    resp = client.post(f"{ASC_BASE}/v1/buildUploads", headers=headers, json=create_body)
    if resp.status_code >= 400:
        return BuildUploadResult(
            ok=False,
            message=f"创建 buildUploads 失败 HTTP {resp.status_code}: {_asc_error_message(resp)}",
            details=details,
        )
    upload_id = ((resp.json() or {}).get("data") or {}).get("id")
    if not upload_id:
        return BuildUploadResult(
            ok=False,
            message="创建 buildUploads 成功但未返回 id",
            details={"raw": resp.text[:500]},
        )
    details["build_upload_id"] = upload_id
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
        f"{ASC_BASE}/v1/buildUploadFiles", headers=headers, json=file_body
    )
    if resp.status_code >= 400:
        return BuildUploadResult(
            ok=False,
            message=(
                f"创建 buildUploadFiles 失败 HTTP {resp.status_code}: "
                f"{_asc_error_message(resp)}"
            ),
            build_upload_id=upload_id,
            details=details,
        )
    file_data = (resp.json() or {}).get("data") or {}
    file_id = file_data.get("id")
    attrs = file_data.get("attributes") or {}
    operations = attrs.get("uploadOperations") or []
    details["build_upload_file_id"] = file_id
    details["upload_operations"] = len(operations)
    if not file_id or not operations:
        return BuildUploadResult(
            ok=False,
            message="buildUploadFiles 未返回 uploadOperations",
            build_upload_id=upload_id,
            build_upload_file_id=file_id,
            details=details,
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
                return BuildUploadResult(
                    ok=False,
                    message=f"uploadOperations[{idx}] 缺少 url/length",
                    build_upload_id=upload_id,
                    build_upload_file_id=file_id,
                    details=details,
                )
            fp.seek(offset)
            chunk = fp.read(length)
            if len(chunk) != length:
                return BuildUploadResult(
                    ok=False,
                    message=(
                        f"读取分片失败 part={idx} expect={length} got={len(chunk)}"
                    ),
                    build_upload_id=upload_id,
                    build_upload_file_id=file_id,
                    details=details,
                )
            logger.info(
                "ASC PUT part {}/{} offset={} length={} MB={:.1f}",
                idx + 1,
                len(operations),
                offset,
                length,
                length / (1024 * 1024),
            )
            put_resp = client.request(
                method,
                url,
                content=chunk,
                headers=req_headers,
                timeout=put_timeout_seconds,
            )
            if put_resp.status_code >= 400:
                return BuildUploadResult(
                    ok=False,
                    message=(
                        f"分片上传失败 part={idx + 1} HTTP {put_resp.status_code}: "
                        f"{(put_resp.text or '')[:300]}"
                    ),
                    build_upload_id=upload_id,
                    build_upload_file_id=file_id,
                    details=details,
                )

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
        headers=headers,
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
            headers=headers,
            json=patch_body,
        )
        if resp.status_code >= 400:
            return BuildUploadResult(
                ok=False,
                message=(
                    f"标记 uploaded 失败 HTTP {resp.status_code}: "
                    f"{_asc_error_message(resp)}"
                ),
                build_upload_id=upload_id,
                build_upload_file_id=file_id,
                details=details,
            )

    # 轮询 buildUpload 会话
    deadline = time.time() + poll_timeout_seconds
    upload_state = ""
    while time.time() < deadline:
        resp = client.get(
            f"{ASC_BASE}/v1/buildUploads/{upload_id}",
            headers=headers,
        )
        if resp.status_code >= 400:
            return BuildUploadResult(
                ok=False,
                message=(
                    f"轮询 buildUploads 失败 HTTP {resp.status_code}: "
                    f"{_asc_error_message(resp)}"
                ),
                build_upload_id=upload_id,
                build_upload_file_id=file_id,
                details=details,
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
        if upload_state in {"COMPLETE", "COMPLETED", "SUCCESS"}:
            break
        if upload_state in {"FAILED", "FAILURE", "ERROR"} or errors:
            return BuildUploadResult(
                ok=False,
                message=f"buildUploads 失败 state={upload_state} errors={errors!r}",
                build_upload_id=upload_id,
                build_upload_file_id=file_id,
                details=details,
            )
        time.sleep(poll_seconds)
    else:
        return BuildUploadResult(
            ok=False,
            message=f"等待 buildUploads 完成超时（最后 state={upload_state}）",
            build_upload_id=upload_id,
            build_upload_file_id=file_id,
            details=details,
        )

    # 轮询出现对应 build 且 VALID
    build_id, proc = _wait_build_valid(
        client=client,
        headers=headers,
        app_store_app_id=plan.app_store_app_id,
        cf_bundle_version=plan.cf_bundle_version,
        poll_seconds=poll_seconds,
        deadline=deadline,
    )
    details["build_id"] = build_id
    details["build_processing_state"] = proc
    if not build_id:
        return BuildUploadResult(
            ok=False,
            message=(
                "IPA 已上传且 buildUploads=COMPLETE，但在超时前未等到 "
                f"processingState=VALID 的构建（cfBundleVersion={plan.cf_bundle_version}）。"
                "可稍后用 status / list builds 再查。"
            ),
            build_upload_id=upload_id,
            build_upload_file_id=file_id,
            build_processing_state=proc,
            details=details,
        )
    if proc != "VALID":
        return BuildUploadResult(
            ok=False,
            message=f"构建已出现但状态为 {proc}（期望 VALID）",
            build_upload_id=upload_id,
            build_upload_file_id=file_id,
            build_id=build_id,
            build_processing_state=proc,
            details=details,
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


def _wait_build_valid(
    *,
    client: httpx.Client,
    headers: dict[str, str],
    app_store_app_id: str,
    cf_bundle_version: str,
    poll_seconds: float,
    deadline: float,
) -> tuple[str | None, str | None]:
    last_proc: str | None = None
    last_id: str | None = None
    while time.time() < deadline:
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
        if resp.status_code < 400:
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
        time.sleep(poll_seconds)
    return last_id, last_proc
