from __future__ import annotations

import math

import cv2
import numpy as np

from ..base import FrameContext, NeuralMod


class Mod(NeuralMod):
    """Выделяет ближайший к центру камеры элемент-верхушку (side == 'top').

    Зависит от ``__particle_centers`` (читает ``context.shared["particle_centers"]``).
    Может использовать ``particle_grid`` настройки для перевода px → world.
    """

    name = "__particle_centers_nearest"

    FONT = cv2.FONT_HERSHEY_SIMPLEX

    SCALE_X: float = 0.1
    SCALE_Y: float = 0.1
    UNIT: str = "mm"
    ORIGIN_OFFSET_X: float = 0.0
    ORIGIN_OFFSET_Y: float = 0.0
    INVERT_Y: bool = True

    COLOR_HIGHLIGHT: tuple = (0, 255, 100)
    COLOR_LINE: tuple = (0, 220, 0)
    COLOR_LABEL: tuple = (255, 255, 255)
    LINE_THICKNESS: int = 1
    HIGHLIGHT_THICKNESS: int = 2
    LABEL_FONT_SCALE: float = 0.44
    LABEL_THICKNESS: int = 1

    def __init__(self) -> None:
        self._cfg_loaded = False

    def apply(self, frame: object, context: FrameContext) -> object:
        if not self._cfg_loaded:
            self._load_cfg()
            self._cfg_loaded = True

        context.shared["_nearest_active"] = True

        particles = context.shared.get("particle_centers")
        if not particles:
            return frame

        fh, fw = frame.shape[:2]
        cx_cam, cy_cam = fw / 2.0, fh / 2.0

        pin_info = context.shared.get("particle_pin")
        pinned_tid = None
        if pin_info and pin_info.get("active") and pin_info.get("tid") is not None:
            pinned_tid = pin_info["tid"]

        best = None
        if pinned_tid is not None:
            for p in particles:
                if p.get("tid") == pinned_tid:
                    best = p
                    break

        if best is None:
            tops = [p for p in particles if p.get("side") == "top"]
            if not tops:
                return frame
            best = min(
                tops,
                key=lambda p: (p["cx"] - cx_cam) ** 2 + (p["cy"] - cy_cam) ** 2,
            )

        if pinned_tid is None:
            self._draw(frame, best, cx_cam, cy_cam)

        wx, wy = self._px_to_world(best["cx"], best["cy"], cx_cam, cy_cam)
        dist_px = math.hypot(best["cx"] - cx_cam, best["cy"] - cy_cam)
        dist_w = math.hypot(wx, wy)
        bearing = math.degrees(math.atan2(
            -(best["cy"] - cy_cam) if self.INVERT_Y else (best["cy"] - cy_cam),
            best["cx"] - cx_cam,
        ))

        context.shared["particle_nearest"] = {
            "tid": best["tid"],
            "px_x": best["cx"], "px_y": best["cy"],
            "x": round(wx, 2), "y": round(wy, 2),
            "w": round(best["w"] * self.SCALE_X, 2),
            "h": round(best["h"] * self.SCALE_Y, 2),
            "angle": best["angle"],
            "bearing_deg": round(bearing, 1),
            "dist": round(dist_w, 2),
            "dist_px": round(dist_px, 1),
            "unit": self.UNIT,
            "side": best["side"],
        }

        return frame

    def _draw(
        self, frame: np.ndarray, p: dict,
        cx_cam: float, cy_cam: float,
    ) -> None:
        px, py = int(p["cx"]), int(p["cy"])
        icx, icy = int(cx_cam), int(cy_cam)

        cv2.line(
            frame, (icx, icy), (px, py),
            self.COLOR_LINE, self.LINE_THICKNESS, cv2.LINE_AA,
        )

        w, h, angle = p["w"], p["h"], p["angle"]
        box = np.intp(cv2.boxPoints(((p["cx"], p["cy"]), (w, h), angle)))
        cv2.drawContours(
            frame, [box], 0, self.COLOR_HIGHLIGHT,
            self.HIGHLIGHT_THICKNESS, cv2.LINE_AA,
        )

        rad = math.radians(angle)
        cs, sn = math.cos(rad), math.sin(rad)
        hw, hh = w * 0.5, h * 0.5
        dx1, dy1 = cs * hw, sn * hw
        dx2, dy2 = -sn * hh, cs * hh
        cv2.line(frame,
                 (int(p["cx"] - dx1), int(p["cy"] - dy1)),
                 (int(p["cx"] + dx1), int(p["cy"] + dy1)),
                 self.COLOR_HIGHLIGHT, 1, cv2.LINE_AA)
        cv2.line(frame,
                 (int(p["cx"] - dx2), int(p["cy"] - dy2)),
                 (int(p["cx"] + dx2), int(p["cy"] + dy2)),
                 self.COLOR_HIGHLIGHT, 1, cv2.LINE_AA)

        wx, wy = self._px_to_world(p["cx"], p["cy"], cx_cam, cy_cam)
        dist_w = math.hypot(wx, wy)
        bearing = math.degrees(math.atan2(
            -(p["cy"] - cy_cam) if self.INVERT_Y else (p["cy"] - cy_cam),
            p["cx"] - cx_cam,
        ))

        lines = [
            f"XY: ({wx:+.1f}, {wy:+.1f}) {self.UNIT}",
            f"dist: {dist_w:.1f} {self.UNIT}",
            f"bearing: {bearing:+.1f} deg",
            f"tilt: {p['angle']:.1f} deg",
        ]

        ty = py - 16
        for line in reversed(lines):
            (tw, th), _ = cv2.getTextSize(
                line, self.FONT, self.LABEL_FONT_SCALE, self.LABEL_THICKNESS,
            )
            tx = px - tw // 2
            cv2.rectangle(
                frame, (tx - 2, ty - th - 2), (tx + tw + 2, ty + 2),
                (0, 0, 0), -1,
            )
            cv2.putText(
                frame, line, (tx, ty),
                self.FONT, self.LABEL_FONT_SCALE,
                self.COLOR_LABEL, self.LABEL_THICKNESS, cv2.LINE_AA,
            )
            ty -= th + 6

    def _px_to_world(
        self, px_x: float, px_y: float,
        cx_frame: float, cy_frame: float,
    ) -> tuple[float, float]:
        dx = (px_x - cx_frame) * self.SCALE_X + self.ORIGIN_OFFSET_X
        dy = (px_y - cy_frame) * self.SCALE_Y + self.ORIGIN_OFFSET_Y
        if self.INVERT_Y:
            dy = -dy
        return dx, dy

    def _load_cfg(self) -> None:
        try:
            import json
            from pathlib import Path
            p = Path(__file__).resolve().parents[4] / "config.json"
            if not p.exists():
                return
            raw = json.loads(p.read_text("utf-8"))
        except Exception:
            return

        grid_cfg = raw.get("particle_grid", {})
        for k, attr in {
            "scale_x": "SCALE_X", "scale_y": "SCALE_Y",
            "origin_offset_x": "ORIGIN_OFFSET_X",
            "origin_offset_y": "ORIGIN_OFFSET_Y",
        }.items():
            if k in grid_cfg:
                setattr(self, attr, float(grid_cfg[k]))

        if "unit" in grid_cfg:
            self.UNIT = str(grid_cfg["unit"])
        if "invert_y" in grid_cfg:
            self.INVERT_Y = bool(grid_cfg["invert_y"])

        nearest_cfg = raw.get("particle_nearest", {})
        for k, attr in {
            "color_highlight": "COLOR_HIGHLIGHT",
            "color_line": "COLOR_LINE",
            "color_label": "COLOR_LABEL",
        }.items():
            if k in nearest_cfg:
                setattr(self, attr, tuple(nearest_cfg[k]))
        for k, attr in {
            "line_thickness": "LINE_THICKNESS",
            "highlight_thickness": "HIGHLIGHT_THICKNESS",
            "label_thickness": "LABEL_THICKNESS",
        }.items():
            if k in nearest_cfg:
                setattr(self, attr, int(nearest_cfg[k]))
        if "label_font_scale" in nearest_cfg:
            self.LABEL_FONT_SCALE = float(nearest_cfg["label_font_scale"])
