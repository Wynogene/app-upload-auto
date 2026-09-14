"""App Store Connect 状态映射。

Apple 有两套并行字段，需要都兼容：
  * `appStoreState`    —— 旧字段，逐步弃用
  * `appVersionState`  —— 新字段，取值集合略有不同
两个字段的含义一致，取到哪个都能解析。

另外 build 的 `processingState` 表示「二进制是否已被 Apple 处理通过」，
上传后卡在这一步最常见（尤其签名不对时），所以单独映射。
"""

from __future__ import annotations

from app.models import ReviewState

# appStoreState / appVersionState 取值 → 本项目统一的 ReviewState
_VERSION_STATE_MAP: dict[str, ReviewState] = {
    # 还没提交 / 还在编辑
    "PREPARE_FOR_SUBMISSION": ReviewState.DRAFT,
    # 提审路上
    "WAITING_FOR_REVIEW": ReviewState.WAITING_FOR_REVIEW,
    "IN_REVIEW": ReviewState.IN_REVIEW,
    "PROCESSING_FOR_APP_STORE": ReviewState.IN_REVIEW,
    "PROCESSING_FOR_DISTRIBUTION": ReviewState.IN_REVIEW,
    # 审核通过
    "ACCEPTED": ReviewState.APPROVED,
    "PENDING_APPLE_RELEASE": ReviewState.APPROVED,
    # 审核通过，但设置为「手动发布」——等开发者自己点发布
    "PENDING_DEVELOPER_RELEASE": ReviewState.APPROVED,
    # 已上线
    "READY_FOR_SALE": ReviewState.RELEASED,
    "READY_FOR_DISTRIBUTION": ReviewState.RELEASED,
    "PREORDER_READY_FOR_SALE": ReviewState.RELEASED,
    # 被拒 / 失败
    "REJECTED": ReviewState.REJECTED,
    "METADATA_REJECTED": ReviewState.REJECTED,
    "INVALID_BINARY": ReviewState.REJECTED,
    # 开发者自行撤回
    "DEVELOPER_REJECTED": ReviewState.CANCELED,
    # 已下架 / 被新版本取代
    "REMOVED_FROM_SALE": ReviewState.HALTED,
    "REPLACED_WITH_NEW_VERSION": ReviewState.HALTED,
}

# 供提示语使用的中文说明
_VERSION_STATE_LABEL: dict[str, str] = {
    "PREPARE_FOR_SUBMISSION": "准备提交（尚未提审）",
    "WAITING_FOR_REVIEW": "等待审核",
    "IN_REVIEW": "审核中",
    "PROCESSING_FOR_APP_STORE": "Apple 处理中",
    "PROCESSING_FOR_DISTRIBUTION": "Apple 处理中",
    "ACCEPTED": "审核通过",
    "PENDING_APPLE_RELEASE": "已通过，等待 Apple 发布",
    "PENDING_DEVELOPER_RELEASE": "已通过，等待你手动发布（手动发布模式）",
    "READY_FOR_SALE": "已上线",
    "READY_FOR_DISTRIBUTION": "已上线",
    "PREORDER_READY_FOR_SALE": "已上线（可预订）",
    "REJECTED": "已被拒绝",
    "METADATA_REJECTED": "元数据被拒",
    "INVALID_BINARY": "二进制无效",
    "DEVELOPER_REJECTED": "开发者已撤回",
    "REMOVED_FROM_SALE": "已下架",
    "REPLACED_WITH_NEW_VERSION": "已被新版本取代",
    "WAITING_FOR_EXPORT_COMPLIANCE": "等待出口合规确认",
    "PENDING_CONTRACT": "等待合同生效",
    "NOT_APPLICABLE": "不适用",
}

_BUILD_STATE_MAP: dict[str, ReviewState] = {
    "PROCESSING": ReviewState.IN_REVIEW,  # Apple 仍在处理二进制
    "VALID": ReviewState.APPROVED,  # 二进制可用
    "INVALID": ReviewState.REJECTED,  # 二进制无效（多为签名/权限问题）
    "FAILED": ReviewState.REJECTED,
}

_BUILD_STATE_LABEL: dict[str, str] = {
    "PROCESSING": "Apple 处理中",
    "VALID": "可用",
    "INVALID": "无效（多为签名或权限问题）",
    "FAILED": "处理失败",
}

# 上传后 build 有可能长时间停在 PROCESSING，超过这个时长值得提醒人工介入
BUILD_PROCESSING_HINT = "build 长期停在 Apple 处理中，请到 ASC → TestFlight 查看具体报错"


def pick_version_state(attrs: dict) -> str | None:
    """优先用新字段 appVersionState，回退 appStoreState。"""
    return attrs.get("appVersionState") or attrs.get("appStoreState")


def map_version_state(raw: str | None) -> ReviewState:
    return _VERSION_STATE_MAP.get((raw or "").upper(), ReviewState.UNKNOWN)


def describe_version_state(raw: str | None) -> str:
    if not raw:
        return "未知状态"
    return _VERSION_STATE_LABEL.get(raw.upper(), raw)


def map_build_processing_state(raw: str | None) -> ReviewState:
    return _BUILD_STATE_MAP.get((raw or "").upper(), ReviewState.UNKNOWN)


def describe_build_processing_state(raw: str | None) -> str:
    if not raw:
        return "未知"
    return _BUILD_STATE_LABEL.get(raw.upper(), raw)


def is_under_review(raw: str | None) -> bool:
    """是否正在审核流程中（用于「避免重复提审」的防呆）。"""
    return map_version_state(raw) in {ReviewState.WAITING_FOR_REVIEW, ReviewState.IN_REVIEW}


def is_live_or_approved(raw: str | None) -> bool:
    """是否已上线或已通过（用于「该版本无需再提审」的判断）。"""
    return map_version_state(raw) in {ReviewState.RELEASED, ReviewState.APPROVED}
