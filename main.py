from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from __core.__camera import ConfigService
from __core.__camera.__neural.manager import NeuralManager


def _use_colors() -> bool:
    return os.getenv("NO_COLOR") is None and os.getenv("TERM") != "dumb"


def _color(text: str, code: str) -> str:
    if not _use_colors():
        return text
    return f"\033[{code}m{text}\033[0m"


def _accent(text: str) -> str:
    return _color(text, "96")


def _ok(text: str) -> str:
    return _color(text, "92")


def _warn(text: str) -> str:
    return _color(text, "93")


def _str_to_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "да", "д"}:
        return True
    if normalized in {"0", "false", "no", "n", "нет", "н"}:
        return False
    raise ValueError("Допустимые значения: true/false, да/нет, 1/0")


def _config_to_dict(config: object) -> dict:
    return {
        "source": {
            "type": config.source.type,
            "camera": {"index": config.source.camera.index},
            "file": {
                "path": config.source.file.path,
                "loop": config.source.file.loop,
            },
        },
        "window": {"title": config.window.title},
        "neural": {"mods": config.neural.mods},
    }


def _print_current_config(config: object) -> None:
    data = _config_to_dict(config)
    print(_accent("Текущая конфигурация:"))
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _parse_source_choice(raw_value: str) -> str | None:
    value = raw_value.strip().lower()
    if value in {"1", "camera", "cam", "c", "кам", "камера", "к"}:
        return "camera"
    if value in {"2", "file", "f", "видео", "видеофайл", "файл", "ф"}:
        return "file"
    return None


def _parse_camera_index(raw_value: str, default_index: int) -> int:
    value = raw_value.strip()
    if not value:
        return default_index
                                                     
    lowered = value.lower()
    for prefix in ("camera", "cam", "c", "камера", "кам", "к"):
        if lowered.startswith(prefix):
            remainder = lowered[len(prefix) :].strip()
            if remainder:
                return int(remainder)
    return int(value)


def _probe_cameras(max_index: int = 10) -> list[dict]:
                                                                          
                                    
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    try:
        import cv2
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "OpenCV не установлен. Установите зависимости: pip install -r requirements.txt"
        ) from exc

    if hasattr(cv2, "setLogLevel"):
                                                                                    
        level_error = getattr(cv2, "LOG_LEVEL_ERROR", 2)
        cv2.setLogLevel(level_error)

    cameras: list[dict] = []
    consecutive_misses = 0
    miss_limit = 3

    for index in range(max_index):
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            cap.release()
            consecutive_misses += 1
            if cameras and consecutive_misses >= miss_limit:
                break
            continue

        ok, _ = cap.read()
        if not ok:
            cap.release()
            consecutive_misses += 1
            if cameras and consecutive_misses >= miss_limit:
                break
            continue

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(cap.get(cv2.CAP_PROP_FPS))

        cameras.append(
            {
                "index": index,
                "width": width,
                "height": height,
                "fps": fps if fps > 0 else 0.0,
            }
        )
        consecutive_misses = 0
        cap.release()

    return cameras


def _print_cameras(cameras: list[dict]) -> None:
    if not cameras:
        print(_warn("Доступные камеры не найдены."))
        return

    print(_accent("Найдены доступные камеры:"))
    for cam in cameras:
        fps_label = f"{cam['fps']:.1f}" if cam["fps"] > 0 else "неизвестно"
        print(
            f"  - { _accent('index') }={cam['index']}, "
            f"разрешение={cam['width']}x{cam['height']}, fps={fps_label}"
        )


