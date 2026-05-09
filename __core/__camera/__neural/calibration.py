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
from typing import Callable

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


@dataclass
class AutoDetectResult:
    """Результат автоопределения опорных точек.

    Все обнаруженные точки уже сопоставлены с мировыми координатами
    в системе паттерна (x — столбцы, y — строки), origin в верхнем-левом углу.
    """
    pattern: str
    points: list[CalibrationPoint]
    cols: int
    rows: int
    image_size: tuple[int, int]
    notes: str = ""

    @property
    def corner_points(self) -> list[CalibrationPoint]:
        """4 угла паттерна: TL, TR, BR, BL (для предпросмотра в UI)."""
        if not self.points:
            return []
        if self.cols < 2 or self.rows < 2:
            return list(self.points[:4])
        c, r = self.cols, self.rows
        idx_tl = 0
        idx_tr = c - 1
        idx_br = r * c - 1
        idx_bl = (r - 1) * c
        return [
            self.points[idx_tl], self.points[idx_tr],
            self.points[idx_br], self.points[idx_bl],
        ]


def _build_world_grid(
    cols: int, rows: int, spacing_mm: float, origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> list[tuple[float, float]]:
    """Сгенерировать сетку мировых координат row-major (как cv2 возвращает)."""
    return [
        (origin_x + c * spacing_mm, origin_y + r * spacing_mm)
        for r in range(rows)
        for c in range(cols)
    ]


# --- Препроцессинг: несколько вариантов изображения для повышения шансов ---
# Реальные кадры с производства часто имеют блики, неравномерное освещение,
# шум — поэтому пробуем несколько препроцессингов и для каждого запускаем
# детектор; первый успех останавливает перебор.

def _preprocess_variants(frame: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Вернуть список (label, gray_image) для перебора детектором."""
    if frame is None or frame.size == 0:
        return []
    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame.copy()

    out: list[tuple[str, np.ndarray]] = [("orig", gray)]

    # CLAHE — выравнивание гистограммы по плиткам (защита от теней/бликов)
    try:
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        out.append(("clahe", clahe.apply(gray)))
    except Exception:
        pass

    # Инверсия — на случай светлого паттерна на тёмном фоне
    out.append(("inv", cv2.bitwise_not(gray)))

    # Лёгкое размытие — гасит мелкий шум
    try:
        out.append(("blur", cv2.GaussianBlur(gray, (3, 3), 0)))
    except Exception:
        pass

    # Адаптивная бинаризация — выделяет границы при неравномерном освещении
    try:
        th = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 21, 5,
        )
        out.append(("adaptive", th))
    except Exception:
        pass

    return out


# Стандартные размеры шахматных досок: число *внутренних* углов (cols, rows).
# Порядок: самые распространённые сначала, маленькие в конце — чтобы
# подмножество большого паттерна не «закрывало» полную доску раньше времени.
_CHESSBOARD_CANDIDATES: tuple[tuple[int, int], ...] = (
    (9, 6),  # самый распространённый — печатается на A4
    (8, 6), (10, 7), (11, 8), (12, 9), (13, 10), (14, 11), (15, 12),
    (7, 5), (8, 5), (10, 6), (11, 7), (12, 8),
    (7, 7), (8, 8), (9, 9),
    (6, 4), (5, 4), (4, 3),
    (6, 6), (5, 5), (4, 4),
    (3, 3),  # последний шанс
)

_CIRCLES_ASYM_CANDIDATES: tuple[tuple[int, int], ...] = (
    (4, 11),  # стандарт OpenCV asymmetric
    (3, 9), (5, 13), (4, 9), (3, 7), (5, 11), (6, 13),
)

_CIRCLES_SYM_CANDIDATES: tuple[tuple[int, int], ...] = (
    (7, 5), (4, 4), (5, 5), (6, 6), (8, 5), (3, 3),
    (9, 6), (5, 4), (6, 4),
)


def _try_chessboard(
    gray: np.ndarray, pattern_size: tuple[int, int],
) -> np.ndarray | None:
    """Попытка детектировать углы доски заданного размера.

    Сначала пытаемся современным findChessboardCornersSB (если есть в OpenCV);
    он robust к плохому освещению и не требует CLAHE. Иначе — классический
    findChessboardCorners + cornerSubPix.
    """
    sb = getattr(cv2, "findChessboardCornersSB", None)
    if sb is not None:
        try:
            found, corners = sb(gray, pattern_size)
            if found and corners is not None:
                return corners
        except Exception:
            pass

    flags = (
        cv2.CALIB_CB_ADAPTIVE_THRESH
        + cv2.CALIB_CB_NORMALIZE_IMAGE
        + cv2.CALIB_CB_FAST_CHECK
    )
    found, corners = cv2.findChessboardCorners(gray, pattern_size, flags=flags)
    if not found or corners is None:
        return None
    criteria = (
        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01,
    )
    return cv2.cornerSubPix(
        gray, corners, (11, 11), (-1, -1), criteria,
    )


def detect_chessboard(
    frame: np.ndarray,
    pattern_size: tuple[int, int] = (9, 6),
    square_mm: float = 5.0,
    *,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> AutoDetectResult | None:
    """Автоопределение углов шахматной доски (фиксированный pattern_size).

    Использует все варианты препроцессинга. ``pattern_size`` — число
    *внутренних* углов (cols, rows). Стандартная доска 10×7 клеток имеет
    ``pattern_size=(9, 6)``.
    """
    variants = _preprocess_variants(frame)
    if not variants:
        return None

    cols, rows = pattern_size
    used_label = ""
    corners = None
    for label, gray in variants:
        c = _try_chessboard(gray, pattern_size)
        if c is not None:
            corners = c
            used_label = label
            break
    if corners is None:
        return None

    # Финальный subpix всегда по оригиналу — даёт точность выше
    base_gray = variants[0][1]
    try:
        criteria = (
            cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01,
        )
        corners = cv2.cornerSubPix(
            base_gray, corners, (11, 11), (-1, -1), criteria,
        )
    except Exception:
        pass

    world = _build_world_grid(cols, rows, square_mm, origin_x, origin_y)
    pts: list[CalibrationPoint] = []
    for i, (wx, wy) in enumerate(world):
        cx, cy = corners[i, 0]
        pts.append(CalibrationPoint(
            px_x=float(cx), px_y=float(cy),
            world_x=float(wx), world_y=float(wy),
            label=f"CB{i}",
        ))
    return AutoDetectResult(
        pattern="chessboard",
        points=pts, cols=cols, rows=rows,
        image_size=(base_gray.shape[1], base_gray.shape[0]),
        notes=f"square={square_mm}mm pre={used_label}",
    )


def _bbox_coverage(
    corners: np.ndarray, image_w: int, image_h: int,
) -> float:
    """Доля площади кадра, занятая bounding box найденного паттерна (0..1)."""
    pts = corners.reshape(-1, 2)
    if pts.size == 0 or image_w <= 0 or image_h <= 0:
        return 0.0
    x_min, y_min = pts.min(axis=0)
    x_max, y_max = pts.max(axis=0)
    bw = max(0.0, float(x_max - x_min))
    bh = max(0.0, float(y_max - y_min))
    return (bw * bh) / float(image_w * image_h)


_MIN_PATTERN_COVERAGE = 0.04  # минимум 4% площади кадра


def find_chessboard_any_size(
    frame: np.ndarray,
    square_mm: float = 5.0,
    *,
    candidates: tuple[tuple[int, int], ...] | None = None,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    progress: Callable[[int, int, str], None] | None = None,
    min_coverage: float = _MIN_PATTERN_COVERAGE,
) -> AutoDetectResult | None:
    """Перебор популярных размеров шахматной доски без указания cols/rows.

    Стратегия: проходим список кандидатов, для каждого пробуем все варианты
    препроцессинга. Принимается *первый* размер, у которого:

      - детекция прошла на каком-то препроцессинге, И
      - bbox найденных углов покрывает минимум ``min_coverage`` площади кадра.

    Это защищает от ложного срабатывания на подмножестве большего паттерна
    (например, 3×3 в углу 9×6 доски) — такие фрагменты обычно занимают <4%
    кадра и отсеиваются.
    """
    variants = _preprocess_variants(frame)
    if not variants:
        return None

    base_gray = variants[0][1]
    H, W = base_gray.shape[:2]
    sizes = candidates or _CHESSBOARD_CANDIDATES
    total = len(sizes)

    for idx, ps in enumerate(sizes):
        if progress is not None:
            try:
                progress(idx, total, f"chessboard {ps[0]}x{ps[1]}")
            except Exception:
                pass
        cols, rows = ps
        if cols < 2 or rows < 2:
            continue
        for label, gray in variants:
            corners = _try_chessboard(gray, ps)
            if corners is None:
                continue

            cov = _bbox_coverage(corners, W, H)
            if cov < min_coverage:
                # Слишком маленький паттерн — вероятно подмножество большего
                continue

            try:
                criteria = (
                    cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
                    30, 0.01,
                )
                corners = cv2.cornerSubPix(
                    base_gray, corners, (11, 11), (-1, -1), criteria,
                )
            except Exception:
                pass

            world = _build_world_grid(
                cols, rows, square_mm, origin_x, origin_y,
            )
            pts: list[CalibrationPoint] = []
            for i, (wx, wy) in enumerate(world):
                cx, cy = corners[i, 0]
                pts.append(CalibrationPoint(
                    px_x=float(cx), px_y=float(cy),
                    world_x=float(wx), world_y=float(wy),
                    label=f"CB{i}",
                ))
            return AutoDetectResult(
                pattern="chessboard",
                points=pts, cols=cols, rows=rows,
                image_size=(W, H),
                notes=(
                    f"square={square_mm}mm size={cols}x{rows} "
                    f"pre={label} cov={cov:.0%}"
                ),
            )
    return None


def _try_circles(
    gray: np.ndarray, pattern_size: tuple[int, int], asymmetric: bool,
) -> np.ndarray | None:
    """Попытка детектировать сетку кругов заданного размера."""
    flags = (
        cv2.CALIB_CB_ASYMMETRIC_GRID
        if asymmetric
        else cv2.CALIB_CB_SYMMETRIC_GRID
    )
    try:
        found, centers = cv2.findCirclesGrid(gray, pattern_size, flags=flags)
    except Exception:
        return None
    if not found or centers is None:
        return None
    return centers


def _build_circles_points(
    centers: np.ndarray, cols: int, rows: int, spacing_mm: float,
    asymmetric: bool, origin_x: float, origin_y: float,
) -> list[CalibrationPoint]:
    """Построить CalibrationPoint-ы для найденных центров кругов."""
    pts: list[CalibrationPoint] = []
    if asymmetric:
        idx = 0
        for r in range(rows):
            for c in range(cols):
                cx, cy = centers[idx, 0]
                wx = origin_x + (2 * c + (r % 2)) * spacing_mm
                wy = origin_y + r * spacing_mm
                pts.append(CalibrationPoint(
                    px_x=float(cx), px_y=float(cy),
                    world_x=float(wx), world_y=float(wy),
                    label=f"AG{idx}",
                ))
                idx += 1
    else:
        world = _build_world_grid(cols, rows, spacing_mm, origin_x, origin_y)
        for i, (wx, wy) in enumerate(world):
            cx, cy = centers[i, 0]
            pts.append(CalibrationPoint(
                px_x=float(cx), px_y=float(cy),
                world_x=float(wx), world_y=float(wy),
                label=f"SG{i}",
            ))
    return pts


def detect_circles_grid(
    frame: np.ndarray,
    pattern_size: tuple[int, int] = (4, 11),
    spacing_mm: float = 5.0,
    *,
    asymmetric: bool = True,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
) -> AutoDetectResult | None:
    """Автоопределение сетки кругов с фиксированным pattern_size.

    Поддерживает симметричную и асимметричную раскладку. Использует
    препроцессинг для повышения шансов на детекцию в реальных условиях.
    """
    variants = _preprocess_variants(frame)
    if not variants:
        return None

    cols, rows = pattern_size
    used_label = ""
    centers = None
    for label, gray in variants:
        c = _try_circles(gray, pattern_size, asymmetric)
        if c is not None:
            centers = c
            used_label = label
            break
    if centers is None:
        return None

    base_gray = variants[0][1]
    pts = _build_circles_points(
        centers, cols, rows, spacing_mm, asymmetric, origin_x, origin_y,
    )
    return AutoDetectResult(
        pattern="circles_asymmetric" if asymmetric else "circles_symmetric",
        points=pts, cols=cols, rows=rows,
        image_size=(base_gray.shape[1], base_gray.shape[0]),
        notes=f"spacing={spacing_mm}mm pre={used_label}",
    )


def find_circles_grid_any_size(
    frame: np.ndarray,
    spacing_mm: float = 5.0,
    *,
    asymmetric: bool = True,
    candidates: tuple[tuple[int, int], ...] | None = None,
    origin_x: float = 0.0,
    origin_y: float = 0.0,
    progress: Callable[[int, int, str], None] | None = None,
    min_coverage: float = _MIN_PATTERN_COVERAGE,
) -> AutoDetectResult | None:
    """Перебор размеров сетки кругов без указания cols/rows."""
    variants = _preprocess_variants(frame)
    if not variants:
        return None

    base_gray = variants[0][1]
    H, W = base_gray.shape[:2]
    if candidates is None:
        candidates = (
            _CIRCLES_ASYM_CANDIDATES if asymmetric else _CIRCLES_SYM_CANDIDATES
        )
    total = len(candidates)
    pat_label = "circles_asymmetric" if asymmetric else "circles_symmetric"

    for idx, ps in enumerate(candidates):
        if progress is not None:
            try:
                progress(idx, total, f"{pat_label} {ps[0]}x{ps[1]}")
            except Exception:
                pass
        cols, rows = ps
        if cols < 2 or rows < 2:
            continue
        for label, gray in variants:
            centers = _try_circles(gray, ps, asymmetric)
            if centers is None:
                continue
            cov = _bbox_coverage(centers, W, H)
            if cov < min_coverage:
                continue
            pts = _build_circles_points(
                centers, cols, rows, spacing_mm, asymmetric,
                origin_x, origin_y,
            )
            return AutoDetectResult(
                pattern=pat_label,
                points=pts, cols=cols, rows=rows,
                image_size=(W, H),
                notes=(
                    f"spacing={spacing_mm}mm size={cols}x{rows} "
                    f"pre={label} cov={cov:.0%}"
                ),
            )
    return None


def detect_aruco_markers(
    frame: np.ndarray,
    marker_world_coords: dict[int, tuple[float, float]] | None = None,
    *,
    dictionary_id: int | None = None,
) -> AutoDetectResult | None:
    """Автоопределение ArUco-маркеров.

    ``marker_world_coords`` — словарь {id: (mm_x, mm_y)} с известными мировыми
    координатами центров маркеров. Если None — координаты не сопоставляются
    и вернётся None (точки без мировых координат бесполезны для калибровки).

    Если в OpenCV не подключён ``cv2.aruco`` (нет opencv-contrib), вернёт None.
    """
    if frame is None or frame.size == 0:
        return None
    aruco = getattr(cv2, "aruco", None)
    if aruco is None:
        return None
    if not marker_world_coords:
        return None

    gray = (
        cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if frame.ndim == 3 else frame
    )
    if dictionary_id is None:
        dictionary_id = getattr(aruco, "DICT_4X4_50", 0)
    try:
        if hasattr(aruco, "Dictionary_get"):
            dictionary = aruco.Dictionary_get(dictionary_id)
            params = aruco.DetectorParameters_create()
            corners, ids, _ = aruco.detectMarkers(gray, dictionary, parameters=params)
        else:
            dictionary = aruco.getPredefinedDictionary(dictionary_id)
            params = aruco.DetectorParameters()
            detector = aruco.ArucoDetector(dictionary, params)
            corners, ids, _ = detector.detectMarkers(gray)
    except Exception as exc:
        log.warning("aruco detection failed: %s", exc)
        return None

    if ids is None or len(ids) == 0:
        return None

    pts: list[CalibrationPoint] = []
    for i, marker_id in enumerate(ids.flatten()):
        if int(marker_id) not in marker_world_coords:
            continue
        c = corners[i].reshape(4, 2)
        cx = float(c[:, 0].mean())
        cy = float(c[:, 1].mean())
        wx, wy = marker_world_coords[int(marker_id)]
        pts.append(CalibrationPoint(
            px_x=cx, px_y=cy,
            world_x=float(wx), world_y=float(wy),
            label=f"ID{int(marker_id)}",
        ))

    if len(pts) < 4:
        return None

    return AutoDetectResult(
        pattern="aruco",
        points=pts, cols=0, rows=0,
        image_size=(gray.shape[1], gray.shape[0]),
        notes=f"matched={len(pts)}",
    )


def auto_detect_pattern(
    frame: np.ndarray,
    *,
    spacing_mm: float = 5.0,
    aruco_world_coords: dict[int, tuple[float, float]] | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> AutoDetectResult | None:
    """Полностью автоматический детектор без ручного указания cols/rows.

    Порядок попыток:
      1. Шахматная доска — все стандартные размеры (от 9×6 до 15×12)
      2. Асимметричная сетка кругов — все стандартные размеры
      3. Симметричная сетка кругов — все стандартные размеры
      4. ArUco-маркеры (если задано соответствие id → mm)

    Для каждого размера применяется набор препроцессингов (CLAHE / инверсия /
    blur / adaptive threshold), что покрывает плохое освещение и блики.

    Параметр ``spacing_mm`` — единственное, что должен знать оператор:
    шаг сетки в миллиметрах. Размер паттерна определяется автоматически.
    """
    def _wrap_progress(stage: str):
        if progress is None:
            return None
        def _cb(idx: int, total: int, label: str) -> None:
            try:
                progress(idx, total, f"{stage}: {label}")
            except Exception:
                pass
        return _cb

    res = find_chessboard_any_size(
        frame, square_mm=spacing_mm,
        progress=_wrap_progress("шахматная доска"),
    )
    if res is not None:
        return res

    res = find_circles_grid_any_size(
        frame, spacing_mm=spacing_mm, asymmetric=True,
        progress=_wrap_progress("круги (асим.)"),
    )
    if res is not None:
        return res

    res = find_circles_grid_any_size(
        frame, spacing_mm=spacing_mm, asymmetric=False,
        progress=_wrap_progress("круги (сим.)"),
    )
    if res is not None:
        return res

    if aruco_world_coords:
        res = detect_aruco_markers(frame, aruco_world_coords)
        if res is not None:
            return res

    return None
