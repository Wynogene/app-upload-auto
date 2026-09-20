"""ASC reviewSubmissions：建版本 / 挂构建 / what's New / 送审。

execute=False 时只做只读检查与计划，不写 ASC。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

from app.stores.apple_states import pick_version_state

ASC_BASE = "https://api.appstoreconnect.apple.com"

# 不允许再送审的版本状态（已在审 / 已过审等待等）
_BLOCK_SUBMIT_STATES = frozenset(
    {
        "WAITING_FOR_REVIEW",
        "IN_REVIEW",
        "PENDING_APPLE_RELEASE",
        "PENDING_DEVELOPER_RELEASE",
        "PROCESSING_FOR_APP_STORE",
        "READY_FOR_DISTRIBUTION",
        "READY_FOR_SALE",
    }
)


@dataclass
class ReviewSubmitPlan:
    app_store_app_id: str
    version_string: str
    build_id: str | None
    platform: str = "IOS"
    release_type: str = "AFTER_APPROVAL"  # 默认过审后自动；调用方可改 MANUAL
    whats_new_by_locale: dict[str, str] = field(default_factory=dict)
    fallback_whats_new: str = ""
    steps: list[str] = field(default_factory=list)


@dataclass
class ReviewSubmitResult:
    ok: bool
    message: str
    version_id: str | None = None
    submission_id: str | None = None
    dry_run: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def _asc_error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        errs = body.get("errors") or []
        if errs:
            return "; ".join(
                f"{e.get('code') or e.get('title')}: {e.get('detail') or e.get('title')}"
                for e in errs[:5]
            )
    except Exception:  # noqa: BLE001
        pass
    return (resp.text or "")[:400]


def find_version_by_string(
    client: httpx.Client,
    headers: dict[str, str],
    app_store_app_id: str,
    version_string: str,
    *,
    platform: str = "IOS",
) -> dict[str, Any] | None:
    resp = client.get(
        f"{ASC_BASE}/v1/apps/{app_store_app_id}/appStoreVersions",
        headers=headers,
        params={
            "filter[versionString]": version_string,
            "filter[platform]": platform,
            "limit": 5,
            "fields[appStoreVersions]": (
                "versionString,appStoreState,appVersionState,platform,releaseType"
            ),
        },
    )
    if resp.status_code >= 400:
        return None
    data = (resp.json() or {}).get("data") or []
    return data[0] if data else None


def list_open_review_submissions(
    client: httpx.Client,
    headers: dict[str, str],
    app_store_app_id: str,
) -> list[dict[str, Any]]:
    resp = client.get(
        f"{ASC_BASE}/v1/reviewSubmissions",
        headers=headers,
        params={
            "filter[app]": app_store_app_id,
            "limit": 20,
            "fields[reviewSubmissions]": "state,platform,submittedDate",
        },
    )
    if resp.status_code >= 400:
        logger.warning(
            "list reviewSubmissions HTTP {}: {}",
            resp.status_code,
            _asc_error_message(resp),
        )
        return []
    out = []
    for item in (resp.json() or {}).get("data") or []:
        state = ((item.get("attributes") or {}).get("state") or "").upper()
        if state in {
            "READY_FOR_REVIEW",
            "WAITING_FOR_REVIEW",
            "IN_REVIEW",
            "UNRESOLVED_ISSUES",
        }:
            out.append(item)
    return out


def ensure_phased_release(
    client: httpx.Client,
    headers: dict[str, str],
    version_id: str,
) -> dict[str, Any]:
    """确保该版本已开启「分批发布」配置（默认 INACTIVE，过审/手动发布后走 7 天曲线）。

    首版本 App 可能不支持 phased（仅后续更新），此时返回 ok=False 并说明原因。
    """
    get_resp = client.get(
        f"{ASC_BASE}/v1/appStoreVersions/{version_id}/appStoreVersionPhasedRelease",
        headers=headers,
    )
    if get_resp.status_code < 400:
        data = (get_resp.json() or {}).get("data")
        if data:
            state = (data.get("attributes") or {}).get("phasedReleaseState") or ""
            return {
                "ok": True,
                "existed": True,
                "phased_id": data.get("id"),
                "phasedReleaseState": state,
                "message": f"分批已存在 state={state or '未知'}",
            }

    body = {
        "data": {
            "type": "appStoreVersionPhasedReleases",
            "attributes": {"phasedReleaseState": "INACTIVE"},
            "relationships": {
                "appStoreVersion": {
                    "data": {"type": "appStoreVersions", "id": version_id},
                }
            },
        }
    }
    resp = client.post(
        f"{ASC_BASE}/v1/appStoreVersionPhasedReleases",
        headers=headers,
        json=body,
    )
    if resp.status_code >= 400:
        return {
            "ok": False,
            "existed": False,
            "message": (
                f"开启分批失败 HTTP {resp.status_code}: {_asc_error_message(resp)}"
                "（若为本 App 第一个商店版本，Apple 可能不支持分批，需人工确认）"
            ),
        }
    data = (resp.json() or {}).get("data") or {}
    state = ((data.get("attributes") or {}).get("phasedReleaseState") or "INACTIVE")
    return {
        "ok": True,
        "existed": False,
        "created": True,
        "phased_id": data.get("id"),
        "phasedReleaseState": state,
        "message": f"已创建分批发布配置 state={state}",
    }


def check_build_export_compliance(
    client: httpx.Client,
    headers: dict[str, str],
    build_id: str,
) -> dict[str, Any]:
    """检查构建是否已声明出口合规（usesNonExemptEncryption 非空）。"""
    resp = client.get(
        f"{ASC_BASE}/v1/builds/{build_id}",
        headers=headers,
        params={"fields[builds]": "usesNonExemptEncryption,processingState,version"},
    )
    if resp.status_code >= 400:
        return {
            "ok": False,
            "message": (
                f"读取构建合规信息失败 HTTP {resp.status_code}: "
                f"{_asc_error_message(resp)}"
            ),
        }
    attrs = ((resp.json() or {}).get("data") or {}).get("attributes") or {}
    flag = attrs.get("usesNonExemptEncryption")
    details = {
        "usesNonExemptEncryption": flag,
        "processingState": attrs.get("processingState"),
        "build_version": attrs.get("version"),
    }
    if flag is None:
        return {
            "ok": False,
            "message": (
                "构建缺少出口合规声明（usesNonExemptEncryption 为空）。"
                "请在 ASC → TestFlight → 该构建 填写「出口合规信息」，"
                "或在 Xcode Info.plist 设置 ITSAppUsesNonExemptEncryption。"
            ),
            "details": details,
        }
    return {
        "ok": True,
        "message": f"出口合规已声明 usesNonExemptEncryption={flag}",
        "details": details,
    }


def execute_review_submit(
    *,
    client: httpx.Client,
    headers: dict[str, str],
    plan: ReviewSubmitPlan,
) -> ReviewSubmitResult:
    details: dict[str, Any] = {
        "version_string": plan.version_string,
        "build_id": plan.build_id,
        "release_type": plan.release_type,
    }

    open_subs = list_open_review_submissions(
        client, headers, plan.app_store_app_id
    )
    if open_subs:
        states = [
            ((s.get("attributes") or {}).get("state"), s.get("id")) for s in open_subs
        ]
        return ReviewSubmitResult(
            ok=False,
            message=(
                "已存在进行中的 reviewSubmission，拒绝重复提审："
                f"{states!r}"
            ),
            details={**details, "open_submissions": states},
        )

    version = find_version_by_string(
        client,
        headers,
        plan.app_store_app_id,
        plan.version_string,
        platform=plan.platform,
    )
    version_id: str | None = None
    if version:
        version_id = version.get("id")
        raw_state = pick_version_state(version.get("attributes") or {})
        details["existing_version_state"] = raw_state
        if raw_state in _BLOCK_SUBMIT_STATES:
            return ReviewSubmitResult(
                ok=False,
                message=f"版本 {plan.version_string} 状态为 {raw_state}，不能再提审",
                version_id=version_id,
                details=details,
            )
    else:
        create_body = {
            "data": {
                "type": "appStoreVersions",
                "attributes": {
                    "platform": plan.platform,
                    "versionString": plan.version_string,
                    "releaseType": plan.release_type,
                },
                "relationships": {
                    "app": {
                        "data": {"type": "apps", "id": plan.app_store_app_id},
                    }
                },
            }
        }
        resp = client.post(
            f"{ASC_BASE}/v1/appStoreVersions", headers=headers, json=create_body
        )
        if resp.status_code >= 400:
            return ReviewSubmitResult(
                ok=False,
                message=(
                    f"创建 appStoreVersions 失败 HTTP {resp.status_code}: "
                    f"{_asc_error_message(resp)}"
                ),
                details=details,
            )
        version_id = ((resp.json() or {}).get("data") or {}).get("id")
        details["created_version"] = True

    if not version_id:
        return ReviewSubmitResult(
            ok=False, message="无法解析 appStoreVersion id", details=details
        )
    details["version_id"] = version_id

    if plan.build_id:
        rel_body = {
            "data": {"type": "builds", "id": plan.build_id},
        }
        resp = client.patch(
            f"{ASC_BASE}/v1/appStoreVersions/{version_id}/relationships/build",
            headers=headers,
            json=rel_body,
        )
        if resp.status_code >= 400:
            return ReviewSubmitResult(
                ok=False,
                message=(
                    f"关联构建失败 HTTP {resp.status_code}: {_asc_error_message(resp)}"
                ),
                version_id=version_id,
                details=details,
            )
        details["build_attached"] = True

    # 默认开启分批（INACTIVE：过审或手动发布后走 7 天曲线，避免接近全量）
    phased = ensure_phased_release(client, headers, version_id)
    details["phased_release"] = phased
    if not phased.get("ok"):
        return ReviewSubmitResult(
            ok=False,
            message=f"提审前分批校验失败：{phased.get('message')}",
            version_id=version_id,
            details=details,
        )

    # what's New：必须覆盖 ASC 上全部已有本地化语言
    wn = _apply_whats_new(
        client,
        headers,
        version_id,
        text_by_locale=plan.whats_new_by_locale,
        fallback=plan.fallback_whats_new,
        require_all_locales=True,
    )
    details["whats_new"] = wn
    if wn.get("error"):
        return ReviewSubmitResult(
            ok=False,
            message=f"写入 what's New 失败：{wn['error']}",
            version_id=version_id,
            details=details,
        )

    # 出口合规：构建必须已声明 usesNonExemptEncryption
    if plan.build_id:
        compliance = check_build_export_compliance(client, headers, plan.build_id)
        details["export_compliance"] = compliance
        if not compliance.get("ok"):
            return ReviewSubmitResult(
                ok=False,
                message=compliance.get("message") or "出口合规检查未通过",
                version_id=version_id,
                details=details,
            )

    # reviewSubmissions
    sub_body = {
        "data": {
            "type": "reviewSubmissions",
            "attributes": {"platform": plan.platform},
            "relationships": {
                "app": {"data": {"type": "apps", "id": plan.app_store_app_id}},
            },
        }
    }
    resp = client.post(
        f"{ASC_BASE}/v1/reviewSubmissions", headers=headers, json=sub_body
    )
    if resp.status_code >= 400:
        return ReviewSubmitResult(
            ok=False,
            message=(
                f"创建 reviewSubmissions 失败 HTTP {resp.status_code}: "
                f"{_asc_error_message(resp)}"
            ),
            version_id=version_id,
            details=details,
        )
    submission_id = ((resp.json() or {}).get("data") or {}).get("id")
    if not submission_id:
        return ReviewSubmitResult(
            ok=False,
            message="创建 reviewSubmissions 未返回 id",
            version_id=version_id,
            details=details,
        )
    details["submission_id"] = submission_id

    item_body = {
        "data": {
            "type": "reviewSubmissionItems",
            "relationships": {
                "reviewSubmission": {
                    "data": {"type": "reviewSubmissions", "id": submission_id}
                },
                "appStoreVersion": {
                    "data": {"type": "appStoreVersions", "id": version_id}
                },
            },
        }
    }
    resp = client.post(
        f"{ASC_BASE}/v1/reviewSubmissionItems", headers=headers, json=item_body
    )
    if resp.status_code >= 400:
        return ReviewSubmitResult(
            ok=False,
            message=(
                f"添加 reviewSubmissionItems 失败 HTTP {resp.status_code}: "
                f"{_asc_error_message(resp)}"
            ),
            version_id=version_id,
            submission_id=submission_id,
            details=details,
        )

    patch_body = {
        "data": {
            "type": "reviewSubmissions",
            "id": submission_id,
            "attributes": {"submitted": True},
        }
    }
    resp = client.patch(
        f"{ASC_BASE}/v1/reviewSubmissions/{submission_id}",
        headers=headers,
        json=patch_body,
    )
    if resp.status_code >= 400:
        return ReviewSubmitResult(
            ok=False,
            message=(
                f"提交 reviewSubmissions(submitted=true) 失败 "
                f"HTTP {resp.status_code}: {_asc_error_message(resp)}"
            ),
            version_id=version_id,
            submission_id=submission_id,
            details=details,
        )

    state = (
        ((resp.json() or {}).get("data") or {}).get("attributes") or {}
    ).get("state")
    details["submission_state"] = state
    return ReviewSubmitResult(
        ok=True,
        message=(
            f"已提审：version={plan.version_string} id={version_id} "
            f"submission={submission_id} state={state}"
        ),
        version_id=version_id,
        submission_id=submission_id,
        details=details,
    )


def _apply_whats_new(
    client: httpx.Client,
    headers: dict[str, str],
    version_id: str,
    *,
    text_by_locale: dict[str, str],
    fallback: str,
    require_all_locales: bool = True,
) -> dict[str, Any]:
    from app.stores.apple_whats_new import (
        parse_localization_rows,
        plan_whats_new_updates,
        resolve_text_for_locale,
    )

    resp = client.get(
        f"{ASC_BASE}/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations",
        headers=headers,
        params={"limit": 50, "fields[appStoreVersionLocalizations]": "locale,whatsNew"},
    )
    if resp.status_code >= 400:
        return {"error": _asc_error_message(resp), "patched": 0}
    rows = parse_localization_rows(resp.json())
    if not rows:
        return {
            "error": "ASC 上该版本没有任何本地化语言，无法写入 what's New",
            "patched": 0,
        }

    if require_all_locales:
        # 空语言：优先用 locale 专文案，否则用 fallback（RELEASE_NOTES_DEFAULT / --whats-new）
        missing: list[str] = []
        for row in rows:
            desired = resolve_text_for_locale(
                row.locale,
                text_by_locale=text_by_locale or {},
                fallback_text=fallback or "",
            )
            if not (row.whats_new or "").strip() and not (desired or "").strip():
                missing.append(row.locale)
        if missing:
            return {
                "error": (
                    "以下已本地化语言缺少 what's New，且没有可用默认文案："
                    + ", ".join(missing)
                    + "。请配置 RELEASE_NOTES_DEFAULT / apps.yaml release_notes_default，"
                    "或传 --whats-new。"
                ),
                "patched": 0,
                "missing_locales": missing,
                "locales": [r.locale for r in rows],
            }

    # 只填 ASC 上仍为空的语言；已有文案不覆盖。空的用默认/指定文案填满。
    planned = plan_whats_new_updates(
        rows,
        text_by_locale=text_by_locale or {},
        fallback_text=fallback or "",
        fill_empty_only=True,
    )
    patched = 0
    skipped = 0
    for item in planned:
        if item.action != "fill" or not item.planned:
            skipped += 1
            continue
        body = {
            "data": {
                "type": "appStoreVersionLocalizations",
                "id": item.localization_id,
                "attributes": {"whatsNew": item.planned},
            }
        }
        p = client.patch(
            f"{ASC_BASE}/v1/appStoreVersionLocalizations/{item.localization_id}",
            headers=headers,
            json=body,
        )
        if p.status_code >= 400:
            return {
                "error": f"{item.locale}: {_asc_error_message(p)}",
                "patched": patched,
            }
        patched += 1
    return {
        "patched": patched,
        "skipped": skipped,
        "locales": [r.locale for r in rows],
    }


def build_review_plan_steps(plan: ReviewSubmitPlan) -> list[str]:
    return [
        "检查是否已有进行中的 reviewSubmission（有则拒绝）",
        f"查找或创建 appStoreVersion {plan.version_string}",
        f"关联 build_id={plan.build_id or '（无，需先 upload）'}",
        "确保开启分批发布（phasedRelease，默认 7 天曲线）",
        "空的本地化 what's New 用默认文案填满（已有文案不覆盖；仍空则拒绝）",
        "校验构建出口合规 usesNonExemptEncryption 已声明",
        "POST reviewSubmissions → POST reviewSubmissionItems → PATCH submitted=true",
    ]
