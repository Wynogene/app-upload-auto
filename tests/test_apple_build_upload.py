"""ASC Build Upload / reviewSubmit：离线单测（不碰真实商店）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.models import UploadRequest, Platform
from app.stores.apple_build_upload import (
    execute_build_upload,
    file_md5_hex,
    plan_build_upload,
)
from app.stores.apple_review_submit import (
    ReviewSubmitPlan,
    build_review_plan_steps,
    execute_review_submit,
    list_open_review_submissions,
)


def test_upload_request_execute_defaults_false() -> None:
    req = UploadRequest(app_id="x", platform=Platform.IOS)
    assert req.execute is False


def test_file_md5_and_plan(tmp_path: Path) -> None:
    ipa = tmp_path / "app.ipa"
    ipa.write_bytes(b"hello-ipa")
    md5 = file_md5_hex(ipa)
    assert len(md5) == 32
    plan = plan_build_upload(
        app_store_app_id="123",
        ipa_path=ipa,
        version_name="1.2.3",
        build_number="99",
    )
    assert plan.file_size == 9
    assert plan.md5_hex == md5
    assert plan.cf_bundle_short_version == "1.2.3"
    assert plan.cf_bundle_version == "99"
    assert any("buildUploads" in s for s in plan.steps)


def test_plan_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        plan_build_upload(
            app_store_app_id="1",
            ipa_path=tmp_path / "no.ipa",
            version_name="1.0",
            build_number="1",
        )


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: dict[str, Any] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or ""

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """按调用顺序返回预设响应；记录请求便于断言。"""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("POST", url))
        return self._next()

    def patch(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("PATCH", url))
        return self._next()

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(("GET", url))
        return self._next()

    def request(self, method: str, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append((method.upper(), url))
        return self._next()

    def _next(self) -> _FakeResponse:
        if not self._responses:
            return _FakeResponse(500, text="no more fixtures")
        return self._responses.pop(0)


def test_execute_build_upload_happy_path(tmp_path: Path) -> None:
    ipa = tmp_path / "app.ipa"
    content = b"0123456789abcdef"
    ipa.write_bytes(content)
    plan = plan_build_upload(
        app_store_app_id="app1",
        ipa_path=ipa,
        version_name="2.0.0",
        build_number="10",
    )
    client = _FakeClient(
        [
            _FakeResponse(201, {"data": {"id": "bu-1"}}),
            _FakeResponse(
                201,
                {
                    "data": {
                        "id": "buf-1",
                        "attributes": {
                            "uploadOperations": [
                                {
                                    "method": "PUT",
                                    "url": "https://upload.example/part0",
                                    "offset": 0,
                                    "length": len(content),
                                    "requestHeaders": [
                                        {"name": "Content-Type", "value": "application/octet-stream"}
                                    ],
                                }
                            ]
                        },
                    }
                },
            ),
            _FakeResponse(200),  # PUT
            _FakeResponse(200),  # PATCH uploaded
            _FakeResponse(
                200,
                {
                    "data": {
                        "attributes": {"state": {"state": "COMPLETE"}},
                    }
                },
            ),
            _FakeResponse(
                200,
                {
                    "data": [
                        {
                            "id": "build-99",
                            "attributes": {
                                "version": "10",
                                "processingState": "VALID",
                            },
                        }
                    ]
                },
            ),
        ]
    )
    result = execute_build_upload(
        client=client,  # type: ignore[arg-type]
        headers={"Authorization": "Bearer x"},
        plan=plan,
        poll_seconds=0.01,
        poll_timeout_seconds=5.0,
    )
    assert result.ok is True
    assert result.build_id == "build-99"
    assert result.build_upload_id == "bu-1"
    assert any(c[0] == "PUT" for c in client.calls)


def test_execute_build_upload_create_fails(tmp_path: Path) -> None:
    ipa = tmp_path / "app.ipa"
    ipa.write_bytes(b"x")
    plan = plan_build_upload(
        app_store_app_id="app1",
        ipa_path=ipa,
        version_name="1.0",
        build_number="1",
    )
    client = _FakeClient(
        [
            _FakeResponse(
                403,
                {"errors": [{"code": "FORBIDDEN", "detail": "no"}]},
                text="forbidden",
            )
        ]
    )
    result = execute_build_upload(
        client=client,  # type: ignore[arg-type]
        headers={},
        plan=plan,
    )
    assert result.ok is False
    assert "创建 buildUploads 失败" in result.message


def test_build_review_plan_steps() -> None:
    plan = ReviewSubmitPlan(
        app_store_app_id="1",
        version_string="1.0.0",
        build_id="b1",
    )
    steps = build_review_plan_steps(plan)
    assert any("reviewSubmission" in s for s in steps)


def test_list_open_review_submissions_filters() -> None:
    client = _FakeClient(
        [
            _FakeResponse(
                200,
                {
                    "data": [
                        {
                            "id": "s1",
                            "attributes": {"state": "WAITING_FOR_REVIEW"},
                        },
                        {
                            "id": "s2",
                            "attributes": {"state": "COMPLETE"},
                        },
                    ]
                },
            )
        ]
    )
    open_subs = list_open_review_submissions(
        client,  # type: ignore[arg-type]
        {},
        "app1",
    )
    assert len(open_subs) == 1
    assert open_subs[0]["id"] == "s1"


def test_execute_review_submit_blocks_open_submission() -> None:
    client = _FakeClient(
        [
            _FakeResponse(
                200,
                {
                    "data": [
                        {
                            "id": "sub-open",
                            "attributes": {"state": "IN_REVIEW"},
                        }
                    ]
                },
            )
        ]
    )
    plan = ReviewSubmitPlan(
        app_store_app_id="app1",
        version_string="3.0.0",
        build_id="build-1",
        whats_new_by_locale={"en-US": "fixes"},
        fallback_whats_new="fixes",
    )
    result = execute_review_submit(
        client=client,  # type: ignore[arg-type]
        headers={},
        plan=plan,
    )
    assert result.ok is False
    assert "拒绝重复提审" in result.message


def test_headers_list_to_dict_via_put_ops(tmp_path: Path) -> None:
    """uploadOperations 的 requestHeaders 列表应能转成 dict。"""
    from app.stores.apple_build_upload import _headers_list_to_dict

    assert _headers_list_to_dict(
        [{"name": "Content-Length", "value": "10"}]
    ) == {"Content-Length": "10"}
    assert _headers_list_to_dict({"A": 1}) == {"A": "1"}
