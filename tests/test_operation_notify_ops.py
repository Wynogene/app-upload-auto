"""飞书操作结果短文案（不写商店）。"""

from __future__ import annotations

from app.core.notify_copy import format_operation_result_ops, format_operation_results_ops
from app.models import OperationResult, Platform, ReviewState


def test_ops_skips_android_follow() -> None:
    upload = OperationResult(
        ok=True,
        app_id="blurams",
        platform=Platform.ANDROID,
        message="已上传并发布到 `production`，versionCode=1959。已按 分阶段发布 5% 放量（使用配置默认放量）（很长续推说明）\n确认稳定后可续推，例如：\n  python cli.py release ...",
        details={
            "track": "production",
            "version_code": 1959,
            "rollout": "分阶段发布 5%",
            "rollout_fraction": 0.05,
            "watch_registered": True,
        },
    )
    follow = OperationResult(
        ok=True,
        app_id="blurams",
        platform=Platform.ANDROID,
        message="Android 已在 upload 阶段发布到 `production`（versionCode=1959）。",
        details={"notify_skip": True, "synthetic_follow": True},
    )
    text = format_operation_results_ops([upload, follow])
    assert "upload 阶段" not in text
    assert "进度以 Play Console" not in text
    assert "1959 @ production" in text
    assert "约5%" in text or "分阶段发布 5%" in text
    assert "已登记" in text
    assert "打开 Play Console" in text
    assert "确认稳定后可续推" not in text


def test_ops_ios_submit_short() -> None:
    r = OperationResult(
        ok=True,
        app_id="blurams",
        platform=Platform.IOS,
        review_state=ReviewState.WAITING_FOR_REVIEW,
        message=(
            "已提审：version=5.1049.127 id=xxx submission=yyy state=WAITING_FOR_REVIEW\n"
            "已登记 iOS 盯盘 version=5.1049.127（serve 会扫）。临时加盯：python cli.py watch ..."
        ),
        details={
            "version_name": "5.1049.127",
            "submission_state": "WAITING_FOR_REVIEW",
            "execute": True,
            "watch_registered": True,
        },
    )
    text = format_operation_result_ops(r) or ""
    assert "5.1049.127" in text
    assert "WAITING_FOR_REVIEW" in text
    assert "已登记" in text
    assert "打开 App Store Connect" in text
    assert "不会代点" not in text
    assert "临时加盯" not in text


def test_ops_failure_keeps_summary() -> None:
    r = OperationResult(
        ok=False,
        app_id="blurams",
        platform=Platform.IOS,
        message="出口合规检查未通过\n\n—— 失败摘要（未自动续跑）——\n卡在：出口合规",
        details={"execute": True},
    )
    text = format_operation_result_ops(r) or ""
    assert "失败" in text
    assert "失败摘要" in text
