from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CameraSourceConfig:
    index: int = 0


@dataclass
class FileSourceConfig:
    path: str = ""
    loop: bool = False


@dataclass
class SourceConfig:
    type: str = "camera"
    camera: CameraSourceConfig = field(default_factory=CameraSourceConfig)
    file: FileSourceConfig = field(default_factory=FileSourceConfig)


@dataclass
class WindowConfig:
    title: str = "Video Viewer"


@dataclass
class NeuralConfig:
    mods: list[str] = field(default_factory=list)


@dataclass
class AppConfig:
    source: SourceConfig = field(default_factory=SourceConfig)
    window: WindowConfig = field(default_factory=WindowConfig)
    neural: NeuralConfig = field(default_factory=NeuralConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        source_data = data.get("source", {})
        camera_data = source_data.get("camera", {})
        file_data = source_data.get("file", {})
        window_data = data.get("window", {})
        neural_data = data.get("neural", {})

        return cls(
            source=SourceConfig(
                type=source_data.get("type", "camera"),
                camera=CameraSourceConfig(
                    index=int(camera_data.get("index", 0)),
                ),
                file=FileSourceConfig(
                    path=str(file_data.get("path", "")),
                    loop=bool(file_data.get("loop", False)),
                ),
            ),
            window=WindowConfig(
                title=str(window_data.get("title", "Video Viewer")),
            ),
            neural=NeuralConfig(
                mods=[str(item) for item in neural_data.get("mods", [])],
            ),
        )

    def validate(self) -> None:
        source_type = self.source.type
        if source_type not in {"camera", "file"}:
            raise ValueError("source.type must be 'camera' or 'file'")

        if source_type == "camera" and self.source.camera.index < 0:
            raise ValueError("camera.index must be >= 0")

        if source_type == "file":
            file_path = self.source.file.path.strip()
            if not file_path:
                raise ValueError("file.path must not be empty for source.type='file'")
            if not Path(file_path).exists():
                raise ValueError(f"file.path does not exist: {file_path}")

        for mod_name in self.neural.mods:
            if not mod_name.startswith("__"):
                raise ValueError(f"neural.mods item must start with '__': {mod_name}")
