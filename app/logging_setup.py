from __future__ import annotations

from pathlib import Path

from loguru import logger

from app.config import get_settings

_configured = False


def setup_logging() -> None:
    """Logs only under local logs/ (gitignored). No remote sinks."""
    global _configured
    if _configured:
        return
    settings = get_settings()
    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        level="INFO",
        colorize=True,
    )
    logger.add(
        log_dir / "app-upload-auto.log",
        rotation="10 MB",
        retention="14 days",
        encoding="utf-8",
        level="DEBUG",
    )
    _configured = True
