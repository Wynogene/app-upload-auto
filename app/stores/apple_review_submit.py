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
    # 仅用于失败摘要里的 CLI 提示；不参与 ASC 写入
    app_id: str | None = None


@dataclass
class ReviewSubmitResult:
    ok: bool
    message: str
    version_id: str | None = None
    submission_id: str | None = None
    dry_run: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def format_ios_submit_failure_guide(
    plan: ReviewSubmitPlan,
    details: dict[str, Any],
    *,
    stuck_at: str,
    version_id: str | None = None,
    submission_id: str | None = None,
) -> str:
    """失败时追加的只读摘要：已做到哪 + 建议命令。不自动跳步、不写商店。"""
    vid = version_id or details.get("version_id")
    bid = plan.build_id or details.get("build_id")
    sid = submission_id or details.get("submission_id")
    app = (plan.app_id or "").strip() or "<app-id>"
    ver = plan.version_string

    done: list[str] = []
    if details.get("created_version"):
        done.append(f"已创建 App Store 版本 {ver}" + (f"（id={vid}）" if vid else ""))
    elif vid:
        done.append(f"已定位版本 {ver}（id={vid}）")
    else:
        done.append(f"尚未得到 version id（目标版本 {ver}）")

    if details.get("build_attached"):
        done.append(f"已挂构建 build_id={bid}")
    elif bid:
        done.append(f"计划挂构建 build_id={bid}（本步可能未完成）")
    else:
        done.append("未指定 build_id")

    phased = details.get("phased_release") or {}
    if isinstance(phased, dict) and phased.get("ok"):
        done.append("分批发布已确认/开启")
    wn = details.get("whats_new") or {}
    if isinstance(wn, dict) and wn and not wn.get("error"):
        done.append("what's New 已处理")
    compliance = details.get("export_compliance") or {}
    if isinstance(compliance, dict) and compliance.get("ok"):
        done.append("出口合规检查已通过")
    if sid:
        done.append(f"已创建 reviewSubmission id={sid}")

    stuck_labels = {
        "open_submission": "卡在：已有进行中的审核会话（勿重复提审）",
        "version_state_blocked": "卡在：版本状态不允许再提审",
        "create_version": "卡在：创建 appStoreVersion",
        "resolve_version_id": "卡在：解析 version id",
        "attach_build": "卡在：关联构建",
        "phased_release": "卡在：开启/校验分批发布",
        "whats_new": "卡在：写入 what's New",
        "export_compliance": "卡在：出口合规（请到 ASC TestFlight 该构建补全后重试）",
        "create_submission": "卡在：创建 reviewSubmissions",
        "submission_id": "卡在：解析 submission id",
        "add_submission_item": "卡在：添加 reviewSubmissionItems",
        "submit_submission": "卡在：提交 submitted=true",
        "items_unreadable": "卡在：无法读取现有审核会话 items",
    }
    stuck_line = stuck_labels.get(stuck_at, f"卡在：{stuck_at}")

    tips = [
        f"查看现状：python cli.py status --app-id {app} --platform ios --no-notify",
    ]
    if stuck_at == "export_compliance" and bid:
        tips.insert(
            0,
            "出口合规需人工补全后再提审；工具不会代填合规声明。",
        )
        tips.append(
            "先到 ASC → TestFlight → 该构建填写出口合规，再提审（勿省略 --execute）："
        )
        tips.append(
            f"  python cli.py release --app-id {app} --platform ios "
            f"--version-name {ver} --build-id {bid} --execute --no-notify"
        )
    elif stuck_at == "whats_new":
        tips.insert(0, "what's New 需补全后再提审。")
        tips.append(
            f"可先预览/补全：python cli.py ios-whats-new --app-id {app} --version {ver}"
        )
        if bid:
            tips.append(
                f"  再：python cli.py release --app-id {app} --platform ios "
                f"--version-name {ver} --build-id {bid} --execute --no-notify"
            )
    elif stuck_at == "open_submission":
        tips.insert(
            0,
            "存在无法自动续跑的进行中审核会话；不要并行再开一条，也勿轻易在 ASC 取消。"
            "若仅为空 READY_FOR_REVIEW、或已挂本版本 item 未 submitted，"
            "再跑同一条 release --execute 会尝试续跑。",
        )
    elif stuck_at == "items_unreadable":
        tips.insert(
            0,
            "无法读取现有 reviewSubmission 的 items（网络/API 抖动）。请稍后重试同一条 release，勿取消会话。",
        )
        if bid:
            tips.append(
                f"  python cli.py release --app-id {app} --platform ios "
                f"--version-name {ver} --build-id {bid} --execute --no-notify"
            )
    elif stuck_at == "version_state_blocked":
        tips.insert(0, "版本状态不允许再提审。")
        tips.append("到 ASC 核对该版本状态；已在审/已过审通常无需再跑提审。")
    elif bid and stuck_at in {
        "attach_build",
        "phased_release",
        "create_submission",
        "submission_id",
        "add_submission_item",
        "submit_submission",
    }:
        tips.insert(
            0,
            "上传/前期步骤多半已成功。勿再 upload 同一 IPA；"
            "再跑 release --execute 会尝试续跑（空会话加 item，或已有本版本 item 则只补 submitted）。",
        )
        tips.append(
            f"  python cli.py release --app-id {app} --platform ios "
            f"--version-name {ver} --build-id {bid} --execute --no-notify"
        )
    else:
        tips.insert(0, "需要时到 ASC 核对该版本/构建后，再决定是否重跑 --execute。")

    lines = ["进度："] + [f"  · {x}" for x in done] + ["", stuck_line, ""] + tips
    return "\n".join(lines)


