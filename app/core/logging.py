"""Logging configuration."""

from __future__ import annotations

import logging
import re
from logging.config import dictConfig

from app.config.settings import get_settings

_API_KEY_PATTERN = re.compile(r"(?i)(apiKey(?:=|%3D))([^&\s\"']+)")


def redact_sensitive_data(value: object) -> str:
    """对日志值中的外部 API Key 查询参数统一脱敏。"""
    return _API_KEY_PATTERN.sub(r"\1***", str(value))


def _redact_log_argument(value: object) -> object:
    raw = str(value)
    redacted = redact_sensitive_data(raw)
    return redacted if redacted != raw else value


class SensitiveDataFilter(logging.Filter):
    """在格式化前清理消息及参数，覆盖应用日志和第三方库日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_sensitive_data(record.msg)
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_redact_log_argument(item) for item in args)
        elif isinstance(args, dict):
            record.args = {key: _redact_log_argument(item) for key, item in args.items()}
        return True


class RedactingFormatter(logging.Formatter):
    """对包含异常堆栈的最终日志文本再次脱敏。"""

    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive_data(super().format(record))


def configure_logging() -> None:
    """Configure root logging based on settings.

    Kept intentionally simple for the skeleton; can be swapped for structured
    JSON logging (e.g. structlog) without touching call sites.
    """
    settings = get_settings()
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "()": "app.core.logging.RedactingFormatter",
                    "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                }
            },
            "filters": {
                "sensitive_data": {
                    "()": "app.core.logging.SensitiveDataFilter",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "filters": ["sensitive_data"],
                }
            },
            "loggers": {
                # httpx 的 INFO 请求日志会包含完整查询串；生产仅保留告警。
                "httpx": {"level": "WARNING", "propagate": True},
                "httpcore": {"level": "WARNING", "propagate": True},
            },
            "root": {
                "level": settings.log_level,
                "handlers": ["console"],
            },
        }
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
