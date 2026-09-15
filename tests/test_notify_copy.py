"""运营向通知正文单测（风格 D）。"""

from __future__ import annotations

from app.core.notify_copy import format_review_status_ops
from app.models import Platform, ReviewState, ReviewStatus


def test_android_phased_ops_copy() -> None:
    s = ReviewStatus(
        app_id="easelife",
        platform=Platform.ANDROID,
        version_name="10428",
        state=ReviewState.RELEASED,
        message=(
            "lifecycle[production]: PUBLISHED (已上架对用户开放（含分批放量）) "
            "codes=[10428] name=10428 (5.1054.4.428)\n"
            "target versionCode=10428 出现在: production:inProgress\n"
            "production: 10428 (5.1054.4.428) codes=[10428] status=inProgress "
            "rollout=0.05 ← target\n"
            "进度以 Play Console（正式版 / 发布概览）为准；本工具不会代点「发布」。"
        ),
    )
    text = format_review_status_ops(s)
    assert "easelife" in text
    assert "Android" in text
    assert "5.1054.4.428" in text
    assert "版本码" not in text
    assert "约5%" in text or "约 5%" in text
    assert "Play Console" in text
    assert "无需操作" not in text
    assert "动作" not in text  # 分批观察中不写动作
    assert "play.google.com/console" in text
    assert "lifecycle[" not in text


def test_ios_phased_ops_copy_uses_build_number() -> None:
    s = ReviewStatus(
        app_id="easelife",
        platform=Platform.IOS,
        version_name="5.1054.52",
        state=ReviewState.RELEASED,
        raw={
            "build": {"version": "5.1054.52.1"},
            "app_store_app_id": "1588050679",
        },
        message=(
            "5.1054.52：已上线；发布方式=过审后自动发布；构建 5.1054.52.1：可用；"
            "分批：ACTIVE 第1天≈1%（分批进行中）；开始于2026-09-14T21:11:27Z"
        ),
    )
    text = format_review_status_ops(s)
    assert "5.1054.52.1" in text
    assert "商店版本" not in text
    assert "第1天≈1%" in text or "第1天≈1" in text
    assert "不可自定义" in text or "按天自动抬升" in text
    assert "无需操作" not in text
    assert "动作" not in text
    assert "appstoreconnect.apple.com/apps/1588050679" in text
    ver_line = next(x for x in text.splitlines() if x.startswith("版本"))
    assert "5.1054.52.1" in ver_line


def test_android_needs_publish_has_action() -> None:
    s = ReviewStatus(
        app_id="easelife",
        platform=Platform.ANDROID,
        state=ReviewState.APPROVED,
        message=(
            "lifecycle[production]: APPROVED_NOT_PUBLISHED (自管式待发布) "
            "codes=[10428]\n"
            "production: 10428 (5.1054.4.428) codes=[10428] status=completed"
        ),
    )
    text = format_review_status_ops(s)
    assert "动作" in text
    assert "发布" in text