def _fail_submit(
    plan: ReviewSubmitPlan,
    message: str,
    *,
    details: dict[str, Any],
    stuck_at: str,
    version_id: str | None = None,
    submission_id: str | None = None,
) -> ReviewSubmitResult:
    guide = format_ios_submit_failure_guide(
        plan,
        details,
        stuck_at=stuck_at,
        version_id=version_id,
        submission_id=submission_id,
    )
    return ReviewSubmitResult(
        ok=False,
        message=f"{message}\n\n—— 失败摘要 ——\n{guide}",
        version_id=version_id,
        submission_id=submission_id,
        details={**details, "failure_stuck_at": stuck_at},
    )

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


def count_review_submission_items(
    client: httpx.Client,
    headers: dict[str, str],
    submission_id: str,
) -> int | None:
    """返回会话 items 数量；读失败时返回 None。"""
    items = list_review_submission_items(client, headers, submission_id)
    if items is None:
        return None
    return len(items)


def list_review_submission_items(
    client: httpx.Client,
    headers: dict[str, str],
    submission_id: str,
) -> list[dict[str, Any]] | None:
    """列出会话 items；读失败返回 None。尽量带上 appStoreVersion 关系。"""
    resp = client.get(
        f"{ASC_BASE}/v1/reviewSubmissions/{submission_id}/items",
        headers=headers,
        params={"limit": 50, "include": "appStoreVersion"},
    )
    if resp.status_code >= 400:
        # include 不被接受时降级
        resp = client.get(
            f"{ASC_BASE}/v1/reviewSubmissions/{submission_id}/items",
            headers=headers,
            params={"limit": 50},
        )
    if resp.status_code >= 400:
        logger.warning(
            "list reviewSubmissionItems HTTP {}: {}",
            resp.status_code,
            _asc_error_message(resp),
        )
        return None
    payload = resp.json() or {}
    data = payload.get("data") or []
    items = [x for x in data if isinstance(x, dict)]
    for item in items:
        if _item_app_store_version_id(item):
            continue
        iid = item.get("id")
        if not iid:
            continue
        rel_resp = client.get(
            f"{ASC_BASE}/v1/reviewSubmissionItems/{iid}/relationships/appStoreVersion",
            headers=headers,
        )
        if rel_resp.status_code >= 400:
            continue
        rel_data = ((rel_resp.json() or {}).get("data")) or {}
        if isinstance(rel_data, dict) and rel_data.get("id"):
            rels = item.setdefault("relationships", {})
            rels["appStoreVersion"] = {"data": rel_data}
    return items


