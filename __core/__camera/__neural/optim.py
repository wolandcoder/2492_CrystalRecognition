"""Утилиты профилирования и кэширования для нейро-модов.

Предоставляет:
- ``profile()`` — декоратор для замера времени выполнения метода.
- ``KernelCache`` — кэш морфологических ядер (cv2.getStructuringElement дорогой).
- ``GrayscaleCache`` — переиспользование преобразования BGR→Gray в рамках одного кадра.
- ``MetricsCollector`` — глобальный сборщик метрик для CLI ``main.py metrics``.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Deque, Iterator

import cv2
import numpy as np

log = logging.getLogger("optim")


@dataclass
class _ProfileEntry:
    name: str
    samples: Deque[float] = field(default_factory=lambda: deque(maxlen=120))
    total_ms: float = 0.0
    calls: int = 0

    def record(self, elapsed_ms: float) -> None:
        self.samples.append(elapsed_ms)
        self.total_ms += elapsed_ms
        self.calls += 1

    @property
    def avg_ms(self) -> float:
        if not self.samples:
            return 0.0
        return sum(self.samples) / len(self.samples)


class _Profiler:
    """Глобальный профилировщик. Потокобезопасный."""

    def __init__(self) -> None:
        self._entries: dict[str, _ProfileEntry] = {}
        self._lock = threading.RLock()
        self._enabled = False

    def enable(self, on: bool = True) -> None:
        self._enabled = bool(on)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record(self, name: str, elapsed_ms: float) -> None:
        with self._lock:
            entry = self._entries.get(name)
            if entry is None:
                entry = _ProfileEntry(name=name)
                self._entries[name] = entry
            entry.record(elapsed_ms)

    def report(self) -> list[dict]:
        with self._lock:
            rows = [
                {
                    "name": e.name,
                    "calls": e.calls,
                    "avg_ms": round(e.avg_ms, 3),
                    "total_ms": round(e.total_ms, 1),
                }
                for e in self._entries.values()
            ]
        rows.sort(key=lambda r: -r["total_ms"])
        return rows

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


_PROFILER = _Profiler()


def get_profiler() -> _Profiler:
    return _PROFILER


def profile(name: str | None = None) -> Callable:
    """Декоратор для замера времени метода.

    Включается/выключается через ``get_profiler().enable(True)``.
    Когда выключен — нулевой оверхед.
    """
    def decorator(fn: Callable) -> Callable:
        label = name or f"{fn.__module__}.{fn.__qualname__}"

        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not _PROFILER.enabled:
                return fn(*args, **kwargs)
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                elapsed = (time.perf_counter() - t0) * 1000.0
                _PROFILER.record(label, elapsed)

        return wrapper

    return decorator


@contextmanager
def profile_block(name: str) -> Iterator[None]:
    """Контекстный менеджер для замера произвольного блока кода."""
    if not _PROFILER.enabled:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        elapsed = (time.perf_counter() - t0) * 1000.0
        _PROFILER.record(name, elapsed)


class KernelCache:
    """LRU-кэш морфологических ядер OpenCV.

    ``cv2.getStructuringElement`` создаёт numpy-массив каждый раз;
    кэширование экономит ~5% на тяжёлом пайплайне детекции.
    """

    def __init__(self, max_size: int = 32) -> None:
        self._cache: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._max_size = max(4, int(max_size))
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0

    def get(self, shape: int, ksize: tuple[int, int]) -> np.ndarray:
        key = (int(shape), int(ksize[0]), int(ksize[1]))
        with self._lock:
            kernel = self._cache.get(key)
            if kernel is not None:
                self._cache.move_to_end(key)
                self._hits += 1
                return kernel
            kernel = cv2.getStructuringElement(int(shape), (int(ksize[0]), int(ksize[1])))
            self._cache[key] = kernel
            self._misses += 1
            if len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
            return kernel

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "size": len(self._cache),
                "hit_rate": round(self._hits / total, 3) if total else 0.0,
            }


_KERNEL_CACHE = KernelCache()


def get_kernel(shape: int, ksize: tuple[int, int]) -> np.ndarray:
    """Получить морфологическое ядро из кэша."""
    return _KERNEL_CACHE.get(shape, ksize)


class GrayscaleCache:
    """Кэш перевода BGR→Gray для одного кадра.

    Несколько модов делают cv2.cvtColor(frame, COLOR_BGR2GRAY) подряд,
    что дублирует вычисления. Привязывается к id(frame).
    """

    def __init__(self) -> None:
        self._frame_id: int | None = None
        self._gray: np.ndarray | None = None

    def get(self, frame: np.ndarray) -> np.ndarray:
        fid = id(frame)
        if self._frame_id == fid and self._gray is not None:
            return self._gray
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        self._frame_id = fid
        self._gray = gray
        return gray

    def invalidate(self) -> None:
        self._frame_id = None
        self._gray = None
