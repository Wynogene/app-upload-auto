"""Serve healthcheck without a console window (use pythonw.exe).

Read-only: GET local /health; optionally restart serve; Feishu only to owner.
Does not write Google Play / App Store.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _health_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status == 200 and '"ok"' in body and "true" in body
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _restart_serve() -> None:
    start = ROOT / "scripts" / "windows" / "start-serve-watch.ps1"
    # Same process tree via powershell -WindowStyle Hidden; avoid visible console.
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(start),
        ],
        cwd=str(ROOT),
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _notify(url: str, status: str) -> None:
    sys.path.insert(0, str(ROOT))
    from app.notify.feishu_notify import Notifier

    Notifier().notify_owner(
        title="serve 盯盘健康检查",
        markdown=(
            f"本机 serve 曾不可用（{url}）。当前状态：**{status}**。\n"
            "仅私聊你；未写商店。"
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:18088/health",
        help="health endpoint",
    )
    parser.add_argument(
        "--notify-if-down",
        action="store_true",
        help="DM owner if down (personal-only)",
    )
    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="do not restart serve when down",
    )
    args = parser.parse_args()

    if _health_ok(args.url):
        return 0

    recovered = False
    if not args.no_restart:
        _restart_serve()
        time.sleep(6)
        recovered = _health_ok(args.url)

    if args.notify_if_down:
        try:
            _notify(args.url, "已恢复" if recovered else "仍不可用")
        except Exception:  # noqa: BLE001
            pass

    return 0 if recovered else 1


if __name__ == "__main__":
    raise SystemExit(main())