def _item_app_store_version_id(item: dict[str, Any]) -> str | None:
    rel = (item.get("relationships") or {}).get("appStoreVersion") or {}
    data = rel.get("data")
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    return None


def classify_open_submission_resume(
    client: httpx.Client,
    headers: dict[str, str],
    open_subs: list[dict[str, Any]],
) -> dict[str, Any]:
    """分类进行中的 reviewSubmission，决定能否安全续跑。

    返回字段：
    - ``states``: 供错误文案
    - ``mode``: ``None`` | ``empty`` | ``pending_version`` | ``unreadable``
    - ``submission_id``: 可续跑时的会话 id
    - ``items``: ``pending_version`` 时缓存的 items（待 version_id 核对）
    """
    states: list[tuple[str | None, str | None]] = [
        ((s.get("attributes") or {}).get("state"), s.get("id")) for s in open_subs
    ]
    out: dict[str, Any] = {
        "states": states,
        "mode": None,
        "submission_id": None,
        "items": None,
    }
    if not open_subs:
        return out
    if len(open_subs) != 1:
        return out
    only = open_subs[0]
    state = ((only.get("attributes") or {}).get("state") or "").upper()
    sid = only.get("id")
    if state != "READY_FOR_REVIEW" or not sid:
        return out
    items = list_review_submission_items(client, headers, str(sid))
    if items is None:
        out["mode"] = "unreadable"
        out["submission_id"] = str(sid)
        return out
    out["submission_id"] = str(sid)
    if len(items) == 0:
        out["mode"] = "empty"
        return out
    out["mode"] = "pending_version"
    out["items"] = items
    return out


def match_submission_item_for_version(
    items: list[dict[str, Any]],
    version_id: str,
) -> str | None:
    """若恰好 1 条 item 且指向本 version，返回 item id；否则 None。"""
    if len(items) != 1:
        return None
    only = items[0]
    linked = _item_app_store_version_id(only)
    if linked is None or linked != str(version_id):
        return None
    iid = only.get("id")
    return str(iid) if iid else None


