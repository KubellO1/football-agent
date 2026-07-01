"""Logging configuration."""

from __future__ import annotations

import logging
from logging.config import dictConfig

from app.config.settings import get_settings


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
                    "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                }
            },
            "root": {
                "level": settings.log_level,
                "handlers": ["console"],
            },
        }
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
