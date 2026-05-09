from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FrameContext:
    fps: float
    source_type: str
    source_label: str
    frame_width: int
    frame_height: int
    frame_index: int
    overlay_line: int = 0

    timestamp_ms: float = 0.0
    frame_age_ms: float = 0.0

    shared: dict[str, Any] = field(default_factory=dict)

    def reserve_text_lines(self, lines: int = 1) -> int:
        start_line = self.overlay_line
        self.overlay_line += max(1, lines)
        return start_line


class NeuralMod:
    name: str = "__base"

    def apply(self, frame: Any, context: FrameContext) -> Any:
        return frame

    def shutdown(self) -> None:
        return None
