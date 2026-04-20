from __future__ import annotations

import math
from collections import deque

import cv2
import numpy as np

from ..base import FrameContext, NeuralMod


class Mod(NeuralMod):

    name = "__particle_centers_pin"

    FONT = cv2.FONT_HERSHEY_SIMPLEX

    COLOR_PIN: tuple = (0, 255, 255)
    COLOR_TRAIL: tuple = (0, 180, 180)
    PIN_THICKNESS: int = 3
    LABEL_FONT_SCALE: float = 0.44
    TRAIL_LENGTH: int = 30

    REACQUIRE_DIST: float = 80.0
    REACQUIRE_ANGLE_TOL: float = 30.0
    REACQUIRE_RATIO_TOL: float = 0.4
    HOLD_FRAMES: int = 18

    def __init__(self) -> None:
        self._cfg_loaded = False
        self._pinned_tid: int | None = None
        self._last_cx: float = 0.0
        self._last_cy: float = 0.0
        self._last_angle: float = 0.0
        self._last_side: str = "top"
        self._last_wh_ratio: float = 1.0
        self._lost_frames: int = 0
        self._trail: deque[tuple[int, int]] = deque(maxlen=30)

    def apply(self, frame: object, context: FrameContext) -> object:
        if not self._cfg_loaded:
            self._load_cfg()
            self._cfg_loaded = True
            self._trail = deque(maxlen=self.TRAIL_LENGTH)

        pin_req = context.shared.get("_pin_request")
        if pin_req is not None:
            if pin_req == 0:
                if self._pinned_tid is not None:
                    self._clear_pin()
            elif pin_req != self._pinned_tid:
                self._pinned_tid = pin_req
                self._lost_frames = 0
                self._trail.clear()

        particles = context.shared.get("particle_centers")
        if not particles or self._pinned_tid is None:
            context.shared["particle_pin"] = {
                "active": False, "tid": None,
                "cx": 0, "cy": 0, "trail": [], "lost_frames": 0,
            }
            return frame

        target = self._find_by_tid(particles, self._pinned_tid)

        if target is None:
            target = self._reacquire(particles)

        just_lost = False
        if target is not None:
            self._lost_frames = 0
            self._pinned_tid = target["tid"]
            self._last_cx = target["cx"]
            self._last_cy = target["cy"]
            self._last_angle = target["angle"]
            self._last_side = target.get("side", "top")
            w, h = max(target["w"], 1), max(target["h"], 1)
            self._last_wh_ratio = w / h
            self._trail.append((int(target["cx"]), int(target["cy"])))
            fh, fw = frame.shape[:2]
            self._draw(frame, target, fw // 2, fh // 2)
        else:
            self._lost_frames += 1
            if self._lost_frames > self.HOLD_FRAMES:
                self._draw_lost_banner(frame)
                self._clear_pin()
                just_lost = True
            else:
                self._draw_searching(frame)

        context.shared["particle_pin"] = {
            "active": self._pinned_tid is not None,
            "tid": self._pinned_tid,
            "cx": int(self._last_cx),
            "cy": int(self._last_cy),
            "trail": list(self._trail),
            "lost_frames": self._lost_frames,
            "just_lost": just_lost,
        }

        return frame

    def _find_by_tid(self, particles: list[dict], tid: int) -> dict | None:
        for p in particles:
            if p.get("tid") == tid:
                return p
        return None

    def _reacquire(self, particles: list[dict]) -> dict | None:
        best, best_score = None, float("inf")
        for p in particles:
            dist = math.hypot(p["cx"] - self._last_cx, p["cy"] - self._last_cy)
            if dist > self.REACQUIRE_DIST:
                continue

            angle_diff = abs(p["angle"] - self._last_angle)
            if angle_diff > 180:
                angle_diff = 360 - angle_diff
            if angle_diff > self.REACQUIRE_ANGLE_TOL:
                continue

            w, h = max(p["w"], 1), max(p["h"], 1)
            ratio = w / h
            if abs(ratio - self._last_wh_ratio) > self.REACQUIRE_RATIO_TOL:
                continue

            side_penalty = 0.0 if p.get("side") == self._last_side else 30.0
            score = dist + angle_diff * 0.5 + side_penalty
            if score < best_score:
                best_score = score
                best = p

        return best

    def _clear_pin(self) -> None:
        self._pinned_tid = None
        self._lost_frames = 0
        self._trail.clear()

    def _draw(self, frame: np.ndarray, p: dict, cam_cx: int = 0, cam_cy: int = 0) -> None:
        cx, cy = int(p["cx"]), int(p["cy"])
        w, h, angle = p["w"], p["h"], p["angle"]

        cv2.line(frame, (cam_cx, cam_cy), (cx, cy), self.COLOR_PIN, 1, cv2.LINE_AA)

        ew, eh = int(w * 1.4), int(h * 1.4)
        outer_box = np.intp(cv2.boxPoints(((p["cx"], p["cy"]), (ew, eh), angle)))
        cv2.drawContours(frame, [outer_box], 0, self.COLOR_PIN, 1, cv2.LINE_AA)

        box = np.intp(cv2.boxPoints(((p["cx"], p["cy"]), (w, h), angle)))
        cv2.drawContours(frame, [box], 0, self.COLOR_PIN, self.PIN_THICKNESS, cv2.LINE_AA)

        rad = math.radians(angle)
        cs, sn = math.cos(rad), math.sin(rad)
        hw, hh = w * 0.5, h * 0.5
        dx1, dy1 = cs * hw, sn * hw
        dx2, dy2 = -sn * hh, cs * hh
        cv2.line(frame,
                 (int(p["cx"] - dx1), int(p["cy"] - dy1)),
                 (int(p["cx"] + dx1), int(p["cy"] + dy1)),
                 self.COLOR_PIN, 1, cv2.LINE_AA)
        cv2.line(frame,
                 (int(p["cx"] - dx2), int(p["cy"] - dy2)),
                 (int(p["cx"] + dx2), int(p["cy"] + dy2)),
                 self.COLOR_PIN, 1, cv2.LINE_AA)

        for i, corner in enumerate(box):
            arm = 6
            nxt = box[(i + 1) % 4]
            d = nxt - corner
            ln = max(np.linalg.norm(d), 1)
            uv = (d / ln * arm).astype(int)
            cv2.line(frame, tuple(corner), tuple(corner + uv), self.COLOR_PIN, 2, cv2.LINE_AA)
            prv = box[(i - 1) % 4]
            d2 = prv - corner
            ln2 = max(np.linalg.norm(d2), 1)
            uv2 = (d2 / ln2 * arm).astype(int)
            cv2.line(frame, tuple(corner), tuple(corner + uv2), self.COLOR_PIN, 2, cv2.LINE_AA)

        if len(self._trail) >= 2:
            pts = np.array(list(self._trail), dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(frame, [pts], False, self.COLOR_TRAIL, 2, cv2.LINE_AA)

        label = f"PIN #{self._pinned_tid}"
        (tw, th), _ = cv2.getTextSize(label, self.FONT, 0.5, 1)
        tx, ty = cx - tw // 2, cy - int(h * 0.5) - 14
        cv2.rectangle(frame, (tx - 4, ty - th - 4), (tx + tw + 4, ty + 4),
                       (0, 40, 40), -1)
        cv2.rectangle(frame, (tx - 4, ty - th - 4), (tx + tw + 4, ty + 4),
                       self.COLOR_PIN, 1, cv2.LINE_AA)
        cv2.putText(frame, label, (tx, ty), self.FONT, 0.5,
                    self.COLOR_PIN, 1, cv2.LINE_AA)

    def _draw_searching(self, frame: np.ndarray) -> None:
        cx, cy = int(self._last_cx), int(self._last_cy)
        r = 20 + self._lost_frames * 2
        cv2.circle(frame, (cx, cy), r, (0, 180, 255), 1, cv2.LINE_AA)
        label = f"SEARCH #{self._pinned_tid} ({self._lost_frames})"
        (tw, th), _ = cv2.getTextSize(label, self.FONT, 0.38, 1)
        tx, ty = cx - tw // 2, cy - r - 8
        cv2.rectangle(frame, (tx - 3, ty - th - 3), (tx + tw + 3, ty + 3), (0, 0, 0), -1)
        cv2.putText(frame, label, (tx, ty), self.FONT, 0.38,
                    (0, 180, 255), 1, cv2.LINE_AA)
        if len(self._trail) >= 2:
            pts = np.array(list(self._trail), dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(frame, [pts], False, (0, 100, 140), 1, cv2.LINE_AA)

    def _draw_lost_banner(self, frame: np.ndarray) -> None:
        cx, cy = int(self._last_cx), int(self._last_cy)
        label = "PIN LOST"
        (tw, th), _ = cv2.getTextSize(label, self.FONT, 0.6, 2)
        tx, ty = cx - tw // 2, cy + th // 2
        cv2.rectangle(frame, (tx - 6, ty - th - 6), (tx + tw + 6, ty + 6), (0, 0, 80), -1)
        cv2.putText(frame, label, (tx, ty), self.FONT, 0.6,
                    (0, 0, 255), 2, cv2.LINE_AA)

    def _load_cfg(self) -> None:
        try:
            import json as _json
            from pathlib import Path
            p = Path(__file__).resolve().parents[4] / "config.json"
            if not p.exists():
                return
            raw = _json.loads(p.read_text("utf-8"))
        except Exception:
            return

        cfg = raw.get("particle_pin", {})
        for k, attr in {
            "color_pin": "COLOR_PIN",
            "color_trail": "COLOR_TRAIL",
        }.items():
            if k in cfg:
                setattr(self, attr, tuple(cfg[k]))
        if "pin_thickness" in cfg:
            self.PIN_THICKNESS = int(cfg["pin_thickness"])
        if "label_font_scale" in cfg:
            self.LABEL_FONT_SCALE = float(cfg["label_font_scale"])
        if "trail_length" in cfg:
            self.TRAIL_LENGTH = int(cfg["trail_length"])
