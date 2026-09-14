"""Shared models for store operations."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Platform(str, Enum):
    IOS = "ios"
    ANDROID = "android"


class ReviewState(str, Enum):
    UNKNOWN = "unknown"
    DRAFT = "draft"
    WAITING_FOR_REVIEW = "waiting_for_review"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    RELEASED = "released"
    CANCELED = "canceled"
    HALTED = "halted"


class UploadRequest(BaseModel):
    app_id: str
    platform: Platform
    version_name: str | None = None
    build_number: str | None = None
    artifact_path: str | None = None
    artifact_url: str | None = None
    whats_new: str | None = None
    # 多语言版本说明：{"zh-CN": "...", "en-US": "..."}（与 whats_new 二选一，勿混用）
    release_notes: dict[str, str] | None = None
    track: str | None = None  # Android only
    # Android: 允许正式轨道（仍需 CLI 显式传参）
    allow_production: bool = False
    operator_open_id: str | None = None


class SubmitRequest(BaseModel):
    """Android: promote an existing versionCode to a track (发布/提审)."""

    app_id: str
    platform: Platform
    version_name: str | None = None
    build_id: str | None = None  # iOS build id
    version_code: str | None = None  # Android versionCode
    track: str | None = None
    whats_new: str | None = None
    # 多语言版本说明：{"zh-CN": "...", "en-US": "..."}
    release_notes: dict[str, str] | None = None
    allow_production: bool = False
    # completed=发布/送审；draft=仅草稿不对外
    release_status: str = "completed"
    operator_open_id: str | None = None


class StatusRequest(BaseModel):
    app_id: str
    platform: Platform | None = None
    version_name: str | None = None
    # Android: 盯指定 versionCode（出现在某轨道时会写进 message）
    version_code: str | None = None


class OperationResult(BaseModel):
    ok: bool
    message: str
    platform: Platform | None = None
    app_id: str | None = None
    review_state: ReviewState = ReviewState.UNKNOWN
    details: dict[str, Any] = Field(default_factory=dict)


class ReviewStatus(BaseModel):
    app_id: str
    platform: Platform
    version_name: str | None = None
    state: ReviewState = ReviewState.UNKNOWN
    raw: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
