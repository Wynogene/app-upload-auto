"""Synology 分享链解析与下载接线（不改 ai_support）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.synology_share import extract_share_id, is_synology_share_url
from app.stores.apple_review_submit import (
    ReviewSubmitPlan,
    build_review_plan_steps,
    check_build_export_compliance,
    ensure_phased_release,
)


def test_extract_share_id_from_preview_url() -> None:
    url = "http://delivery.vaas.plus:5000/sharing/E78MniZ62"
    assert extract_share_id(url) == "E78MniZ62"
    assert is_synology_share_url(url)
    assert extract_share_id("E78MniZ62") == "E78MniZ62"
    assert extract_share_id("https://example.com/file.ipa") is None


def test_review_plan_mentions_phased_and_compliance() -> None:
    steps = build_review_plan_steps(
        ReviewSubmitPlan(
            app_store_app_id="1",
            version_string="1.0",
            build_id="b1",
        )
    )
    assert any("分批" in s for s in steps)
    assert any("合规" in s for s in steps)
    assert any("what's New" in s or "本地化" in s for s in steps)


class _Resp:
    def __init__(self, code: int, payload: dict[str, Any] | None = None, text: str = "") -> None:
        self.status_code = code
        self._payload = payload or {}
        self.text = text

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    def __init__(self, responses: list[_Resp]) -> None:
        self._responses = list(responses)

    def get(self, url: str, **kwargs: Any) -> _Resp:
        return self._responses.pop(0)

    def post(self, url: str, **kwargs: Any) -> _Resp:
        return self._responses.pop(0)


def test_ensure_phased_creates_when_missing() -> None:
    client = _Client(
        [
            _Resp(404, text="missing"),
            _Resp(
                201,
                {
                    "data": {
                        "id": "ph1",
                        "attributes": {"phasedReleaseState": "INACTIVE"},
                    }
                },
            ),
        ]
    )
    out = ensure_phased_release(client, {}, "ver1")  # type: ignore[arg-type]
    assert out["ok"] is True
    assert out.get("created") is True


def test_ensure_phased_reuses_existing() -> None:
    client = _Client(
        [
            _Resp(
                200,
                {
                    "data": {
                        "id": "ph0",
                        "attributes": {"phasedReleaseState": "INACTIVE"},
                    }
                },
            )
        ]
    )
    out = ensure_phased_release(client, {}, "ver1")  # type: ignore[arg-type]
    assert out["ok"] is True
    assert out.get("existed") is True


def test_export_compliance_missing_blocks() -> None:
    client = _Client(
        [
            _Resp(
                200,
                {
                    "data": {
                        "attributes": {
                            "usesNonExemptEncryption": None,
                            "processingState": "VALID",
                        }
                    }
                },
            )
        ]
    )
    out = check_build_export_compliance(client, {}, "b1")  # type: ignore[arg-type]
    assert out["ok"] is False
    assert "出口合规" in out["message"]


def test_export_compliance_ok() -> None:
    client = _Client(
        [
            _Resp(
                200,
                {
                    "data": {
                        "attributes": {
                            "usesNonExemptEncryption": False,
                            "processingState": "VALID",
                        }
                    }
                },
            )
        ]
    )
    out = check_build_export_compliance(client, {}, "b1")  # type: ignore[arg-type]
    assert out["ok"] is True


def test_resolve_synology_selects_ipa(monkeypatch, tmp_path: Path) -> None:
    from app.core import artifact_resolve as ar
    from app.models import Platform
    import app.core.synology_share as syn

    def fake_download(url: str, dest_dir: Path):
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / "app.ipa"
        out.write_bytes(b"ipa")
        return out, "ok syn"

    monkeypatch.setattr(syn, "download_share_to_dir", fake_download)
    monkeypatch.setattr(syn, "is_synology_share_url", lambda u: True)

    r = ar.resolve_artifact(
        platform=Platform.IOS,
        artifact_url="http://delivery.vaas.plus:5000/sharing/abc123",
        work_root=tmp_path / "work",
    )
    assert r.ok
    assert r.path is not None
    assert r.path.suffix == ".ipa"
    assert "synology" in r.source
