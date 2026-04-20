from __future__ import annotations

import json
from pathlib import Path

from .config_models import AppConfig


class ConfigService:
    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path

    def load(self) -> AppConfig:
        if not self._config_path.exists():
            default_config = AppConfig()
            self.save(default_config)
            return default_config

        with self._config_path.open("r", encoding="utf-8") as fp:
            raw = json.load(fp)

        config = AppConfig.from_dict(raw)
        config.validate()
        return config

    def save(self, config: AppConfig) -> None:
        config.validate()

        existing: dict = {}
        if self._config_path.exists():
            try:
                with self._config_path.open("r", encoding="utf-8") as fp:
                    existing = json.load(fp)
            except (json.JSONDecodeError, OSError):
                existing = {}

        existing["source"] = {
            "type": config.source.type,
            "camera": {"index": config.source.camera.index},
            "file": {"path": config.source.file.path, "loop": config.source.file.loop},
        }
        existing["window"] = {"title": config.window.title}
        existing["neural"] = {"mods": config.neural.mods}

        with self._config_path.open("w", encoding="utf-8") as fp:
            json.dump(existing, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
