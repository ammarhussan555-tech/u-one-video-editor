"""U One - application logging.

Logs are written to the per-user log directory (never the install folder).
API keys are ALWAYS redacted before they reach a log line.
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from .app_paths import log_dir

_KEY_PATTERNS = [
    re.compile(r"(?i)(pexels[_-]?api[_-]?key\s*[=:]\s*)(['\"]?)([A-Za-z0-9\-_]{8,})(['\"]?)"),
    re.compile(r"(?i)(pixabay[_-]?api[_-]?key\s*[=:]\s*)(['\"]?)([A-Za-z0-9\-_]{8,})(['\"]?)"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)(['\"]?)([A-Za-z0-9\-_]{10,})(['\"]?)"),
    re.compile(r"(?i)(key\s*[=:]\s*)(['\"])([A-Za-z0-9\-_]{16,})(['\"]\B)"),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pat in _KEY_PATTERNS:
        text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}***REDACTED***{m.group(4)}", text)
    return text


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            try:
                record.args = tuple(redact(str(a)) for a in record.args) \
                    if isinstance(record.args, tuple) else redact(str(record.args))
            except Exception:
                pass
        return True


_logger: Optional[logging.Logger] = None


def get_logger(project_id: str = "app") -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("u_one")
    logger.setLevel(logging.INFO)
    logger.addFilter(_RedactFilter())
    try:
        path = log_dir() / "u_one.log"
        handler = RotatingFileHandler(str(path), maxBytes=2_000_000, backupCount=3,
                                      encoding="utf-8")
    except Exception:
        handler = logging.StreamHandler()
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] [proj=%(project)s] [scene=%(scene)s] "
        "[stage=%(stage)s] %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)
    logger.propagate = False
    _logger = logger
    return logger


def log(project_id: str, stage: str, message: str, *,
        scene: Optional[int] = None, level: int = logging.INFO,
        error_type: str = "", retry: str = "", exit_code=None):
    extra = {"project": project_id, "scene": scene if scene is not None else "-",
             "stage": stage}
    bits = [message]
    if error_type:
        bits.append(f"error={error_type}")
    if retry:
        bits.append(f"retry={retry}")
    if exit_code is not None:
        bits.append(f"ffmpeg_exit={exit_code}")
    get_logger().log(level, " | ".join(bits), extra=extra)
