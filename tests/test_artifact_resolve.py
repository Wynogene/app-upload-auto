"""artifact_resolve：zip / 目录选型（离线）。"""

from __future__ import annotations

import zipfile
from pathlib import Path

from app.core.artifact_resolve import resolve_artifact, select_package_file
from app.models import Platform


def test_select_single_aab(tmp_path: Path) -> None:
    f = tmp_path / "app.aab"
    f.write_bytes(b"x")
    r = select_package_file(tmp_path, Platform.ANDROID)
    assert r.ok and r.path == f


def test_select_rejects_ambiguous_aabs(tmp_path: Path) -> None:
    (tmp_path / "a.aab").write_bytes(b"1")
    (tmp_path / "b.aab").write_bytes(b"2")
    r = select_package_file(tmp_path, Platform.ANDROID)
    assert not r.ok
    assert len(r.candidates) == 2
    assert "无法唯一判定" in r.message


def test_select_prefers_package_name_match(tmp_path: Path) -> None:
    (tmp_path / "other.aab").write_bytes(b"1")
    hit = tmp_path / "com.blurams.ipc-release.aab"
    hit.write_bytes(b"2")
    r = select_package_file(
        tmp_path, Platform.ANDROID, package_or_bundle="com.blurams.ipc"
    )
    assert r.ok and r.path == hit


def test_select_skips_dsym_style_names(tmp_path: Path) -> None:
    (tmp_path / "app.ipa").write_bytes(b"1")
    (tmp_path / "app.dSYM.ipa").write_bytes(b"2")  # still .ipa but skipped by name
    r = select_package_file(tmp_path, Platform.IOS)
    assert r.ok and r.path and r.path.name == "app.ipa"


def test_resolve_from_zip(tmp_path: Path) -> None:
    inner = tmp_path / "payload"
    inner.mkdir()
    aab = inner / "release.aab"
    aab.write_bytes(b"aab")
    zpath = tmp_path / "pkg.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(aab, arcname="payload/release.aab")

    work = tmp_path / "work"
    r = resolve_artifact(
        platform=Platform.ANDROID,
        artifact_path=str(zpath),
        work_root=work,
    )
    assert r.ok and r.path is not None
    assert r.path.name == "release.aab"
    assert r.source == "zip"


def test_resolve_missing_path(tmp_path: Path) -> None:
    r = resolve_artifact(
        platform=Platform.ANDROID,
        artifact_path=str(tmp_path / "nope.aab"),
    )
    assert not r.ok
