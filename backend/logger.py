"""
logger.py - Structured Logging Configuration
=============================================
Provides consistent, structured logging for the Smart Fitness backend.

Usage:
    from logger import get_logger
    log = get_logger("pose_engine")
    log.info("Engine initialized", extra={"model": "lite", "fps": 30})
"""

import logging
import sys
import json
from datetime import datetime, timezone
from typing import Optional


class StructuredFormatter(logging.Formatter):
    """JSON-structured log formatter for machine-parseable logs."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)

        # Include exception info if present
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False)


class ColoredConsoleFormatter(logging.Formatter):
    """Human-readable colored console formatter."""

    COLORS = {
        "DEBUG": "\033[36m",      # Cyan
        "INFO": "\033[32m",       # Green
        "WARNING": "\033[33m",    # Yellow
        "ERROR": "\033[31m",      # Red
        "CRITICAL": "\033[41m",   # Red background
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname
        color = self.COLORS.get(level, self.COLORS["RESET"])
        reset = self.COLORS["RESET"]

        extra = ""
        if hasattr(record, "extra_fields") and record.extra_fields:
            extra = f" {record.extra_fields}"

        return (
            f"{color}[{level[:4]}]{reset} "
            f"{self.formatTime(record, '%H:%M:%S')} "
            f"{record.name}: {record.getMessage()}{extra}"
        )


_LOG_CONFIGURED = False


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    json_format: bool = False,
) -> None:
    """
    Configure root logger with consistent formatting.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional path to log file
        json_format: Use JSON structure output (vs colored console)
    """
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return

    logger = logging.getLogger("fitness")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    if json_format:
        console_handler.setFormatter(StructuredFormatter())
    else:
        console_handler.setFormatter(ColoredConsoleFormatter())
    console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(StructuredFormatter())
        file_handler.setLevel(logging.DEBUG)
        logger.addHandler(file_handler)

    _LOG_CONFIGURED = True


class ExtraAdapter(logging.LoggerAdapter):
    """LoggerAdapter that attaches extra fields to every log call."""

    def __init__(self, logger: logging.Logger, extra: Optional[dict] = None):
        super().__init__(logger, extra or {})

    def process(self, msg, kwargs):
        extra_fields = kwargs.pop("extra", {})
        if self.extra:
            extra_fields.update(self.extra)
        if extra_fields:
            kwargs["extra"] = {"extra_fields": extra_fields}
        return msg, kwargs


def get_logger(name: str, extra: Optional[dict] = None) -> ExtraAdapter:
    """
    Get a logger instance with the given name.

    Args:
        name: Logger name (e.g. "backend", "pose_engine")
        extra: Static extra fields to attach to every log call

    Returns:
        ExtraAdapter wrapping the named logger
    """
    # Ensure root logger is configured
    if not _LOG_CONFIGURED:
        setup_logging()

    logger = logging.getLogger(f"fitness.{name}")
    return ExtraAdapter(logger, extra=extra or {})
