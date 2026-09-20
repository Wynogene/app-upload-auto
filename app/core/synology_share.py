"""群晖分享链下载（不改 ai_support）。

优先：本仓库自实现 FileStation API（凭据来自 .env）。
可选：若已安装依赖且能 import，可复用 ai_support.utils_synology。

现网版本链接::

    http://delivery.vaas.plus:5000/sharing/<share_id>
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from loguru import logger

from app.config import ROOT_DIR, get_settings

_SHARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def extract_share_id(url_or_id: str) -> str | None:
    """从分享 URL 或纯 share_id 解析出 id；无法识别则 None。"""
    raw = (url_or_id or "").strip()
    if not raw:
        return None
    if _SHARE_ID_RE.fullmatch(raw) and "://" not in raw and "/" not in raw:
        return raw
    try:
        parsed = urlparse(raw)
    except Exception:  # noqa: BLE001
        return None
    path = (parsed.path or "").rstrip("/")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    share_id = parts[-1]
    if not _SHARE_ID_RE.fullmatch(share_id):
        return None
    lower_path = path.lower()
    host = (parsed.hostname or "").lower()
    if "/sharing/" in lower_path or "sharing" in lower_path:
        return share_id
    if "delivery" in host or "synology" in host or "vaas" in host:
        return share_id
    return None


def is_synology_share_url(url: str) -> bool:
    raw = url or ""
    return extract_share_id(raw) is not None and (
        "/sharing/" in raw.lower()
        or "delivery.vaas.plus" in raw.lower()
        or "synology" in raw.lower()
    )


def _synology_settings() -> tuple[str, str, str]:
    s = get_settings()
    base = (getattr(s, "synology_base_url", None) or "").strip().rstrip("/")
    user = (getattr(s, "synology_username", None) or "").strip()
    password = (getattr(s, "synology_password", None) or "").strip()
    return base, user, password


def _download_via_filestation(
    share_id: str,
    dest_dir: Path,
    *,
    base_url: str,
    username: str,
    password: str,
) -> tuple[Path | None, str]:
    """本仓库实现：login → Sharing.getinfo → Download。"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(120.0, connect=30.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        login = client.get(
            f"{base_url}/webapi/auth.cgi",
            params={
                "api": "SYNO.API.Auth",
                "version": "6",
                "method": "login",
                "account": username,
                "passwd": password,
                "session": "FileStation",
                "format": "sid",
            },
        )
        login.raise_for_status()
        login_body = login.json()
        if not login_body.get("success"):
            return None, f"群晖登录失败: {login_body}"
        sid = (login_body.get("data") or {}).get("sid")
        if not sid:
            return None, "群晖登录成功但未返回 sid"

        info = client.get(
            f"{base_url}/webapi/entry.cgi",
            params={
                "api": "SYNO.FileStation.Sharing",
                "version": 2,
                "method": "getinfo",
                "id": share_id,
                "_sid": sid,
            },
        )
        info.raise_for_status()
        info_body = info.json()
        file_path = (info_body.get("data") or {}).get("path")
        if not file_path:
            return None, f"无法解析分享路径 share_id={share_id}: {info_body}"

        file_name = str(file_path).rstrip("/").split("/")[-1] or f"{share_id}.bin"
        dest = dest_dir / file_name
        with client.stream(
            "GET",
            f"{base_url}/webapi/entry.cgi",
            params={
                "api": "SYNO.FileStation.Download",
                "version": 2,
                "method": "download",
                "path": file_path,
                "_sid": sid,
            },
        ) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                for chunk in resp.iter_bytes():
                    if chunk:
                        f.write(chunk)
        logger.info("synology FileStation {} -> {}", share_id, dest)
        return dest, f"已从群晖分享下载: {dest.name}（share_id={share_id}）"


def _candidate_ai_support_roots() -> list[Path]:
    settings = get_settings()
    roots: list[Path] = []
    configured = (getattr(settings, "ai_support_root", None) or "").strip()
    if configured:
        roots.append(Path(configured).expanduser())
    roots.append(Path(r"F:\NNE\ai_support"))
    roots.append(ROOT_DIR.parent / "NNE" / "ai_support")
    roots.append(ROOT_DIR.parent / "ai_support")
    out: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        key = str(r.resolve()) if r.exists() else str(r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _load_download_release_package() -> Any:
    """可选：动态加载 ai_support.utils_synology（不改对方代码）。"""
    for root in _candidate_ai_support_roots():
        if not (root / "utils_synology.py").is_file():
            continue
        root_s = str(root.resolve())
        if root_s not in sys.path:
            sys.path.insert(0, root_s)
        try:
            import utils_synology  # type: ignore

            fn = getattr(utils_synology, "download_release_package", None)
            if callable(fn):
                logger.info("synology: optional ai_support loader ok at {}", root)
                return fn
        except Exception as exc:  # noqa: BLE001
            logger.debug("synology: skip ai_support import from {}: {}", root, exc)
    return None


def download_share_to_dir(url_or_id: str, dest_dir: Path) -> tuple[Path | None, str]:
    """下载分享包到 dest_dir，返回 (本地路径, 说明)。"""
    share_id = extract_share_id(url_or_id)
    if not share_id:
        return None, f"无法从链接解析 share_id: {url_or_id!r}"

    dest_dir.mkdir(parents=True, exist_ok=True)

    base, user, password = _synology_settings()
    if base and user and password:
        try:
            return _download_via_filestation(
                share_id,
                dest_dir,
                base_url=base,
                username=user,
                password=password,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("synology FileStation failed, try ai_support: {}", exc)

    fn = _load_download_release_package()
    if fn is None:
        return None, (
            "群晖下载未配置：请在 .env 设置 SYNOLOGY_BASE_URL / SYNOLOGY_USERNAME / "
            "SYNOLOGY_PASSWORD（推荐），或安装 ai_support 依赖后设 AI_SUPPORT_ROOT。"
        )

    cwd_before = Path.cwd()
    try:
        import os

        for root in _candidate_ai_support_roots():
            if (root / "utils_synology.py").is_file():
                os.chdir(root)
                break
        download_path, file_name, msg = fn(share_id)
    except Exception as exc:  # noqa: BLE001
        return None, f"ai_support Synology 下载异常: {exc}"
    finally:
        try:
            import os

            os.chdir(cwd_before)
        except Exception:  # noqa: BLE001
            pass

    if not download_path:
        return None, f"Synology 下载失败: {msg}"

    src = Path(download_path)
    if not src.is_file():
        for root in _candidate_ai_support_roots():
            cand = root / download_path
            if cand.is_file():
                src = cand
                break
    if not src.is_file():
        return None, f"Synology 返回路径不存在: {download_path}（{msg}）"

    name = file_name or src.name
    dest = dest_dir / Path(name).name
    shutil.copy2(src, dest)
    logger.info("synology ai_support share {} -> {}", share_id, dest)
    return dest, f"已从群晖分享下载: {dest.name}（share_id={share_id}，via ai_support）"
