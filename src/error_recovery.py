"""U One - structured errors and recovery policy (v2).

Every failure in the render pipeline becomes a UOneError carrying:
stage, scene, exit code, human reason, recovery hint, and the FFmpeg
stderr tail. RetryPolicy bounds automatic retries (default: 3 attempts)
so recovery never loops forever.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


class UOneError(RuntimeError):
    """Base for all structured U One failures."""

    def __init__(self, message: str, *, stage: str = "",
                 scene: Optional[int] = None,
                 exit_code: Optional[int] = None,
                 reason: str = "",
                 recovery_hint: str = "",
                 stderr_tail: str = ""):
        super().__init__(message)
        self.stage = stage
        self.scene = scene
        self.exit_code = exit_code
        self.reason = reason or message
        self.recovery_hint = recovery_hint
        self.stderr_tail = stderr_tail

    def describe(self) -> str:
        lines = []
        if self.stage:
            lines.append(f"Stage: {self.stage}")
        if self.scene is not None:
            lines.append(f"Scene: {self.scene}")
        if self.exit_code is not None:
            lines.append(f"Exit code: {self.exit_code}")
        lines.append(f"Reason: {self.reason}")
        if self.stderr_tail:
            lines.append("Details:")
            lines.append(self.stderr_tail)
        if self.recovery_hint:
            lines.append(f"Recovery: {self.recovery_hint}")
        return "\n".join(lines)


class SceneFailedError(UOneError):
    """A single scene could not be produced even after recovery."""


class ValidationError(UOneError):
    """A media file failed validation and cannot be used."""


class RenderCancelled(Exception):
    """User cancelled the render. Re-exported here for pipeline use."""
    pass


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    backoff_s: float = 1.0
    backoff_factor: float = 2.0

    def run(self, fn: Callable[[], T], *, what: str = "operation",
            on_retry=None) -> T:
        """Run fn(); retry up to max_attempts on UOneError/Exception.

        on_retry(attempt, error) is called before each retry.
        The last error is raised unchanged.
        """
        attempt = 0
        delay = self.backoff_s
        while True:
            attempt += 1
            try:
                return fn()
            except RenderCancelled:
                raise
            except Exception as e:  # noqa: BLE001 - policy applies to all
                if attempt >= self.max_attempts:
                    raise
                if on_retry:
                    try:
                        on_retry(attempt, e)
                    except Exception:
                        pass
                time.sleep(delay)
                delay *= self.backoff_factor


def is_fatal_disk_error(stderr_tail: str) -> bool:
    low = (stderr_tail or "").lower()
    return ("no space left" in low or "disk quota exceeded" in low
            or "not enough space" in low)
