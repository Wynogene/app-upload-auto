"""iOS 提审失败摘要（只读文案，不写商店）。"""

from __future__ import annotations

from app.stores.apple_review_submit import (
    ReviewSubmitPlan,
    format_ios_submit_failure_guide,
)


def test_failure_guide_export_compliance() -> None:
    plan = ReviewSubmitPlan(
        app_store_app_id="1",
        version_string="5.1049.127",
        build_id="build-xyz",
        app_id="blurams",
    )
    text = format_ios_submit_failure_guide(
        plan,
        {
            "version_id": "ver-1",
            "created_version": True,
            "build_attached": True,
            "phased_release": {"ok": True},
            "whats_new": {"updated": ["en-US"]},
        },
        stuck_at="export_compliance",
        version_id="ver-1",
    )
    assert "失败摘要" not in text  # 函数本身只返回 guide 正文
    assert "已创建 App Store 版本" in text
    assert "已挂构建" in text
    assert "卡在：出口合规" in text
    assert "status --app-id blurams" in text
    assert "release --app-id blurams" in text
    assert "--build-id build-xyz" in text
    assert "--execute" in text


def test_failure_guide_open_submission_no_release_push() -> None:
    plan = ReviewSubmitPlan(
        app_store_app_id="1",
        version_string="1.0.0",
        build_id="b1",
        app_id="easelife",
    )
    text = format_ios_submit_failure_guide(
        plan,
        {},
        stuck_at="open_submission",
    )
    assert "进行中" in text or "审核会话" in text
    assert "勿轻易" in text or "不要并行" in text or "续跑" in text


def test_failure_guide_add_item_mentions_resume() -> None:
    plan = ReviewSubmitPlan(
        app_store_app_id="1",
        version_string="1.2.3",
        build_id="b9",
        app_id="boykeep",
    )
    text = format_ios_submit_failure_guide(
        plan,
        {"submission_id": "sub-1", "version_id": "ver-1"},
        stuck_at="add_submission_item",
        version_id="ver-1",
        submission_id="sub-1",
    )
    assert "勿再 upload" in text or "续跑" in text
    assert "release --app-id boykeep" in text
    assert "不会自动从中途续跑" not in text