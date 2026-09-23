from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from app.config import get_settings

_configured = False


def setup_logging() -> None:
    """Logs under local logs/ (gitignored). INFO+ 默认同屏输出到 stderr（含上传进度）。"""
    global _configured
    if _configured:
        return
    settings = get_settings()
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    # 终端：无颜色、立即 flush，避免长传时「看起来没进度」
    logger.add(
        sys.stderr,
        level="INFO",
        colorize=False,
        enqueue=False,
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        log_dir / "app-upload-auto.log",
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
        level="DEBUG",
    )
    _configured = True


def echo_upload_progress(message: str) -> None:
    """上传进度专用：保证打到 stderr（即使 logging 未初始化也能见）。"""
    line = message if message.endswith("\n") else message + "\n"
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    try:
        logger.info(message)
    except Exception:  # noqa: BLE001
        pass
