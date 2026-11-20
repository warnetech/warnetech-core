"""Rotating file logger for daemon lifecycle events and status."""

import logging
import logging.handlers
from pathlib import Path


def setup_daemon_logger(
    log_path: Path,
    level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 5,
) -> logging.Logger:
    """
    Set up a rotating file logger for daemon operations.

    Args:
        log_path: Path to log file
        level: Logging level (default INFO)
        max_bytes: Max size per log file before rotation
        backup_count: Number of backup files to keep

    Returns:
        Configured logger instance
    """
    # Ensure parent directory exists
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("warnetech.daemon")
    logger.setLevel(level)

    # Rotating file handler
    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
    )
    handler.setLevel(level)

    # Formatter with timestamp and level
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    logger.addHandler(handler)
    return logger


def get_daemon_logger(name: str | None = None) -> logging.Logger:
    """
    Get daemon logger instance (after setup_daemon_logger has been called).

    Args:
        name: Optional sublogger name (e.g., "warnetech.daemon.supervisor")

    Returns:
        Logger instance
    """
    base = logging.getLogger("warnetech.daemon")
    if name:
        return base.getChild(name)
    return base
