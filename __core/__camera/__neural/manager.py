from __future__ import annotations

import importlib
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from typing import Any

from .base import FrameContext, NeuralMod
from .metrics import PipelineMetrics

log = logging.getLogger("neural.manager")


class _ModWrapper:
    """Обёртка над NeuralMod с измерением времени и обработкой ошибок."""

    __slots__ = ("mod", "name", "isolate", "timeout_s", "_skip_count", "_max_skip")

    def __init__(self, mod: NeuralMod, isolate: bool = False,
                 timeout_s: float = 0.0) -> None:
        self.mod = mod
        self.name = mod.name
        self.isolate = isolate
        self.timeout_s = timeout_s
        self._skip_count = 0
        self._max_skip = 30

    def is_circuit_broken(self) -> bool:
        """True если мод временно отключён из-за повторных ошибок."""
        return self._skip_count >= self._max_skip

    def trip(self) -> None:
        self._skip_count = self._max_skip

    def reset_circuit(self) -> None:
        self._skip_count = 0

    def half_open(self) -> bool:
        if self._skip_count > 0:
            self._skip_count -= 1
        return self._skip_count == 0


class NeuralManager:
    """Менеджер нейро-модов с метриками и опциональной изоляцией.

    Совместим со старым API: ``apply(frame, context)`` работает синхронно.
    Дополнительно: ``metrics``, ``set_mod_isolation()``, ``shutdown()``.
    """

    def __init__(self, mod_names: list[str], *, executor_workers: int = 2) -> None:
        self._mods: list[_ModWrapper] = []
        self._mod_lock = threading.RLock()
        self._metrics = PipelineMetrics()
        self._executor: ThreadPoolExecutor | None = None
        self._executor_workers = max(1, int(executor_workers))
        self._closed = False

        for mod_name in mod_names:
            mod = self._load_mod(mod_name)
            self._mods.append(_ModWrapper(mod=mod, isolate=False))
            self._metrics.get_or_create(mod_name)

    @property
    def metrics(self) -> PipelineMetrics:
        return self._metrics

    @property
    def loaded_mod_names(self) -> list[str]:
        with self._mod_lock:
            return [w.name for w in self._mods]

    def set_mod_isolation(self, mod_name: str, *, isolate: bool,
                          timeout_s: float = 0.0) -> bool:
        """Включить изоляцию мода (выполнение в отдельном потоке с таймаутом).

        Полезно для тяжёлых модов, чтобы зависание одного не вешало пайплайн.
        """
        with self._mod_lock:
            for w in self._mods:
                if w.name == mod_name:
                    w.isolate = isolate
                    w.timeout_s = max(0.0, float(timeout_s))
                    if isolate and self._executor is None:
                        self._executor = ThreadPoolExecutor(
                            max_workers=self._executor_workers,
                            thread_name_prefix="neuralmod",
                        )
                    log.info("Mod %s isolation: %s timeout=%.2fs",
                             mod_name, isolate, timeout_s)
                    return True
        return False

    def apply(self, frame: Any, context: FrameContext) -> Any:
        if self._closed:
            return frame
        out = frame
        t_pipe_start = time.perf_counter()
        with self._mod_lock:
            wrappers = list(self._mods)
        for w in wrappers:
            if w.is_circuit_broken():
                if not w.half_open():
                    continue
            out = self._apply_one(w, out, context)
        elapsed_ms = (time.perf_counter() - t_pipe_start) * 1000.0
        self._metrics.record_frame(elapsed_ms)
        return out

    def _apply_one(self, w: _ModWrapper, frame: Any,
                   context: FrameContext) -> Any:
        m = self._metrics.get_or_create(w.name)
        t0 = time.perf_counter()
        try:
            if w.isolate and self._executor is not None:
                fut = self._executor.submit(w.mod.apply, frame, context)
                try:
                    timeout = w.timeout_s if w.timeout_s > 0 else None
                    out = fut.result(timeout=timeout)
                except FutTimeout:
                    fut.cancel()
                    raise TimeoutError(
                        f"mod '{w.name}' exceeded {w.timeout_s:.2f}s",
                    )
            else:
                out = w.mod.apply(frame, context)
            elapsed = (time.perf_counter() - t0) * 1000.0
            m.record(elapsed)
            w.reset_circuit()
            return out if out is not None else frame
        except Exception as exc:
            m.record_error(str(exc))
            log.warning("mod %s failed: %s", w.name, exc)
            w._skip_count += 1
            if w._skip_count >= w._max_skip:
                log.error("mod %s circuit-broken after %d failures",
                          w.name, w._skip_count)
            return frame

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def reset_metrics(self) -> None:
        self._metrics = PipelineMetrics()
        for w in self._mods:
            self._metrics.get_or_create(w.name)

    @staticmethod
    def list_available() -> list[str]:
        from .mods import AVAILABLE_MODS
        return list(AVAILABLE_MODS)

    def _load_mod(self, mod_name: str) -> NeuralMod:
        if not mod_name.startswith("__"):
            raise ValueError(f"Mod name must start with '__': {mod_name}")

        module_path = f"__core.__camera.__neural.mods.{mod_name}"
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:
            raise RuntimeError(
                f"Cannot import neural mod '{mod_name}': {exc}",
            ) from exc

        mod_class = getattr(module, "Mod", None)
        if mod_class is None:
            raise RuntimeError(
                f"Neural mod '{mod_name}' must define class Mod",
            )

        mod_instance = mod_class()
        if not isinstance(mod_instance, NeuralMod):
            raise RuntimeError(
                f"Neural mod '{mod_name}' Mod class must inherit NeuralMod",
            )

        return mod_instance


class FrameQueueDispatcher:
    """Очередь кадров с политикой drop-latest при переполнении.

    Используется когда продюсер (камера) быстрее консьюмера (пайплайн).
    """

    def __init__(self, maxsize: int = 2) -> None:
        self._q: queue.Queue = queue.Queue(maxsize=max(1, int(maxsize)))
        self._dropped = 0
        self._lock = threading.Lock()

    def put_latest(self, item: Any) -> bool:
        """Положить кадр; если очередь заполнена — выбросить старейший."""
        try:
            self._q.put_nowait(item)
            return True
        except queue.Full:
            with self._lock:
                self._dropped += 1
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._q.put_nowait(item)
                return True
            except queue.Full:
                return False

    def get(self, timeout: float | None = None) -> Any:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def clear(self) -> None:
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
