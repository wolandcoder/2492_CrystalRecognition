from __future__ import annotations

import cv2
import numpy as np

from ..base import FrameContext, NeuralMod


class Mod(NeuralMod):
    """Overlay showing vacuum tube target position relative to nearest/pinned crystal.

    Reads config from ``movement`` section and grid scale from ``particle_grid``.
    Depends on ``__particle_centers``, ``__particle_centers_nearest``.
    """

    name = "__particle_centers_movement"

    FONT = cv2.FONT_HERSHEY_SIMPLEX

    SCALE_X: float = 0.1
    SCALE_Y: float = 0.1
    INVERT_Y: bool = True
    ORIGIN_OFFSET_X: float = 0.0
    ORIGIN_OFFSET_Y: float = 0.0

    VACUUM_OFFSET_X: float = -44.0
    VACUUM_OFFSET_Y: float = -6.0

    COLOR_TUBE: tuple = (0, 200, 255)
    TUBE_SIZE: int = 14

    def __init__(self) -> None:
        self._cfg_loaded = False

    def apply(self, frame: object, context: FrameContext) -> object:
        if not self._cfg_loaded:
            self._load_cfg()
            self._cfg_loaded = True

        fh, fw = frame.shape[:2]
        cx_cam, cy_cam = fw / 2.0, fh / 2.0

        nearest = context.shared.get("particle_nearest")
        pin_info = context.shared.get("particle_pin")

        target = None
        if pin_info and pin_info.get("active") and pin_info.get("tid") is not None:
            particles = context.shared.get("particle_centers", [])
            for p in particles:
                if p.get("tid") == pin_info["tid"]:
                    target = p
                    break

        if target is None and nearest:
            particles = context.shared.get("particle_centers", [])
            tid = nearest.get("tid")
            if tid is not None:
                for p in particles:
                    if p.get("tid") == tid:
                        target = p
                        break

        if target is None:
            context.shared["particle_movement"] = None
            return frame

        crystal_px_x = target["cx"]
        crystal_px_y = target["cy"]

        world_x = (crystal_px_x - cx_cam) * self.SCALE_X + self.ORIGIN_OFFSET_X
        world_y = (crystal_px_y - cy_cam) * self.SCALE_Y + self.ORIGIN_OFFSET_Y
        if self.INVERT_Y:
            world_y = -world_y

        tube_world_x = world_x + self.VACUUM_OFFSET_X
        tube_world_y = world_y + self.VACUUM_OFFSET_Y

        tube_dy = -self.VACUUM_OFFSET_Y if self.INVERT_Y else self.VACUUM_OFFSET_Y
        tube_px_x = crystal_px_x + self.VACUUM_OFFSET_X / self.SCALE_X
        tube_px_y = crystal_px_y + tube_dy / self.SCALE_Y

        ipx = int(round(tube_px_x))
        ipy = int(round(tube_px_y))
        s = self.TUBE_SIZE

        cv2.line(frame, (ipx - s, ipy), (ipx + s, ipy),
                 self.COLOR_TUBE, 1, cv2.LINE_AA)
        cv2.line(frame, (ipx, ipy - s), (ipx, ipy + s),
                 self.COLOR_TUBE, 1, cv2.LINE_AA)
        cv2.circle(frame, (ipx, ipy), s, self.COLOR_TUBE, 1, cv2.LINE_AA)

        label = f"TUBE ({tube_world_x:+.1f}, {tube_world_y:+.1f})"
        (tw, th), _ = cv2.getTextSize(label, self.FONT, 0.35, 1)
        cv2.putText(frame, label, (ipx - tw // 2, ipy - s - 4),
                    self.FONT, 0.35, self.COLOR_TUBE, 1, cv2.LINE_AA)

        cv2.line(frame, (int(crystal_px_x), int(crystal_px_y)), (ipx, ipy),
                 self.COLOR_TUBE, 1, cv2.LINE_AA)

        context.shared["particle_movement"] = {
            "crystal_world_x": round(world_x, 3),
            "crystal_world_y": round(world_y, 3),
            "tube_target_x": round(tube_world_x, 3),
            "tube_target_y": round(tube_world_y, 3),
            "tid": target.get("tid"),
        }

        return frame

    def _load_cfg(self) -> None:
        try:
            import json
            from pathlib import Path
            p = Path(__file__).resolve().parents[4] / "config.json"
            if not p.exists():
                return
            raw = json.loads(p.read_text("utf-8"))

            grid = raw.get("particle_grid", {})
            for k, attr in {"scale_x": "SCALE_X", "scale_y": "SCALE_Y",
                            "origin_offset_x": "ORIGIN_OFFSET_X",
                            "origin_offset_y": "ORIGIN_OFFSET_Y"}.items():
                if k in grid:
                    setattr(self, attr, float(grid[k]))
            if "invert_y" in grid:
                self.INVERT_Y = bool(grid["invert_y"])

            mv = raw.get("movement", {})
            if "vacuum_offset_x" in mv:
                self.VACUUM_OFFSET_X = float(mv["vacuum_offset_x"])
            if "vacuum_offset_y" in mv:
                self.VACUUM_OFFSET_Y = float(mv["vacuum_offset_y"])
        except Exception:
            pass
