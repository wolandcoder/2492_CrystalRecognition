"""Калибровка пиксель→миллиметр через перспективное преобразование.

Поддерживает два режима:
1. **Линейный (legacy)**: ``wx = (px - cx) * scale_x + offset_x``.
2. **Перспективный**: 3x3-матрица гомографии, вычисляется по 4 опорным точкам
   с известными мировыми координатами.

Матрица сохраняется в ``config.json`` → ``particle_grid.perspective_matrix``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("calibration")


@dataclass
class CalibrationPoint:
    """Опорная точка калибровки: пара (px, mm)."""
    px_x: float
    px_y: float
    world_x: float
    world_y: float
    label: str = ""


@dataclass
class CalibrationResult:
    """Результат калибровки."""
    matrix: np.ndarray
    inverse_matrix: np.ndarray
    rms_error_mm: float
    points: list[CalibrationPoint] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "matrix": self.matrix.flatten().tolist(),
            "rms_error_mm": float(self.rms_error_mm),
            "points": [
                {
                    "px_x": p.px_x, "px_y": p.px_y,
                    "world_x": p.world_x, "world_y": p.world_y,
                    "label": p.label,
                }
                for p in self.points
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CalibrationResult | None":
        try:
            mat = np.asarray(data["matrix"], dtype=np.float64).reshape(3, 3)
            inv = np.linalg.inv(mat)
            pts = [
                CalibrationPoint(
                    px_x=float(p["px_x"]), px_y=float(p["px_y"]),
                    world_x=float(p["world_x"]), world_y=float(p["world_y"]),
                    label=str(p.get("label", "")),
                )
                for p in data.get("points", [])
            ]
            return cls(
                matrix=mat,
                inverse_matrix=inv,
                rms_error_mm=float(data.get("rms_error_mm", 0.0)),
                points=pts,
            )
        except (KeyError, ValueError, np.linalg.LinAlgError) as exc:
            log.warning("Cannot restore calibration: %s", exc)
            return None


class PerspectiveCalibrator:
    """Утилита для построения и применения перспективной матрицы."""

    @staticmethod
    def compute(points: list[CalibrationPoint]) -> CalibrationResult:
        """Построить матрицу гомографии по >=4 точкам.

        Использует ``cv2.findHomography`` с RANSAC при >4 точках,
        иначе ``cv2.getPerspectiveTransform``.
        """
        if len(points) < 4:
            raise ValueError(
                f"Нужно минимум 4 точки калибровки, получено {len(points)}",
            )

        src = np.array(
            [[p.px_x, p.px_y] for p in points],
            dtype=np.float32,
        )
        dst = np.array(
            [[p.world_x, p.world_y] for p in points],
            dtype=np.float32,
        )

        if len(points) == 4:
            mat = cv2.getPerspectiveTransform(src, dst).astype(np.float64)
        else:
            mat, _ = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
            if mat is None:
                raise ValueError("Не удалось вычислить гомографию")
            mat = mat.astype(np.float64)

        try:
            inv = np.linalg.inv(mat)
        except np.linalg.LinAlgError as exc:
            raise ValueError(
                f"Матрица вырожденная: {exc}",
            ) from exc

        rms = PerspectiveCalibrator._rms_error(mat, points)
        log.info("Calibration: %d points, RMS=%.3fmm", len(points), rms)
        return CalibrationResult(
            matrix=mat,
            inverse_matrix=inv,
            rms_error_mm=rms,
            points=list(points),
        )

    @staticmethod
    def _rms_error(matrix: np.ndarray,
                   points: list[CalibrationPoint]) -> float:
        if not points:
            return 0.0
        errors = []
        for p in points:
            wx, wy = PerspectiveCalibrator.apply(matrix, p.px_x, p.px_y)
            dx = wx - p.world_x
            dy = wy - p.world_y
            errors.append(dx * dx + dy * dy)
        return float(np.sqrt(np.mean(errors)))

    @staticmethod
    def apply(matrix: np.ndarray, px_x: float,
              px_y: float) -> tuple[float, float]:
        """Px → mm через гомографию."""
        v = matrix @ np.array([px_x, px_y, 1.0])
        if abs(v[2]) < 1e-12:
            return 0.0, 0.0
        return float(v[0] / v[2]), float(v[1] / v[2])

    @staticmethod
    def apply_inverse(inv_matrix: np.ndarray, world_x: float,
                      world_y: float) -> tuple[float, float]:
        """Mm → px через обратную матрицу."""
        v = inv_matrix @ np.array([world_x, world_y, 1.0])
        if abs(v[2]) < 1e-12:
            return 0.0, 0.0
        return float(v[0] / v[2]), float(v[1] / v[2])


def load_calibration(config_path: Path) -> CalibrationResult | None:
    """Загрузить сохранённую калибровку из config.json."""
    try:
        if not config_path.exists():
            return None
        raw = json.loads(config_path.read_text("utf-8"))
        cfg = raw.get("particle_grid", {})
        persp = cfg.get("perspective")
        if not persp or not cfg.get("use_perspective", False):
            return None
        return CalibrationResult.from_dict(persp)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.warning("load_calibration: %s", exc)
        return None


def save_calibration(config_path: Path, result: CalibrationResult,
                     enable: bool = True) -> bool:
    """Сохранить калибровку в config.json (раздел particle_grid)."""
    try:
        raw = {}
        if config_path.exists():
            raw = json.loads(config_path.read_text("utf-8"))
        cfg = raw.setdefault("particle_grid", {})
        cfg["perspective"] = result.to_dict()
        cfg["use_perspective"] = bool(enable)
        config_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            "utf-8",
        )
        log.info("Calibration saved: %s (enable=%s)", config_path, enable)
        return True
    except (OSError, ValueError) as exc:
        log.error("save_calibration failed: %s", exc)
        return False


def disable_calibration(config_path: Path) -> bool:
    """Отключить перспективную калибровку (оставить матрицу, но не использовать)."""
    try:
        if not config_path.exists():
            return False
        raw = json.loads(config_path.read_text("utf-8"))
        cfg = raw.setdefault("particle_grid", {})
        cfg["use_perspective"] = False
        config_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2),
            "utf-8",
        )
        return True
    except (OSError, ValueError):
        return False