def _interactive_setup(config: object) -> object:
    print(_accent("Интерактивная настройка источника видео"))
    _print_current_config(config)
    print(_warn("Настройки уже существуют и будут перезаписаны после подтверждения."))
    print(f"1) Камера ({_accent('можно: 1 / камера / cam / c')})")
    print(f"2) Видеофайл ({_accent('можно: 2 / файл / file / f')})")
    source_choice_raw = input("Выберите источник [1/2, по умолчанию 1]: ").strip() or "1"
    source_choice = _parse_source_choice(source_choice_raw)

    if source_choice == "camera":
        cameras = _probe_cameras()
        _print_cameras(cameras)
        if cameras:
            default_index = cameras[0]["index"]
            index_raw = input(
                f"Индекс камеры (по умолчанию {default_index}, можно c0/cam0): "
            )
        else:
            default_index = 0
            index_raw = input("Индекс камеры (по умолчанию 0, можно c0/cam0): ")
        config.source.type = "camera"
        config.source.camera.index = _parse_camera_index(index_raw, default_index=default_index)
        _interactive_mods_setup(config)
        return config

    if source_choice == "file":
        path_raw = input("Путь к видеофайлу: ").strip()
        if not path_raw:
            raise ValueError("Путь к видеофайлу не должен быть пустым")
        loop_raw = input("Зациклить видео? [да/нет, по умолчанию нет]: ").strip() or "нет"
        config.source.type = "file"
        config.source.file.path = str(Path(path_raw).resolve())
        config.source.file.loop = _str_to_bool(loop_raw)
        _interactive_mods_setup(config)
        return config

    raise ValueError("Неверный выбор источника. Используйте 1/2 или префиксы camera/file.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI для настройки и запуска источника видео (камера/файл)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    source_parser = subparsers.add_parser("source", help="Настройка источника видео")
    source_subparsers = source_parser.add_subparsers(dest="source_command", required=True)

    use_camera_parser = source_subparsers.add_parser(
        "camera",
        help="Использовать камеру",
    )
    use_camera_parser.add_argument(
        "--index",
        type=int,
        default=0,
        help="Индекс камеры (например 0)",
    )
    use_camera_parser.add_argument(
        "--list",
        action="store_true",
        help="Показать список доступных камер перед сохранением",
    )

    use_file_parser = source_subparsers.add_parser(
        "file",
        help="Использовать видеофайл",
    )
    use_file_parser.add_argument(
        "--path",
        required=True,
        help="Путь к видеофайлу (абсолютный или относительный)",
    )
    use_file_parser.add_argument(
        "--loop",
        choices=["true", "false", "да", "нет"],
        default="false",
        help="Зациклить воспроизведение (true/false, да/нет)",
    )

    subparsers.add_parser("config", help="Показать текущий config.json")
    subparsers.add_parser("init", help="Интерактивная настройка источника")
    subparsers.add_parser("view", help="Запустить просмотр текущего источника")
    subparsers.add_parser("list-cameras", help="Показать доступные камеры и их параметры")

    mods_parser = subparsers.add_parser("mods", help="Управление нейро-модами")
    mods_subparsers = mods_parser.add_subparsers(dest="mods_command", required=True)
    mods_subparsers.add_parser("show", help="Показать включенные моды")
    mods_subparsers.add_parser("available", help="Показать доступные моды")

    mods_enable_parser = mods_subparsers.add_parser("enable", help="Включить мод")
    mods_enable_parser.add_argument("--name", required=True, help="Имя мода, например __hud_info")

    mods_disable_parser = mods_subparsers.add_parser("disable", help="Выключить мод")
    mods_disable_parser.add_argument("--name", required=True, help="Имя мода, например __hud_info")

    return parser


def _interactive_mods_setup(config: object) -> None:
    available = NeuralManager.list_available()
    if not available:
        print(_warn("Доступных нейро-модов пока нет."))
        config.neural.mods = []
        return

    print(_accent("Нейро-моды (оверлеи):"))
    for item in available:
        print(f"  - {item}")

    current = ", ".join(config.neural.mods) if config.neural.mods else "нет"
    print(f"Сейчас включено: {current}")
    mode_raw = input("Включить моды? [да/нет, по умолчанию да]: ").strip() or "да"
    if not _str_to_bool(mode_raw):
        config.neural.mods = []
        return

    mods_raw = input("Введите имена модов через запятую (по умолчанию __hud_info): ").strip()
    if not mods_raw:
        selected = ["__hud_info"]
    else:
        selected = [item.strip() for item in mods_raw.split(",") if item.strip()]

    unknown = [item for item in selected if item not in available]
    if unknown:
        raise ValueError(f"Неизвестные моды: {', '.join(unknown)}")

    config.neural.mods = selected


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config_path = Path(__file__).resolve().parent / "config.json"
    service = ConfigService(config_path=config_path)
    config = service.load()

    if args.command == "config":
        print(json.dumps(_config_to_dict(config), ensure_ascii=False, indent=2))
        return 0

    if args.command == "init":
        config = _interactive_setup(config)
        service.save(config)
        print(_ok("Настройки сохранены в config.json"))
        return 0

    if args.command == "list-cameras":
        cameras = _probe_cameras()
        _print_cameras(cameras)
        return 0

    if args.command == "mods" and args.mods_command == "show":
        if not config.neural.mods:
            print("Включенные моды: нет")
        else:
            print("Включенные моды:")
            for mod in config.neural.mods:
                print(f"  - {mod}")
        return 0

    if args.command == "mods" and args.mods_command == "available":
        print("Доступные моды:")
        for mod in NeuralManager.list_available():
            print(f"  - {mod}")
        return 0

    if args.command == "mods" and args.mods_command == "enable":
        mod_name = args.name.strip()
        if mod_name not in NeuralManager.list_available():
            raise ValueError(f"Мод недоступен: {mod_name}")
        if mod_name not in config.neural.mods:
            config.neural.mods.append(mod_name)
            service.save(config)
        print(_ok(f"Мод включен: {mod_name}"))
        return 0

    if args.command == "mods" and args.mods_command == "disable":
        mod_name = args.name.strip()
        config.neural.mods = [item for item in config.neural.mods if item != mod_name]
        service.save(config)
        print(_ok(f"Мод выключен: {mod_name}"))
        return 0

    if args.command == "source" and args.source_command == "camera":
        if args.list:
            cameras = _probe_cameras()
            _print_cameras(cameras)
        config.source.type = "camera"
        config.source.camera.index = args.index
        service.save(config)
        print(_ok(f"Источник: камера (index={args.index})"))
        return 0

    if args.command == "source" and args.source_command == "file":
        config.source.type = "file"
        config.source.file.path = str(Path(args.path).resolve())
        config.source.file.loop = _str_to_bool(args.loop)
        service.save(config)
        print(_ok(f"Источник: файл ({config.source.file.path}), loop={config.source.file.loop}"))
        return 0

    if args.command == "view":
        from __core.__camera.viewer import CameraViewer

        viewer = CameraViewer(config=config)
        viewer.run()
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
