from __future__ import annotations

import time

import cv2

from .__neural.base import FrameContext
from .__neural.manager import NeuralManager
from .config_models import AppConfig
from .video_source import VideoSourceFactory


class CameraViewer:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def run(self) -> None:
        source = VideoSourceFactory.from_config(self._config)
        window_title = self._config.window.title
        neural = NeuralManager(mod_names=self._config.neural.mods)
        frame_index = 0
        last_ts = time.perf_counter()
        fps = 0.0

        source_type = self._config.source.type
        source_label = (
            str(self._config.source.camera.index)
            if source_type == "camera"
            else self._config.source.file.path
        )

        try:
            while True:
                result = source.read()
                if not result.ok or result.frame is None:
                    break

                now = time.perf_counter()
                dt = now - last_ts
                if dt > 0:
                    fps = 1.0 / dt
                last_ts = now
                frame_index += 1

                frame_height, frame_width = result.frame.shape[:2]
                frame_ctx = FrameContext(
                    fps=fps,
                    source_type=source_type,
                    source_label=source_label,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    frame_index=frame_index,
                )
                frame_to_show = neural.apply(result.frame, frame_ctx)

                cv2.imshow(window_title, frame_to_show)
                if cv2.waitKey(1) & 0xFF == 27:
                    break
        finally:
            source.release()
            cv2.destroyAllWindows()
