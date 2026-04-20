from __future__ import annotations

import cv2

from ..base import FrameContext, NeuralMod


class Mod(NeuralMod):
    name = "__hud_info"

    def apply(self, frame: object, context: FrameContext) -> object:
        block_line = context.reserve_text_lines(lines=2)
        y1 = 28 + block_line * 34
        y2 = y1 + 30

        line1 = f"FPS: {context.fps:.1f}"
        line2 = (
            f"Source: {context.source_type} ({context.source_label}) | "
            f"{context.frame_width}x{context.frame_height}"
        )

        cv2.putText(
            frame,
            line1,
            (12, y1),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            (40, 220, 40),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            line2,
            (12, y2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (40, 220, 40),
            1,
            cv2.LINE_AA,
        )
        return frame
