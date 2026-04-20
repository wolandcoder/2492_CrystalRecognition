from __future__ import annotations

from pathlib import Path

from __core.__camera import ConfigService
from __core.__camera.viewer import CameraViewer


def main() -> int:
    config_path = Path(__file__).resolve().parent / "config.json"
    service = ConfigService(config_path=config_path)
    config = service.load()
    viewer = CameraViewer(config=config)
    viewer.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
