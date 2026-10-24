"""Structured JSON logging with an explicit field allowlist.

Only the fields below ever reach the log line. Headers, cookies, bodies and
query strings are never passed in, and the formatter would drop them anyway.
"""

from __future__ import annotations

import json
import logging

ALLOWED_FIELDS = ("request_id", "method", "path", "status", "duration_ms")
LOGGER_NAME = "trendscend.api"


class AllowlistJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {"level": record.levelname, "event": record.getMessage()}
        for field in ALLOWED_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        return json.dumps(payload, default=str)


def configure_logging() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not any(isinstance(h.formatter, AllowlistJsonFormatter) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(AllowlistJsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger
