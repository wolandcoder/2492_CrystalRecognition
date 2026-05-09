from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque


@dataclass
class ModMetrics:
    """Метрики одного нейро-мода."""
    name: str
    last_ms: float = 0.0
    avg_ms: float = 0.0
    max_ms: float = 0.0
    calls: int = 0
    errors: int = 0
    last_error: str | None = None
    fps: float = 0.0

    _samples: Deque[float] = field(default_factory=lambda: deque(maxlen=60))
    _last_call_ts: float = 0.0

    def record(self, elapsed_ms: float) -> None:
        self.calls += 1
        self.last_ms = elapsed_ms
        self.max_ms = max(self.max_ms, elapsed_ms)
        self._samples.append(elapsed_ms)
        self.avg_ms = sum(self._samples) / len(self._samples)
        now = time.monotonic()
        if self._last_call_ts > 0:
            dt = now - self._last_call_ts
            if dt > 1e-6:
                inst_fps = 1.0 / dt
                self.fps = 0.85 * self.fps + 0.15 * inst_fps if self.fps > 0 else inst_fps
        self._last_call_ts = now

    def record_error(self, msg: str) -> None:
        self.errors += 1
        self.last_error = msg

    def reset(self) -> None:
        self.last_ms = 0.0
        self.avg_ms = 0.0
        self.max_ms = 0.0
        self.calls = 0
        self.errors = 0
        self.last_error = None
        self.fps = 0.0
        self._samples.clear()
        self._last_call_ts = 0.0


@dataclass
class PipelineMetrics:
    total_frames: int = 0
    dropped_frames: int = 0
    total_ms: float = 0.0
    avg_total_ms: float = 0.0
    pipeline_fps: float = 0.0
    mods: dict[str, ModMetrics] = field(default_factory=dict)

    _samples: Deque[float] = field(default_factory=lambda: deque(maxlen=60))
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _last_frame_ts: float = 0.0

    def record_frame(self, total_ms: float) -> None:
        with self._lock:
            self.total_frames += 1
            self.total_ms = total_ms
            self._samples.append(total_ms)
            self.avg_total_ms = sum(self._samples) / len(self._samples)
            now = time.monotonic()
            if self._last_frame_ts > 0:
                dt = now - self._last_frame_ts
                if dt > 1e-6:
                    inst = 1.0 / dt
                    self.pipeline_fps = (
                        0.85 * self.pipeline_fps + 0.15 * inst
                        if self.pipeline_fps > 0 else inst
                    )
            self._last_frame_ts = now

    def record_drop(self) -> None:
        with self._lock:
            self.dropped_frames += 1

    def get_or_create(self, name: str) -> ModMetrics:
        with self._lock:
            m = self.mods.get(name)
            if m is None:
                m = ModMetrics(name=name)
                self.mods[name] = m
            return m

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total_frames": self.total_frames,
                "dropped_frames": self.dropped_frames,
                "pipeline_fps": round(self.pipeline_fps, 1),
                "avg_total_ms": round(self.avg_total_ms, 2),
                "mods": [
                    {
                        "name": m.name,
                        "avg_ms": round(m.avg_ms, 2),
                        "max_ms": round(m.max_ms, 2),
                        "fps": round(m.fps, 1),
                        "calls": m.calls,
                        "errors": m.errors,
                        "last_error": m.last_error,
                    }
                    for m in self.mods.values()
                ],
            }
