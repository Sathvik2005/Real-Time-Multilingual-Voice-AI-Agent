"""
Latency tracker — context-manager and helper functions for
measuring and reporting per-stage pipeline latency.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, Optional

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class LatencyReport:
    session_id: str
    asr_ms: Optional[float] = None
    llm_ms: Optional[float] = None
    tts_ms: Optional[float] = None
    extras: Dict[str, float] = field(default_factory=dict)

    @property
    def total_ms(self) -> float:
        return (self.asr_ms or 0) + (self.llm_ms or 0) + (self.tts_ms or 0)

    def to_dict(self) -> Dict:
        return {
            "session_id": self.session_id,
            "ASR_ms": round(self.asr_ms, 1) if self.asr_ms else None,
            "LLM_ms": round(self.llm_ms, 1) if self.llm_ms else None,
            "TTS_ms": round(self.tts_ms, 1) if self.tts_ms else None,
            "Total_ms": round(self.total_ms, 1),
            **{k: round(v, 1) for k, v in self.extras.items()},
        }

    def log(self) -> None:
        logger.info("Pipeline_latency", **self.to_dict())


class StopWatch:
    """Simple high-resolution stopwatch."""

    def __init__(self) -> None:
        self._start: float = 0.0
        self._end: float = 0.0

    def start(self) -> "StopWatch":
        self._start = time.perf_counter()
        return self

    def stop(self) -> "StopWatch":
        self._end = time.perf_counter()
        return self

    @property
    def elapsed_ms(self) -> float:
        end = self._end if self._end else time.perf_counter()
        return (end - self._start) * 1000


@asynccontextmanager
async def measure(label: str, session_id: str = "") -> AsyncIterator[StopWatch]:
    """
    Async context manager that logs execution time of a code block.

    Usage::

        async with measure("LLM", session_id=sid) as sw:
            result = await llm.invoke(...)
        print(sw.elapsed_ms)
    """
    sw = StopWatch().start()
    try:
        yield sw
    finally:
        sw.stop()
        logger.info(
            f"{label}_latency",
            ms=round(sw.elapsed_ms, 1),
            session_id=session_id,
        )
