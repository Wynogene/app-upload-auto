"""Resolve store upload artifacts from a local path, URL, or zip archive.

Safety: never silently pick among multiple candidates — fail with a name list.
"""

from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from loguru import logger

from app.config import ROOT_DIR
from app.models import Platform


def _new_work_dir() -> Path:
    base = ROOT_DIR / "data" / "artifacts"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="app-upload-art-", dir=str(base)))


_SKIP_NAME_PARTS = (
    "dsym",
    ".xcarchive",
    "readme",
    "symbols",
    "proguard",
    "mapping.txt",
)


@dataclass
class ArtifactResolveResult:
    ok: bool
    path: Path | None = None
    message: str = ""
    candidates: list[str] = field(default_factory=list)
    source: str = ""  # path | url | zip
    work_dir: Path | None = None  # temp dir to clean later (optional)


def _is_skipped(name: str) -> bool:
    # Only inspect the basename — pytest tmp dirs may contain substrings like "dsym".
    lower = Path(name).name.lower()
    return any(part in lower for part in _SKIP_NAME_PARTS)


def _collect_packages(root: Path, platform: Platform) -> list[Path]:
    suffix = ".aab" if platform == Platform.ANDROID else ".ipa"
    found: list[Path] = []
    if root.is_file():
        if root.suffix.lower() == suffix and not _is_skipped(root.name):
            found.append(root)
        return found
    for p in root.rglob(f"*{suffix}"):
        if p.is_file() and not _is_skipped(str(p)):
            found.append(p)
    return sorted(found, key=lambda x: str(x).lower())


def _score_candidate(
    path: Path,
    *,
    platform: Platform,
    package_or_bundle: str | None,
    version_hint: str | None,
) -> int:
    """Higher is better. Used only to break ties when multiple files exist."""
    name = path.name.lower()
    score = 0
    needle = (package_or_bundle or "").lower().strip()
    if needle and needle in name:
        score += 10
    if version_hint and version_hint.lower() in name:
        score += 5
    if platform == Platform.IOS and "appstore" in name:
        score += 3
    if platform == Platform.ANDROID and name.endswith(".aab"):
        score += 1
    return score


def select_package_file(
    root: Path,
    platform: Platform,
    *,
    package_or_bundle: str | None = None,
    version_hint: str | None = None,
) -> ArtifactResolveResult:
    """Pick a single .aab / .ipa under root (file or directory)."""
    candidates = _collect_packages(root, platform)
    names = [str(c) for c in candidates]
    if not candidates:
        kind = "AAB" if platform == Platform.ANDROID else "IPA"
        return ArtifactResolveResult(
            ok=False,
            message=f"未找到可上传的 {kind} 文件",
            candidates=names,
            source="zip" if root.is_dir() else "path",
        )
    if len(candidates) == 1:
        return ArtifactResolveResult(
            ok=True,
            path=candidates[0],
            message=f"已选定: {candidates[0].name}",
            candidates=names,
            source="path",
        )

    scored = [
        (
            _score_candidate(
                c,
                platform=platform,
                package_or_bundle=package_or_bundle,
                version_hint=version_hint,
            ),
            c,
        )
        for c in candidates
    ]
    scored.sort(key=lambda x: (-x[0], str(x[1]).lower()))
    best_score, best = scored[0]
    # Unique best score required
    if best_score > 0 and all(s < best_score for s, _ in scored[1:]):
        return ArtifactResolveResult(
            ok=True,
            path=best,
            message=f"多个候选中按包名/版本启发式唯一命中: {best.name}",
            candidates=names,
            source="zip",
        )

    listing = "\n".join(f"- {n}" for n in names)
    return ArtifactResolveResult(
        ok=False,
        message=(
            f"找到 {len(candidates)} 个候选包，无法唯一判定，已拒绝自动上传。\n"
            f"{listing}\n"
            "请改用明确的本地路径，或整理 zip 后只保留一个目标包。"
        ),
        candidates=names,
        source="zip",
    )


