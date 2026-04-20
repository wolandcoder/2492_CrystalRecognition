from __future__ import annotations

from dataclasses import dataclass

import cv2

from .config_models import AppConfig


@dataclass
class FrameResult:
    ok: bool
    frame: object | None


class VideoSource:
    def read(self) -> FrameResult:
        raise NotImplementedError

    def release(self) -> None:
        raise NotImplementedError


class CameraVideoSource(VideoSource):
    def __init__(self, camera_index: int) -> None:
        self._cap = cv2.VideoCapture(camera_index)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open camera index: {camera_index}")

    def read(self) -> FrameResult:
        ok, frame = self._cap.read()
        return FrameResult(ok=ok, frame=frame if ok else None)

    def release(self) -> None:
        self._cap.release()


class FileVideoSource(VideoSource):
    def __init__(self, path: str, loop: bool) -> None:
        self._path = path
        self._loop = loop
        self._cap = cv2.VideoCapture(path)
        if not self._cap.isOpened():
            raise RuntimeError(f"Cannot open video file: {path}")

    def read(self) -> FrameResult:
        ok, frame = self._cap.read()
        if ok:
            return FrameResult(ok=True, frame=frame)

        if not self._loop:
            return FrameResult(ok=False, frame=None)

        self._cap.release()
        self._cap = cv2.VideoCapture(self._path)
        if not self._cap.isOpened():
            return FrameResult(ok=False, frame=None)

        ok, frame = self._cap.read()
        return FrameResult(ok=ok, frame=frame if ok else None)

    def release(self) -> None:
        self._cap.release()


class VideoSourceFactory:
    @staticmethod
    def from_config(config: AppConfig) -> VideoSource:
        source_type = config.source.type

        if source_type == "camera":
            return CameraVideoSource(camera_index=config.source.camera.index)

        if source_type == "file":
            return FileVideoSource(
                path=config.source.file.path,
                loop=config.source.file.loop,
            )

        raise ValueError(f"Unsupported source.type: {source_type}")