def pick_resumable_empty_submission(
    client: httpx.Client,
    headers: dict[str, str],
    open_subs: list[dict[str, Any]],
) -> tuple[str | None, list[tuple[str | None, str | None]]]:
    """兼容旧调用：仅空 READY_FOR_REVIEW 可续跑。"""
    info = classify_open_submission_resume(client, headers, open_subs)
    states = info["states"]
    if info.get("mode") == "empty":
        return info.get("submission_id"), states
    return None, states


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
    resume_submission_id: str | None = None
    resume_skip_add_item = False
    pending_resume_items: list[dict[str, Any]] | None = None
    if open_subs:
        classified = classify_open_submission_resume(client, headers, open_subs)
        states = classified["states"]
        mode = classified.get("mode")
        if mode == "unreadable":
            return _fail_submit(
                plan,
                (
                    "已存在进行中的 reviewSubmission，但读取 items 失败；"
                    f"请稍后重试，勿取消会话：{states!r}"
                ),
                details={**details, "open_submissions": states},
                stuck_at="items_unreadable",
                submission_id=classified.get("submission_id"),
            )
        if mode == "empty":
            resume_submission_id = classified.get("submission_id")
            details["resumed_empty_submission"] = True
            details["open_submissions"] = states
            logger.info(
                "resume empty READY_FOR_REVIEW reviewSubmission id={}",
                resume_submission_id,
            )
        elif mode == "pending_version":
            # 待解析 version_id 后再核对 item 是否指向本版本
            resume_submission_id = classified.get("submission_id")
            pending_resume_items = list(classified.get("items") or [])
            details["open_submissions"] = states
        else:
            return _fail_submit(
                plan,
                f"已存在进行中的 reviewSubmission，拒绝重复提审：{states!r}",
                details={**details, "open_submissions": states},
                stuck_at="open_submission",
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
            return _fail_submit(
                plan,
                f"版本 {plan.version_string} 状态为 {raw_state}，不能再提审",
                details=details,
                stuck_at="version_state_blocked",
                version_id=version_id,
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
            return _fail_submit(
                plan,
                (
                    f"创建 appStoreVersions 失败 HTTP {resp.status_code}: "
                    f"{_asc_error_message(resp)}"
                ),
                details=details,
                stuck_at="create_version",
            )
        version_id = ((resp.json() or {}).get("data") or {}).get("id")
        details["created_version"] = True

    if not version_id:
        return _fail_submit(
            plan,
            "无法解析 appStoreVersion id",
            details=details,
            stuck_at="resolve_version_id",
        )
    details["version_id"] = version_id

    if pending_resume_items is not None:
        item_id = match_submission_item_for_version(pending_resume_items, version_id)
        if not item_id:
            return _fail_submit(
                plan,
                (
                    "已存在进行中的 reviewSubmission 且 items 无法安全续跑"
                    f"（需恰好 1 条且指向本版本 {version_id}）："
                    f"{details.get('open_submissions')!r}"
                ),
                details=details,
                stuck_at="open_submission",
                version_id=version_id,
                submission_id=resume_submission_id,
            )
        resume_skip_add_item = True
        details["resumed_existing_item"] = True
        details["resumed_item_id"] = item_id
        logger.info(
            "resume READY_FOR_REVIEW with existing item id={} submission={} version={}",
            item_id,
            resume_submission_id,
            version_id,
        )

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
            return _fail_submit(
                plan,
                f"关联构建失败 HTTP {resp.status_code}: {_asc_error_message(resp)}",
                details=details,
                stuck_at="attach_build",
                version_id=version_id,
            )
        details["build_attached"] = True

    # 默认开启分批（INACTIVE：过审或手动发布后走 7 天曲线，避免接近全量）
    phased = ensure_phased_release(client, headers, version_id)
    details["phased_release"] = phased
    if not phased.get("ok"):
        return _fail_submit(
            plan,
            f"提审前分批校验失败：{phased.get('message')}",
            details=details,
            stuck_at="phased_release",
            version_id=version_id,
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
        return _fail_submit(
            plan,
            f"写入 what's New 失败：{wn['error']}",
            details=details,
            stuck_at="whats_new",
            version_id=version_id,
        )

    # 出口合规：构建必须已声明 usesNonExemptEncryption
    if plan.build_id:
        compliance = check_build_export_compliance(client, headers, plan.build_id)
        details["export_compliance"] = compliance
        if not compliance.get("ok"):
            return _fail_submit(
                plan,
                compliance.get("message") or "出口合规检查未通过",
                details=details,
                stuck_at="export_compliance",
                version_id=version_id,
            )

    # reviewSubmissions：优先续跑空会话 / 已有本版本 item 的会话
    if resume_submission_id:
        submission_id = resume_submission_id
        details["submission_id"] = submission_id
        details["created_submission"] = False
    else:
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
            return _fail_submit(
                plan,
                (
                    f"创建 reviewSubmissions 失败 HTTP {resp.status_code}: "
                    f"{_asc_error_message(resp)}"
                ),
                details=details,
                stuck_at="create_submission",
                version_id=version_id,
            )
        submission_id = ((resp.json() or {}).get("data") or {}).get("id")
        if not submission_id:
            return _fail_submit(
                plan,
                "创建 reviewSubmissions 未返回 id",
                details=details,
                stuck_at="submission_id",
                version_id=version_id,
            )
        details["submission_id"] = submission_id
        details["created_submission"] = True

    if not resume_skip_add_item:
        # 加 item 前再读一次，降低连点/竞态双加 item 风险
        existing = list_review_submission_items(client, headers, str(submission_id))
        if existing is not None and existing:
            matched = match_submission_item_for_version(existing, version_id)
            if matched:
                resume_skip_add_item = True
                details["resumed_existing_item"] = True
                details["resumed_item_id"] = matched
            else:
                return _fail_submit(
                    plan,
                    (
                        "reviewSubmission 已有 items 但与本版本不匹配，拒绝继续："
                        f"submission={submission_id} version={version_id}"
                    ),
                    details=details,
                    stuck_at="open_submission",
                    version_id=version_id,
                    submission_id=submission_id,
                )

    if not resume_skip_add_item:
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
            return _fail_submit(
                plan,
                (
                    f"添加 reviewSubmissionItems 失败 HTTP {resp.status_code}: "
                    f"{_asc_error_message(resp)}"
                ),
                details=details,
                stuck_at="add_submission_item",
                version_id=version_id,
                submission_id=submission_id,
            )

    patch_body = {
        "data": {
            "type": "reviewSubmissions",
            "id": submission_id,
            "attributes": {"submitted": True},
        }
    }
    try:
        resp = client.patch(
            f"{ASC_BASE}/v1/reviewSubmissions/{submission_id}",
            headers=headers,
            json=patch_body,
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        verified = verify_submission_already_in_review(
            client,
            headers,
            submission_id=str(submission_id),
            version_id=str(version_id),
            version_string=plan.version_string,
        )
        if verified:
            details.update(verified)
            details["submission_state"] = verified.get("submission_state")
            return ReviewSubmitResult(
                ok=True,
                message=(
                    f"已提审（提交请求超时后复核确认）：version={plan.version_string} "
                    f"id={version_id} submission={submission_id} "
                    f"state={verified.get('submission_state')}"
                ),
                version_id=version_id,
                submission_id=submission_id,
                details=details,
            )
        return _fail_submit(
            plan,
            f"提交 reviewSubmissions(submitted=true) 传输失败: {exc}",
            details=details,
            stuck_at="submit_submission",
            version_id=version_id,
            submission_id=submission_id,
        )

    if resp.status_code >= 400:
        verified = verify_submission_already_in_review(
            client,
            headers,
            submission_id=str(submission_id),
            version_id=str(version_id),
            version_string=plan.version_string,
        )
        if verified:
            details.update(verified)
            details["submission_state"] = verified.get("submission_state")
            return ReviewSubmitResult(
                ok=True,
                message=(
                    f"已提审（提交接口返回错误后复核确认）：version={plan.version_string} "
                    f"id={version_id} submission={submission_id} "
                    f"state={verified.get('submission_state')}"
                ),
                version_id=version_id,
                submission_id=submission_id,
                details=details,
            )
        return _fail_submit(
            plan,
            (
                f"提交 reviewSubmissions(submitted=true) 失败 "
                f"HTTP {resp.status_code}: {_asc_error_message(resp)}"
            ),
            details=details,
            stuck_at="submit_submission",
            version_id=version_id,
            submission_id=submission_id,
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


def verify_submission_already_in_review(
    client: httpx.Client,
    headers: dict[str, str],
    *,
    submission_id: str,
    version_id: str,
    version_string: str,
) -> dict[str, Any] | None:
    """严格确认：本 submission + 本 version 均已进入审核。

    用于 ``submitted=true`` 超时/4xx 后的自检；任一条件不满足返回 None。
    """
    sub_resp = client.get(
        f"{ASC_BASE}/v1/reviewSubmissions/{submission_id}",
        headers=headers,
        params={"fields[reviewSubmissions]": "state,platform,submittedDate"},
    )
    if sub_resp.status_code >= 400:
        return None
    sub_attrs = ((sub_resp.json() or {}).get("data") or {}).get("attributes") or {}
    sub_state = str(sub_attrs.get("state") or "").upper()
    if sub_state not in {"WAITING_FOR_REVIEW", "IN_REVIEW"}:
        return None

    ver_resp = client.get(
        f"{ASC_BASE}/v1/appStoreVersions/{version_id}",
        headers=headers,
        params={
            "fields[appStoreVersions]": "versionString,appStoreState,appVersionState",
        },
    )
    if ver_resp.status_code >= 400:
        return None
    ver_data = (ver_resp.json() or {}).get("data") or {}
    ver_attrs = ver_data.get("attributes") or {}
    vs = str(ver_attrs.get("versionString") or "")
    if vs != str(version_string):
        return None
    ver_state = pick_version_state(ver_attrs)
    if ver_state not in {"WAITING_FOR_REVIEW", "IN_REVIEW"}:
        return None
    return {
        "submission_state": sub_state,
        "version_state": ver_state,
        "version_string": vs,
        "submittedDate": sub_attrs.get("submittedDate"),
        "verified_already_in_review": True,
    }


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
