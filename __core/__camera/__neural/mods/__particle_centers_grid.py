from __future__ import annotations

import cv2
import numpy as np

from ..base import FrameContext, NeuralMod


class Mod(NeuralMod):
    """Координатная сетка и подписи мировых координат для каждого элемента.

    Зависит от ``__particle_centers`` — читает ``context.shared["particle_centers"]``.
    Параметры координатной плоскости загружаются из ``config.json`` → ``particle_grid``.
    """

    name = "__particle_centers_grid"

    FONT = cv2.FONT_HERSHEY_SIMPLEX

    SCALE_X: float = 0.1
    SCALE_Y: float = 0.1
    UNIT: str = "mm"
    ORIGIN_OFFSET_X: float = 0.0
    ORIGIN_OFFSET_Y: float = 0.0
    INVERT_Y: bool = True

    GRID_STEP: float = 10.0
    GRID_COLOR: tuple = (60, 60, 60)
    GRID_THICKNESS: int = 1

    AXIS_COLOR: tuple = (100, 100, 100)
    AXIS_THICKNESS: int = 1

    LABEL_COLOR: tuple = (200, 200, 200)
    LABEL_FONT_SCALE: float = 0.38
    LABEL_THICKNESS: int = 1
    LABEL_OFFSET_Y: int = -8

    TICK_COLOR: tuple = (130, 130, 130)
    TICK_FONT_SCALE: float = 0.32
    TICK_LENGTH: int = 4

    def __init__(self) -> None:
        self._cfg_loaded = False

    def apply(self, frame: object, context: FrameContext) -> object:
        if not self._cfg_loaded:
            self._load_cfg(context)
            self._cfg_loaded = True

        h, w = frame.shape[:2]
        cx_frame, cy_frame = w / 2.0, h / 2.0

        eff_step = self._effective_grid_step(w)
        self._draw_grid(frame, w, h, cx_frame, cy_frame, eff_step)
        self._draw_axes(frame, w, h, cx_frame, cy_frame)
        self._draw_ticks(frame, w, h, cx_frame, cy_frame, eff_step)
        self._draw_scale_badge(frame, eff_step)

        particles = context.shared.get("particle_centers")
        if particles:
            context.shared["particle_world"] = [
                self._to_world(p, cx_frame, cy_frame) for p in particles
            ]
            if not context.shared.get("_nearest_active"):
                self._label_particles(frame, particles, cx_frame, cy_frame)

        return frame

    def _load_cfg(self, context: FrameContext) -> None:
        cfg: dict = {}
        try:
            import json
            from pathlib import Path
            p = Path(__file__).resolve().parents[4] / "config.json"
            if p.exists():
                raw = json.loads(p.read_text("utf-8"))
                cfg = raw.get("particle_grid", {})
        except Exception:
            pass

        if not cfg:
            return

        _f = {
            "scale_x": "SCALE_X", "scale_y": "SCALE_Y",
            "origin_offset_x": "ORIGIN_OFFSET_X",
            "origin_offset_y": "ORIGIN_OFFSET_Y",
            "grid_step": "GRID_STEP",
            "label_font_scale": "LABEL_FONT_SCALE",
            "label_offset_y": "LABEL_OFFSET_Y",
            "tick_font_scale": "TICK_FONT_SCALE",
            "tick_length": "TICK_LENGTH",
            "grid_thickness": "GRID_THICKNESS",
            "axis_thickness": "AXIS_THICKNESS",
            "label_thickness": "LABEL_THICKNESS",
        }
        for k, attr in _f.items():
            if k in cfg:
                setattr(self, attr, float(cfg[k]))

        _b = {"invert_y": "INVERT_Y"}
        for k, attr in _b.items():
            if k in cfg:
                setattr(self, attr, bool(cfg[k]))

        _s = {"unit": "UNIT"}
        for k, attr in _s.items():
            if k in cfg:
                setattr(self, attr, str(cfg[k]))

        _c = {
            "grid_color": "GRID_COLOR",
            "axis_color": "AXIS_COLOR",
            "label_color": "LABEL_COLOR",
            "tick_color": "TICK_COLOR",
        }
        for k, attr in _c.items():
            if k in cfg:
                setattr(self, attr, tuple(cfg[k]))

    def _px_to_world(
        self, px_x: float, px_y: float,
        cx_frame: float, cy_frame: float,
    ) -> tuple[float, float]:
        dx = (px_x - cx_frame) * self.SCALE_X + self.ORIGIN_OFFSET_X
        dy = (px_y - cy_frame) * self.SCALE_Y + self.ORIGIN_OFFSET_Y
        if self.INVERT_Y:
            dy = -dy
        return dx, dy

    def _world_to_px(
        self, wx: float, wy: float,
        cx_frame: float, cy_frame: float,
    ) -> tuple[int, int]:
        dy = -wy if self.INVERT_Y else wy
        px_x = (wx - self.ORIGIN_OFFSET_X) / self.SCALE_X + cx_frame
        px_y = (dy - self.ORIGIN_OFFSET_Y) / self.SCALE_Y + cy_frame
        return int(round(px_x)), int(round(px_y))

    def _to_world(
        self, p: dict, cx_frame: float, cy_frame: float,
    ) -> dict:
        wx, wy = self._px_to_world(p["cx"], p["cy"], cx_frame, cy_frame)
        ww = p["w"] * self.SCALE_X
        wh = p["h"] * self.SCALE_Y
        return {
            "tid": p["tid"],
            "x": round(wx, 2), "y": round(wy, 2),
            "w": round(ww, 2), "h": round(wh, 2),
            "angle": p["angle"], "side": p["side"],
            "unit": self.UNIT,
        }

    def _effective_grid_step(self, frame_w: int) -> float:
        MIN_STEP_PX = 40
        MAX_STEP_PX = 200
        step = self.GRID_STEP
        step_px = step / self.SCALE_X

        if step_px < MIN_STEP_PX:
            candidates = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5,
                          1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]
            for c in candidates:
                if c / self.SCALE_X >= MIN_STEP_PX:
                    step = c
                    break
            else:
                step = self.SCALE_X * MIN_STEP_PX

        elif step_px > MAX_STEP_PX:
            candidates = [1000, 500, 200, 100, 50, 20, 10, 5, 2, 1,
                          0.5, 0.2, 0.1, 0.05, 0.02, 0.01]
            for c in candidates:
                if c / self.SCALE_X <= MAX_STEP_PX:
                    step = c
                    break

        return step

    def _draw_grid(
        self, frame: np.ndarray, fw: int, fh: int,
        cx: float, cy: float, grid_step: float = 0,
    ) -> None:
        gs = grid_step if grid_step > 0 else self.GRID_STEP
        step_px = gs / self.SCALE_X
        if step_px < 8:
            return

        x = cx % step_px
        while x < fw:
            ix = int(round(x))
            cv2.line(frame, (ix, 0), (ix, fh), self.GRID_COLOR,
                     int(self.GRID_THICKNESS), cv2.LINE_AA)
            x += step_px

        step_py = gs / self.SCALE_Y
        if step_py < 8:
            return
        y = cy % step_py
        while y < fh:
            iy = int(round(y))
            cv2.line(frame, (0, iy), (fw, iy), self.GRID_COLOR,
                     int(self.GRID_THICKNESS), cv2.LINE_AA)
            y += step_py

    def _draw_axes(
        self, frame: np.ndarray, fw: int, fh: int,
        cx: float, cy: float,
    ) -> None:
        icx, icy = int(round(cx)), int(round(cy))
        cv2.line(frame, (icx, 0), (icx, fh), self.AXIS_COLOR,
                 int(self.AXIS_THICKNESS), cv2.LINE_AA)
        cv2.line(frame, (0, icy), (fw, icy), self.AXIS_COLOR,
                 int(self.AXIS_THICKNESS), cv2.LINE_AA)

        cv2.putText(
            frame, f"0 {self.UNIT}", (icx + 4, icy - 4),
            self.FONT, float(self.TICK_FONT_SCALE),
            self.AXIS_COLOR, 1, cv2.LINE_AA,
        )

    def _draw_ticks(
        self, frame: np.ndarray, fw: int, fh: int,
        cx: float, cy: float, grid_step: float = 0,
    ) -> None:
        gs = grid_step if grid_step > 0 else self.GRID_STEP
        step_px_x = gs / self.SCALE_X
        step_px_y = gs / self.SCALE_Y
        tl = int(self.TICK_LENGTH)
        icy = int(round(cy))
        icx = int(round(cx))

        def _fmt(v: float) -> str:
            av = abs(v)
            if av == 0:
                return "0"
            if av >= 10:
                return f"{v:.0f}"
            if av >= 1:
                return f"{v:.1f}"
            if av >= 0.1:
                return f"{v:.2f}"
            return f"{v:.3f}"

        if step_px_x >= 8:
            x = cx + step_px_x
            while x < fw:
                ix = int(round(x))
                cv2.line(frame, (ix, icy - tl), (ix, icy + tl),
                         self.TICK_COLOR, 1, cv2.LINE_AA)
                val = (x - cx) * self.SCALE_X + self.ORIGIN_OFFSET_X
                cv2.putText(frame, _fmt(val), (ix + 2, icy - tl - 2),
                            self.FONT, float(self.TICK_FONT_SCALE),
                            self.TICK_COLOR, 1, cv2.LINE_AA)
                x += step_px_x
            x = cx - step_px_x
            while x > 0:
                ix = int(round(x))
                cv2.line(frame, (ix, icy - tl), (ix, icy + tl),
                         self.TICK_COLOR, 1, cv2.LINE_AA)
                val = (x - cx) * self.SCALE_X + self.ORIGIN_OFFSET_X
                cv2.putText(frame, _fmt(val), (ix + 2, icy - tl - 2),
                            self.FONT, float(self.TICK_FONT_SCALE),
                            self.TICK_COLOR, 1, cv2.LINE_AA)
                x -= step_px_x

        if step_px_y >= 8:
            y = cy + step_px_y
            while y < fh:
                iy = int(round(y))
                cv2.line(frame, (icx - tl, iy), (icx + tl, iy),
                         self.TICK_COLOR, 1, cv2.LINE_AA)
                val_raw = (y - cy) * self.SCALE_Y + self.ORIGIN_OFFSET_Y
                val = -val_raw if self.INVERT_Y else val_raw
                cv2.putText(frame, _fmt(val), (icx + tl + 2, iy + 4),
                            self.FONT, float(self.TICK_FONT_SCALE),
                            self.TICK_COLOR, 1, cv2.LINE_AA)
                y += step_px_y
            y = cy - step_px_y
            while y > 0:
                iy = int(round(y))
                cv2.line(frame, (icx - tl, iy), (icx + tl, iy),
                         self.TICK_COLOR, 1, cv2.LINE_AA)
                val_raw = (y - cy) * self.SCALE_Y + self.ORIGIN_OFFSET_Y
                val = -val_raw if self.INVERT_Y else val_raw
                cv2.putText(frame, _fmt(val), (icx + tl + 2, iy + 4),
                            self.FONT, float(self.TICK_FONT_SCALE),
                            self.TICK_COLOR, 1, cv2.LINE_AA)
                y -= step_px_y

    def _draw_scale_badge(
        self, frame: np.ndarray, eff_step: float,
    ) -> None:
        fh, fw = frame.shape[:2]
        bar_world = eff_step
        bar_px = int(round(bar_world / self.SCALE_X))
        bar_px = max(20, min(bar_px, fw // 3))
        bar_world = bar_px * self.SCALE_X

        if bar_world >= 10:
            label = f"{bar_world:.0f} {self.UNIT}"
        elif bar_world >= 1:
            label = f"{bar_world:.1f} {self.UNIT}"
        elif bar_world >= 0.1:
            label = f"{bar_world:.2f} {self.UNIT}"
        else:
            label = f"{bar_world:.3f} {self.UNIT}"

        margin = 12
        bar_y = fh - margin - 14
        bar_x0 = fw - margin - bar_px
        bar_x1 = fw - margin

        cv2.rectangle(frame, (bar_x0 - 4, bar_y - 18),
                       (bar_x1 + 4, bar_y + 8), (0, 0, 0), -1)
        cv2.rectangle(frame, (bar_x0 - 4, bar_y - 18),
                       (bar_x1 + 4, bar_y + 8), (80, 80, 80), 1)

        cv2.line(frame, (bar_x0, bar_y), (bar_x1, bar_y),
                 (220, 220, 220), 2, cv2.LINE_AA)
        cv2.line(frame, (bar_x0, bar_y - 4), (bar_x0, bar_y + 4),
                 (220, 220, 220), 1, cv2.LINE_AA)
        cv2.line(frame, (bar_x1, bar_y - 4), (bar_x1, bar_y + 4),
                 (220, 220, 220), 1, cv2.LINE_AA)

        (tw, th), _ = cv2.getTextSize(label, self.FONT, 0.35, 1)
        tx = (bar_x0 + bar_x1) // 2 - tw // 2
        cv2.putText(frame, label, (tx, bar_y - 5),
                    self.FONT, 0.35, (220, 220, 220), 1, cv2.LINE_AA)

        scale_txt = f"1px = {self.SCALE_X:.4f} {self.UNIT}"
        (sw, _), _ = cv2.getTextSize(scale_txt, self.FONT, 0.28, 1)
        cv2.putText(frame, scale_txt, (bar_x1 - sw, bar_y - 20),
                    self.FONT, 0.28, (140, 140, 140), 1, cv2.LINE_AA)

    @staticmethod
    def _fmt_coord(v: float) -> str:
        av = abs(v)
        if av == 0:
            return "0"
        if av >= 10:
            return f"{v:+.0f}"
        if av >= 1:
            return f"{v:+.1f}"
        if av >= 0.1:
            return f"{v:+.2f}"
        return f"{v:+.3f}"

    def _label_particles(
        self, frame: np.ndarray, particles: list[dict],
        cx_frame: float, cy_frame: float,
    ) -> None:
        for p in particles:
            wx, wy = self._px_to_world(p["cx"], p["cy"], cx_frame, cy_frame)
            label = f"({self._fmt_coord(wx)}, {self._fmt_coord(wy)})"
            px, py = int(p["cx"]), int(p["cy"]) + int(self.LABEL_OFFSET_Y)
            (tw, th), _ = cv2.getTextSize(
                label, self.FONT, float(self.LABEL_FONT_SCALE),
                int(self.LABEL_THICKNESS),
            )
            tx = px - tw // 2
            cv2.putText(
                frame, label, (tx, py),
                self.FONT, float(self.LABEL_FONT_SCALE),
                self.LABEL_COLOR, int(self.LABEL_THICKNESS), cv2.LINE_AA,
            )
