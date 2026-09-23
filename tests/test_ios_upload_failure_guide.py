"""iOS 上传失败摘要 + upload-submit「勿再传」提示（仅文案）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from app.core import service as svc
from app.models import OperationResult, Platform, UploadRequest
from app.stores.apple_build_upload import (
    BuildUploadPlan,
    format_ios_upload_failure_guide,
)


def test_upload_failure_guide_wait_valid_no_reupload() -> None:
    plan = BuildUploadPlan(
        app_store_app_id="1",
        ipa_path=Path("x.ipa"),
        file_size=10,
        cf_bundle_short_version="5.1049.127",
        cf_bundle_version="127",
        file_name="x.ipa",
        app_id="blurams",
    )
    text = format_ios_upload_failure_guide(
        plan,
        {
            "build_upload_id": "up-1",
            "parts_uploaded": True,
            "marked_uploaded": True,
            "build_upload_complete": True,
            "build_upload_state": "COMPLETE",
        },
        stuck_at="wait_valid",
        build_upload_id="up-1",
    )
    assert "不要立刻整包再 upload" in text
    assert "release --app-id blurams" in text
    assert "不会自动断点续传" in text


def test_upload_and_submit_tip_when_submit_fails(monkeypatch) -> None:
    service = svc.AppReleaseService()
    monkeypatch.setattr(
        service,
        "_app",
        lambda _id: {"id": "blurams", "ios": {"enabled": True}, "allowed_open_ids": []},
    )
    monkeypatch.setattr(service, "_ensure_allowed", lambda *_a, **_k: None)

    def fake_upload(req, app):  # noqa: ANN001
        return OperationResult(
            ok=True,
            app_id="blurams",
            platform=Platform.IOS,
            message="uploaded",
            details={
                "build_id": "build-99",
                "version_name": "5.1049.127",
                "execute": True,
            },
        )

    def fake_submit(req, app):  # noqa: ANN001
        return OperationResult(
            ok=False,
            app_id="blurams",
            platform=Platform.IOS,
            message="submit failed",
            details={"execute": True},
        )

    service.apple = MagicMock()
    service.apple.upload.side_effect = fake_upload
    service.apple.submit.side_effect = fake_submit

    results = service.upload_and_submit(
        UploadRequest(
            app_id="blurams",
            platform=Platform.IOS,
            artifact_path="x.ipa",
            version_name="5.1049.127",
            execute=True,
        )
    )
    assert len(results) == 2
    assert results[0].ok is True
    assert results[1].ok is False
    assert "勿再 upload" in results[1].message
    assert "--build-id build-99" in results[1].message
    assert "release --app-id blurams" in results[1].message