def _filename_from_url(url: str, content_disposition: str | None) -> str:
    if content_disposition:
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', content_disposition, re.I)
        if m:
            return Path(unquote(m.group(1).strip())).name
    path = unquote(urlparse(url).path)
    name = Path(path).name
    return name or "download.bin"


def download_url_to_dir(url: str, dest_dir: Path, *, timeout: float = 120.0) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            filename = _filename_from_url(url, resp.headers.get("content-disposition"))
            out = dest_dir / filename
            with out.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
    logger.info("downloaded artifact url -> {}", out)
    return out


def extract_zip(zip_path: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)
    return dest_dir


def resolve_artifact(
    *,
    platform: Platform,
    artifact_path: str | None = None,
    artifact_url: str | None = None,
    package_or_bundle: str | None = None,
    version_hint: str | None = None,
    work_root: Path | None = None,
) -> ArtifactResolveResult:
    """Resolve to a local .aab/.ipa path.

    Priority: existing local path → download url → if zip, extract and select.
    """
    if artifact_path:
        p = Path(artifact_path).expanduser()
        if not p.exists():
            return ArtifactResolveResult(
                ok=False,
                message=f"本地路径不存在: {p}",
                source="path",
            )
        if p.is_dir() or p.suffix.lower() == ".zip":
            work = work_root or _new_work_dir()
            work.mkdir(parents=True, exist_ok=True)
            root = p
            if p.suffix.lower() == ".zip":
                root = extract_zip(p, work / "unzipped")
            selected = select_package_file(
                root,
                platform,
                package_or_bundle=package_or_bundle,
                version_hint=version_hint,
            )
            selected.work_dir = work
            selected.source = "zip" if p.suffix.lower() == ".zip" else selected.source
            return selected

        expected = ".aab" if platform == Platform.ANDROID else ".ipa"
        if p.suffix.lower() != expected:
            return ArtifactResolveResult(
                ok=False,
                message=f"平台 {platform.value} 需要 {expected}，收到: {p.name}",
                source="path",
            )
        return ArtifactResolveResult(
            ok=True,
            path=p,
            message=f"使用本地文件: {p.name}",
            source="path",
        )

    if not artifact_url:
        return ArtifactResolveResult(
            ok=False,
            message="未提供 artifact_path 或 artifact_url",
        )

    work = work_root or _new_work_dir()
    work.mkdir(parents=True, exist_ok=True)
    try:
        downloaded = download_url_to_dir(artifact_url, work / "download")
    except Exception as exc:  # noqa: BLE001
        return ArtifactResolveResult(
            ok=False,
            message=f"下载失败: {exc}",
            source="url",
            work_dir=work,
        )

    if downloaded.suffix.lower() == ".zip":
        root = extract_zip(downloaded, work / "unzipped")
        selected = select_package_file(
            root,
            platform,
            package_or_bundle=package_or_bundle,
            version_hint=version_hint,
        )
        selected.work_dir = work
        selected.source = "zip"
        return selected

    expected = ".aab" if platform == Platform.ANDROID else ".ipa"
    if downloaded.suffix.lower() != expected:
        # Maybe bare file without extension — still try select under download dir
        selected = select_package_file(
            work / "download",
            platform,
            package_or_bundle=package_or_bundle,
            version_hint=version_hint,
        )
        selected.work_dir = work
        selected.source = "url"
        if selected.ok:
            return selected
        return ArtifactResolveResult(
            ok=False,
            message=(
                f"下载结果不是 {expected}（也不是可解析的 zip）: {downloaded.name}"
            ),
            source="url",
            work_dir=work,
            candidates=selected.candidates,
        )

    return ArtifactResolveResult(
        ok=True,
        path=downloaded,
        message=f"已下载: {downloaded.name}",
        source="url",
        work_dir=work,
    )


def cleanup_work_dir(work_dir: Path | None) -> None:
    if not work_dir:
        return
    try:
        shutil.rmtree(work_dir, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("cleanup work_dir failed {}: {}", work_dir, exc)
