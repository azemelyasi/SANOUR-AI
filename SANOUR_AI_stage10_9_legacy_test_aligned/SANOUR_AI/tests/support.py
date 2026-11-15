"""Shared test helpers. Not production code."""

import threading
import time

from sanour_ai.models.base import ModelResult

# Allowed caller-side overhead beyond the deadline, for scheduling jitter on slow machines.
TOLERANCE_SECONDS = 0.25


def deadline_in(seconds: float) -> float:
    return time.monotonic() + seconds


def wait_until(predicate, timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class GatedProvider:
    """Blocks inside generate() until release_all(). Records entries and peak concurrency."""

    def __init__(self, name: str = "mock") -> None:
        self.name = name
        self._gate = threading.Event()
        self._lock = threading.Lock()
        self.calls = 0
        self.active = 0
        self.peak = 0

    def generate(self, call):
        with self._lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            self._gate.wait()
        finally:
            with self._lock:
                self.active -= 1
        return ModelResult(text=f"gated:{call.prompt}", model_id=call.model_id)

    def release_all(self) -> None:
        self._gate.set()
