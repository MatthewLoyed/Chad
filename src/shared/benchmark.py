import time
import logging
from contextlib import contextmanager
from typing import Optional

_START_TIME = time.perf_counter()
_logger = logging.getLogger("Benchmark")

def mark(label: str, level: int = logging.INFO):
    """Log a benchmark timestamp relative to module import time."""
    elapsed = time.perf_counter() - _START_TIME
    _logger.log(level, f"[ PERF ] {elapsed:.4f}s : {label}")

def mark_critical(label: str):
    """Explicitly log performance benchmark at WARNING level to force visibility."""
    mark(label, logging.WARNING)

@contextmanager
def timer(label: str, threshold_ms: Optional[float] = None):
    """
    Context manager to measure execution time of a block.
    :param label: Name of the operation.
    :param threshold_ms: Only log if duration exceeds this value (optional).
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        end = time.perf_counter()
        duration_ms = (end - start) * 1000
        if threshold_ms is None or duration_ms >= threshold_ms:
            _logger.info(f"[ TIMER ] {label} took {duration_ms:.2f}ms")

class Section:
    """Utility to measure multiple steps in a workflow."""
    def __init__(self, name: str):
        self.name = name
        self.start = time.perf_counter()
        self.last = self.start
        _logger.info(f"[ SECTION ] START: {name}")

    def step(self, label: str):
        now = time.perf_counter()
        step_duration = (now - self.last) * 1000
        total_duration = (now - self.start) * 1000
        _logger.info(f"[ SECTION ] {self.name} -> {label}: {step_duration:.2f}ms (Total: {total_duration:.2f}ms)")
        self.last = now

    def finish(self):
        total_duration = (time.perf_counter() - self.start) * 1000
        _logger.info(f"[ SECTION ] FINISH: {self.name} (Total: {total_duration:.2f}ms)")
