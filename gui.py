from __future__ import annotations

import asyncio
import base64
import copy
import json
import logging
import math
import platform
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import flet as ft

from __core.__camera import ConfigService
from __core.__camera.__neural.base import FrameContext
from __core.__camera.__neural.manager import NeuralManager
from __core.__camera.video_source import VideoSourceFactory
from __core.__movement import Mach3Bridge

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
    force=True,
)
log = logging.getLogger("gui")

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
TITLE = "Болховский завод полупроводниковых приборов (АО «БЗПП»)"
_CTRL_W = 380

_IS_MAC = platform.system() == "Darwin"
_MOD_KEY = "⌘" if _IS_MAC else "Ctrl"

HOTKEYS = [
    ("Старт / Стоп",     f"{_MOD_KEY}+Enter"),
    ("Пауза / Продолжить", "Пробел"),
    ("Захват кадра",      f"{_MOD_KEY}+Shift+C"),
    ("ПИН элемент",       f"{_MOD_KEY}+Shift+P"),
    ("Трансформация",     f"{_MOD_KEY}+Shift+T"),
    ("Станок Mach3",      f"{_MOD_KEY}+Shift+M"),
    ("Перезапуск",        f"{_MOD_KEY}+Shift+X"),
    ("Горячие клавиши",   "?"),
    ("", ""),
    ("Джог X−/X+",        "← / →"),
    ("Джог Y+/Y−",        "↑ / ↓"),
    ("Джог Z+/Z−",        "PgUp / PgDn"),
    ("Джог A−/A+",        "Home / End"),
]
JPEG_Q = 80
TARGET_FPS = 25
CAM_SCAN_RANGE = 10


def _scan_cameras() -> list[tuple[int, int, int]]:
    found: list[tuple[int, int, int]] = []
    for i in range(CAM_SCAN_RANGE):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            found.append((i, w, h))
            cap.release()
        else:
            cap.release()
    return found


def _probe_video(path: str) -> dict | None:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        cap.release()
        return None
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur = total / fps if fps > 0 else 0
    cap.release()
    return {"w": w, "h": h, "fps": round(fps, 1), "frames": total, "dur": round(dur, 1)}


class _StreamEngine:

    def __init__(self) -> None:
        self._source = None
        self._neural: NeuralManager | None = None
        self._running = False
        self._fps: float = 0.0
        self._frame_index: int = 0
        self._source_type: str = ""
        self._source_label: str = ""
        self.last_shared: dict = {}
        self.last_b64: str = ""
        self.last_frame_w: int = 0
        self.last_frame_h: int = 0
        self.pin_tid: int | None = None
        self.transform_angles: list[float] = [0.0, 0.0, 0.0]
        self.transform_before_neural: bool = True
        self.mach3 = Mach3Bridge()

    def start(self, config) -> None:
        self._stop_internal()
        log.info("Engine: creating source type=%s", config.source.type)
        self._source = VideoSourceFactory.from_config(config)
        log.info("Engine: source OK, mods=%s", config.neural.mods)
        self._neural = NeuralManager(mod_names=config.neural.mods)
        self._source_type = config.source.type
        self._source_label = (
            str(config.source.camera.index)
            if config.source.type == "camera"
            else config.source.file.path
        )
        self._frame_index = 0
        self._fps = 0.0
        self._running = True
        self.last_shared = {}
        self.last_b64 = ""
        log.info("Engine: started")

    def stop(self) -> None:
        self._stop_internal()

    def _stop_internal(self) -> None:
        self._running = False
        src = self._source
        self._source = None
        self._neural = None
        if src is not None:
            try:
                src.release()
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return self._running

    @property
    def fps(self) -> float:
        return self._fps

    def read_and_encode(self) -> str | None:
        if not self._running or self._source is None:
            return None
        src = self._source
        neural = self._neural
        if src is None:
            return None

        t0 = time.perf_counter()
        try:
            result = src.read()
        except Exception:
            log.exception("Engine: source.read() error")
            self._running = False
            return None

        if not result.ok or result.frame is None:
            self._running = False
            return None

        self._frame_index += 1
        raw_frame = result.frame

        if self._has_transform() and self.transform_before_neural:
            raw_frame = self._apply_transform(raw_frame)

        fh, fw = raw_frame.shape[:2]
        ctx = FrameContext(
            fps=self._fps,
            source_type=self._source_type,
            source_label=self._source_label,
            frame_width=fw,
            frame_height=fh,
            frame_index=self._frame_index,
        )

        if self.pin_tid is not None:
            ctx.shared["_pin_request"] = self.pin_tid
        else:
            ctx.shared["_pin_request"] = 0

        try:
            frame = neural.apply(raw_frame, ctx) if neural else raw_frame
        except Exception:
            log.exception("Engine: neural.apply() error")
            frame = raw_frame

        if self._has_transform() and not self.transform_before_neural:
            frame = self._apply_transform(frame)

        self.last_shared = dict(ctx.shared)
        self.last_frame_h, self.last_frame_w = frame.shape[:2]

        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
        if not ok:
            return None

        dt = time.perf_counter() - t0
        if dt > 0:
            self._fps = self._fps * 0.9 + (1.0 / dt) * 0.1

        b64 = base64.b64encode(buf).decode("ascii")
        self.last_b64 = b64
        return b64

    def _has_transform(self) -> bool:
        a = self.transform_angles
        return a[0] != 0.0 or a[1] != 0.0 or a[2] != 0.0

    def _apply_transform(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        cx, cy = w / 2.0, h / 2.0
        ax, ay, az = self.transform_angles

        if az != 0.0:
            M = cv2.getRotationMatrix2D((cx, cy), -az, 1.0)
            frame = cv2.warpAffine(frame, M, (w, h), borderMode=cv2.BORDER_REPLICATE)

        if ax != 0.0 or ay != 0.0:
            f = max(w, h)
            rx = math.radians(ax)
            ry = math.radians(ay)

            src_pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]])

            dx_top = math.tan(ry) * f * 0.3
            dy_left = math.tan(rx) * f * 0.3

            dst_pts = np.float32([
                [0 + dx_top, 0 + dy_left],
                [w - dx_top, 0 - dy_left],
                [w + dx_top, h - dy_left],
                [0 - dx_top, h + dy_left],
            ])

            M_persp = cv2.getPerspectiveTransform(src_pts, dst_pts)
            frame = cv2.warpPerspective(frame, M_persp, (w, h), borderMode=cv2.BORDER_REPLICATE)

        return frame


async def main(page: ft.Page) -> None:
    log.info("=== Flet main() ===")
    page.title = TITLE
    _raw_boot = {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as _bf:
            _raw_boot = json.load(_bf)
    except Exception:
        pass
    _wm = _raw_boot.get("app", {}).get("window_mode", "normal")

    if _wm == "fullscreen":
        page.window.full_screen = True
    else:
        page.window.width = 1320
        page.window.height = 800

    page.padding = 0
    page.spacing = 0
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed=ft.Colors.BLUE_GREY)

    service = ConfigService(config_path=CONFIG_PATH)
    config = service.load()
    engine = _StreamEngine()
    available_mods = NeuralManager.list_available()
    ev_loop = asyncio.get_event_loop()

    saved_snapshot: dict = _ui_snapshot_from_config(config, available_mods)
    running_snapshot: dict | None = None
    _paused: bool = False

    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    # ── camera panel ──────────────────────────────────────────────

    cam_dd = ft.Dropdown(
        label="Камера",
        width=_CTRL_W,
        dense=True,
        options=[],
        on_select=lambda e: _on_any_change(),
    )

    cam_scan_status = ft.Text("", size=11, color=ft.Colors.WHITE38)

    async def on_scan_cameras(e) -> None:
        cam_scan_status.value = "Сканирование..."
        cam_scan_status.color = ft.Colors.YELLOW_300
        scan_btn.disabled = True
        page.update()
        found = await ev_loop.run_in_executor(None, _scan_cameras)
        cam_dd.options.clear()
        if found:
            for idx, w, h in found:
                cam_dd.options.append(
                    ft.dropdown.Option(str(idx), f"Камера {idx}  ({w}×{h})")
                )
            cam_dd.value = str(found[0][0])
            cam_scan_status.value = f"Найдено: {len(found)}"
            cam_scan_status.color = ft.Colors.GREEN_300
        else:
            cam_scan_status.value = "Камеры не найдены"
            cam_scan_status.color = ft.Colors.RED_300
        scan_btn.disabled = False
        page.update()

    _preview_running = False

    async def on_cam_preview(e) -> None:
        nonlocal _preview_running
        if not cam_dd.value:
            return
        idx = int(cam_dd.value)

        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            cap.release()
            cam_scan_status.value = "Не удалось открыть камеру"
            cam_scan_status.color = ft.Colors.RED_300
            page.update()
            return

        _preview_running = True
        preview_img = ft.Image(
            src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
            fit=ft.BoxFit.CONTAIN, width=640, gapless_playback=True,
        )

        def on_close(_) -> None:
            nonlocal _preview_running
            _preview_running = False

        dlg = ft.AlertDialog(
            title=ft.Text(f"Превью — Камера {idx}"),
            content=ft.Container(content=preview_img, width=640, height=480),
            actions=[ft.Button(content=ft.Text("Закрыть"), on_click=on_close)],
            on_dismiss=on_close,
        )
        page.show_dialog(dlg)
        page.update()

        def _read_cam_frame() -> str | None:
            ok, frame = cap.read()
            if not ok or frame is None:
                return None
            ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            return base64.b64encode(buf).decode("ascii") if ok2 else None

        try:
            while _preview_running:
                b64 = await ev_loop.run_in_executor(None, _read_cam_frame)
                if b64 is None or not _preview_running:
                    break
                preview_img.src = f"data:image/jpeg;base64,{b64}"
                page.update()
                await asyncio.sleep(1.0 / 20)
        except asyncio.CancelledError:
            pass
        finally:
            cap.release()
            _preview_running = False
            try:
                page.pop_dialog()
            except Exception:
                pass
            page.update()

    scan_btn = ft.Button(
        content=ft.Text("Поиск камер"), icon=ft.Icons.SEARCH,
        on_click=on_scan_cameras, width=_CTRL_W, height=36,
    )
    preview_btn = ft.Button(
        content=ft.Text("Превью"), icon=ft.Icons.VISIBILITY,
        on_click=on_cam_preview, width=_CTRL_W, height=36,
    )
    cam_panel = ft.Column(
        controls=[scan_btn, cam_dd, preview_btn, cam_scan_status],
        spacing=8, visible=config.source.type == "camera",
    )

    # ── file panel ────────────────────────────────────────────────

    file_info_text = ft.Text("", size=11, color=ft.Colors.WHITE38)

    async def _update_file_info(path: str) -> None:
        info = await ev_loop.run_in_executor(None, _probe_video, path)
        if info:
            file_info_text.value = (
                f"{info['w']}×{info['h']}  •  {info['fps']} fps  •  "
                f"{info['frames']} кадров  •  {info['dur']} сек"
            )
        else:
            file_info_text.value = "Файл не найден или повреждён" if path.strip() else ""
        page.update()

    file_path_tf = ft.TextField(
        label="Путь к видеофайлу", value=config.source.file.path,
        width=_CTRL_W - 40, dense=True,
        on_change=lambda e: _on_file_path_change(),
    )

    async def on_browse(e) -> None:
        result = await file_picker.pick_files(
            dialog_title="Выберите видеофайл",
            file_type=ft.FilePickerFileType.VIDEO, allow_multiple=False,
        )
        if result and len(result) > 0:
            chosen = result[0].path
            if chosen:
                file_path_tf.value = chosen
                page.update()
                await _update_file_info(chosen)
                _on_any_change()

    browse_btn = ft.IconButton(icon=ft.Icons.FOLDER_OPEN, tooltip="Обзор...", on_click=on_browse)
    file_path_row = ft.Row(
        controls=[file_path_tf, browse_btn], spacing=4,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    loop_cb = ft.Checkbox(
        label="Зациклить воспроизведение", value=config.source.file.loop,
        on_change=lambda e: _on_any_change(),
    )
    file_panel = ft.Column(
        controls=[file_path_row, loop_cb, file_info_text],
        spacing=8, visible=config.source.type == "file",
    )

    # ── source toggle ─────────────────────────────────────────────

    def on_source_switch(e) -> None:
        is_cam = "camera" in source_toggle.selected
        cam_panel.visible = is_cam
        file_panel.visible = not is_cam
        page.update()
        _on_any_change()

    source_toggle = ft.SegmentedButton(
        segments=[
            ft.Segment(value="camera", label=ft.Text("Камера"), icon=ft.Icon(ft.Icons.VIDEOCAM)),
            ft.Segment(value="file", label=ft.Text("Видеофайл"), icon=ft.Icon(ft.Icons.VIDEO_FILE)),
        ],
        selected=["camera"] if config.source.type == "camera" else ["file"],
        allow_empty_selection=False, allow_multiple_selection=False,
        on_change=on_source_switch, show_selected_icon=False, width=_CTRL_W,
    )

    # ── neural mods ───────────────────────────────────────────────

    MOD_META: dict[str, dict] = {
        "__hud_info": {
            "title": "HUD Info",
            "desc": "Отображение технической информации поверх кадра (FPS, разрешение и т.д.)",
            "deps": [],
            "required": False,
        },
        "__particle_centers": {
            "title": "Детектор кристаллов",
            "desc": "Основной мод. Обнаружение элементов, трекинг, определение стороны (верх/низ)",
            "deps": [],
            "required": True,
        },
        "__particle_centers_pin": {
            "title": "Фиксация (ПИН)",
            "desc": "Закрепление и отслеживание выбранного элемента с визуальным выделением",
            "deps": ["__particle_centers"],
            "required": False,
        },
        "__particle_centers_grid": {
            "title": "Координатная сетка",
            "desc": "Сетка мировых координат и подписи позиций элементов (мм)",
            "deps": ["__particle_centers"],
            "required": False,
        },
        "__particle_centers_nearest": {
            "title": "Ближайший элемент",
            "desc": "Выделение ближайшего «верхнего» кристалла, линия от центра, координаты и угол",
            "deps": ["__particle_centers", "__particle_centers_grid"],
            "required": False,
        },
        "__particle_centers_movement": {
            "title": "Движение / Станок",
            "desc": "Визуализация позиции вакуумной трубки и данные для управления станком Mach3",
            "deps": ["__particle_centers", "__particle_centers_nearest"],
            "required": False,
        },
    }

    mod_checks: dict[str, ft.Checkbox] = {}
    active_set = set(config.neural.mods)
    for mod_name in available_mods:
        mod_checks[mod_name] = ft.Checkbox(
            label=mod_name.lstrip("_"), value=mod_name in active_set,
            on_change=lambda e: _on_any_change(),
        )

    # ── status bar ────────────────────────────────────────────────

    status_fps = ft.Text("FPS: --", size=12, color=ft.Colors.WHITE70)
    status_source = ft.Text("Источник: --", size=12, color=ft.Colors.WHITE70)
    status_mods = ft.Text("Моды: --", size=12, color=ft.Colors.WHITE70)
    status_state = ft.Text("Остановлен", size=12, color=ft.Colors.ORANGE_300)

    # ── video area ────────────────────────────────────────────────

    video_image = ft.Image(
        src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
        fit=ft.BoxFit.CONTAIN, gapless_playback=True, expand=True,
    )
    video_placeholder = ft.Container(
        content=ft.Column(
            controls=[
                ft.Icon(ft.Icons.VIDEOCAM_OFF, size=64, color=ft.Colors.WHITE24),
                ft.Text("Нажмите «Старт» для запуска видеопотока",
                         size=16, color=ft.Colors.WHITE38, text_align=ft.TextAlign.CENTER),
                ft.Text("Источник видео настраивается в ⚙ Настройки",
                         size=12, color=ft.Colors.WHITE24, text_align=ft.TextAlign.CENTER),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12,
        ),
        alignment=ft.Alignment.CENTER, expand=True,
    )

    _vid_scale: float = 1.0

    video_frame = ft.Container(
        content=video_image,
        border_radius=10,
        border=ft.Border.all(1, ft.Colors.WHITE10),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS, expand=True,
        bgcolor=ft.Colors.with_opacity(0.15, ft.Colors.WHITE), visible=False,
    )

    _scale_text = ft.Text(
        "100%", size=11, color=ft.Colors.WHITE54,
        text_align=ft.TextAlign.CENTER,
    )

    def _on_scale_click(e) -> None:
        nonlocal _vid_scale
        _vid_scale = 1.0
        _apply_video_scale()
        page.update()

    def _on_scale_double_click(e) -> None:
        field = ft.TextField(
            value=str(int(_vid_scale * 100)), width=80, text_size=13,
            dense=True, autofocus=True, text_align=ft.TextAlign.CENTER,
            suffix=ft.Text("%", size=12, color=ft.Colors.WHITE38),
            keyboard_type=ft.KeyboardType.NUMBER,
        )

        def _apply(_) -> None:
            nonlocal _vid_scale
            try:
                val = int(field.value.replace("%", "").strip())
                _vid_scale = max(0.2, min(4.0, val / 100.0))
            except (ValueError, TypeError):
                pass
            page.pop_dialog()
            _apply_video_scale()
            page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("Масштаб видео", size=14, weight=ft.FontWeight.W_600),
            content=ft.Row(
                controls=[field],
                alignment=ft.MainAxisAlignment.CENTER,
                tight=True,
            ),
            actions=[
                ft.Button(content=ft.Text("OK"), on_click=_apply),
                ft.OutlinedButton(
                    content=ft.Text("Отмена"),
                    on_click=lambda _: (page.pop_dialog(), page.update()),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        field.on_submit = _apply
        page.show_dialog(dlg)
        page.update()

    scale_click_detector = ft.GestureDetector(
        content=ft.Container(
            content=_scale_text,
            padding=ft.Padding.symmetric(horizontal=6, vertical=4),
            border_radius=4,
        ),
        on_tap=_on_scale_click,
        on_double_tap=_on_scale_double_click,
        mouse_cursor=ft.MouseCursor.CLICK,
    )

    def _apply_video_scale() -> None:
        pct = int(round(_vid_scale * 100))
        _scale_text.value = f"{pct}%"
        if _vid_scale == 1.0:
            video_frame.expand = True
            video_frame.width = None
            video_frame.height = None
            _scale_text.color = ft.Colors.WHITE38
        else:
            src_w = engine.last_frame_w if engine.last_frame_w else 640
            src_h = engine.last_frame_h if engine.last_frame_h else 480
            video_frame.expand = False
            video_frame.width = max(160, src_w * _vid_scale)
            video_frame.height = max(120, src_h * _vid_scale)
            _scale_text.color = ft.Colors.BLUE_200
        _save_scale_to_config()

    def _video_res_key() -> str:
        w = engine.last_frame_w
        h = engine.last_frame_h
        return f"{w}x{h}" if w and h else ""

    _last_saved_scale: float = 1.0

    def _save_scale_to_config() -> None:
        nonlocal _last_saved_scale
        key = _video_res_key()
        if not key:
            return
        rounded = round(_vid_scale, 2)
        if rounded == _last_saved_scale:
            return
        _last_saved_scale = rounded
        try:
            raw = {}
            with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
                raw = json.load(fp)
            raw.setdefault("app", {}).setdefault("video_scales", {})[key] = rounded
            with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
                json.dump(raw, fp, ensure_ascii=False, indent=2)
                fp.write("\n")
        except Exception:
            pass

    def _load_scale_from_config() -> None:
        nonlocal _vid_scale
        key = _video_res_key()
        if not key:
            return
        try:
            saved = _raw_boot.get("app", {}).get("video_scales", {}).get(key)
            if saved is not None:
                _vid_scale = max(0.2, min(4.0, float(saved)))
                _apply_video_scale()
        except Exception:
            pass

    def _on_video_scroll(e) -> None:
        nonlocal _vid_scale
        dy = e.scroll_delta.y if hasattr(e, "scroll_delta") else 0
        if dy < 0:
            _vid_scale = min(4.0, round(_vid_scale + 0.1, 2))
        elif dy > 0:
            _vid_scale = max(0.2, round(_vid_scale - 0.1, 2))
        _apply_video_scale()
        page.update()

    video_scroll_detector = ft.GestureDetector(
        content=video_frame,
        on_scroll=_on_video_scroll,
        visible=False,
    )

    def _zoom_in(e=None) -> None:
        nonlocal _vid_scale
        _vid_scale = min(4.0, round(_vid_scale + 0.25, 2))
        _apply_video_scale()
        page.update()

    def _zoom_out(e=None) -> None:
        nonlocal _vid_scale
        _vid_scale = max(0.2, round(_vid_scale - 0.25, 2))
        _apply_video_scale()
        page.update()

    def _reset_video_size(e=None) -> None:
        nonlocal _vid_scale
        _vid_scale = 1.0
        _apply_video_scale()
        page.update()

    zoom_out_btn = ft.IconButton(
        icon=ft.Icons.REMOVE, tooltip="Уменьшить",
        icon_color=ft.Colors.WHITE54, icon_size=16,
        style=ft.ButtonStyle(padding=ft.Padding.all(4)),
        on_click=_zoom_out, visible=False,
    )
    zoom_in_btn = ft.IconButton(
        icon=ft.Icons.ADD, tooltip="Увеличить",
        icon_color=ft.Colors.WHITE54, icon_size=16,
        style=ft.ButtonStyle(padding=ft.Padding.all(4)),
        on_click=_zoom_in, visible=False,
    )
    reset_size_btn = ft.IconButton(
        icon=ft.Icons.FIT_SCREEN, tooltip="Сбросить масштаб (100%)",
        icon_color=ft.Colors.WHITE38, icon_size=16,
        style=ft.ButtonStyle(padding=ft.Padding.all(4)),
        on_click=_reset_video_size, visible=False,
    )
    zoom_group = ft.Container(
        content=ft.Row(
            controls=[zoom_out_btn, scale_click_detector, zoom_in_btn, reset_size_btn],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        ),
        border_radius=6,
        bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.WHITE),
        padding=ft.Padding.symmetric(horizontal=2),
        visible=False,
    )

    video_stack = ft.Stack(controls=[video_placeholder, video_scroll_detector], expand=True)

    # ── video toolbar (pause / capture) ───────────────────────────

    def on_pause(e) -> None:
        nonlocal _paused
        _paused = not _paused
        if _paused:
            pause_btn.icon = ft.Icons.PLAY_ARROW
            pause_btn.tooltip = "Продолжить"
            status_state.value = "Пауза"
            status_state.color = ft.Colors.YELLOW_300
        else:
            pause_btn.icon = ft.Icons.PAUSE
            pause_btn.tooltip = "Пауза"
            status_state.value = "Воспроизведение"
            status_state.color = ft.Colors.GREEN_300
        page.update()

    pause_btn = ft.IconButton(
        icon=ft.Icons.PAUSE, tooltip="Пауза",
        icon_color=ft.Colors.WHITE, icon_size=28, visible=False,
        on_click=on_pause,
    )

    # ── capture dialog ───────────────────────────────────────────

    def _build_data_row(icon: str, label: str, value: str) -> ft.Row:
        return ft.Row(
            controls=[
                ft.Icon(icon, size=16, color=ft.Colors.BLUE_200),
                ft.Text(label, size=12, color=ft.Colors.WHITE54, width=100),
                ft.Text(value, size=12, color=ft.Colors.WHITE, selectable=True),
            ],
            spacing=8,
        )

    def on_capture(e) -> None:
        nonlocal _paused
        _paused = True
        pause_btn.icon = ft.Icons.PLAY_ARROW
        pause_btn.tooltip = "Продолжить"
        status_state.value = "Захват"
        status_state.color = ft.Colors.AMBER_300

        b64 = engine.last_b64
        shared = copy.deepcopy(engine.last_shared)
        nearest = shared.get("particle_nearest")

        raw_bytes = base64.b64decode(b64) if b64 else None
        src_img = None
        if raw_bytes is not None:
            arr = np.frombuffer(raw_bytes, dtype=np.uint8)
            src_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

        IMG_W, IMG_H = 540, 400
        _lens_d = [150]
        _mag_power = [3.0]
        _last_lens_cell = [-1, -1]
        _THROTTLE_PX = 4

        def _build_lens_mask(d: int) -> np.ndarray:
            m = np.zeros((d, d), dtype=np.uint8)
            cv2.circle(m, (d // 2, d // 2), d // 2, 255, -1)
            return m

        _lens_mask_arr = [_build_lens_mask(_lens_d[0])]

        cap_img = ft.Image(
            src=f"data:image/jpeg;base64,{b64}" if b64 else "",
            fit=ft.BoxFit.CONTAIN,
            width=IMG_W, height=IMG_H,
        )

        _EMPTY_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQABNjN9GQAAAAlwSFlzAAAWJQAAFiUBSVIk8AAAAA0lEQVQI12P4z8BQDwAEgAF/QualIQAAAABJRU5ErkJggg=="
        D0 = _lens_d[0]
        lens_img = ft.Image(src=_EMPTY_PNG, width=D0, height=D0, visible=False, border_radius=D0 // 2)
        lens_ring = ft.Container(
            content=lens_img, width=D0, height=D0,
            border_radius=D0 // 2, visible=False,
            border=ft.Border.all(2, ft.Colors.BLUE_200),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            shadow=[ft.BoxShadow(blur_radius=10, color=ft.Colors.with_opacity(0.5, ft.Colors.BLACK))],
        )

        crosshair_h = ft.Container(width=12, height=1, bgcolor=ft.Colors.with_opacity(0.5, ft.Colors.BLUE_200))
        crosshair_v = ft.Container(width=1, height=12, bgcolor=ft.Colors.with_opacity(0.5, ft.Colors.BLUE_200))
        crosshair = ft.Container(
            content=ft.Stack(controls=[
                ft.Container(content=crosshair_h, alignment=ft.Alignment.CENTER),
                ft.Container(content=crosshair_v, alignment=ft.Alignment.CENTER),
            ], width=12, height=12),
            width=12, height=12, visible=False,
        )

        img_stack = ft.Stack(
            controls=[cap_img, lens_ring, crosshair],
            width=IMG_W, height=IMG_H,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

        def _resize_lens(d: int) -> None:
            _lens_d[0] = d
            _lens_mask_arr[0] = _build_lens_mask(d)
            lens_img.width = d
            lens_img.height = d
            lens_img.border_radius = d // 2
            lens_ring.width = d
            lens_ring.height = d
            lens_ring.border_radius = d // 2

        def _render_lens(lx: float, ly: float, force: bool = False) -> None:
            if src_img is None:
                return
            ix, iy = int(lx), int(ly)
            if not force:
                if abs(ix - _last_lens_cell[0]) < _THROTTLE_PX and abs(iy - _last_lens_cell[1]) < _THROTTLE_PX:
                    return
            _last_lens_cell[0] = ix
            _last_lens_cell[1] = iy

            d = _lens_d[0]
            ih, iw = src_img.shape[:2]
            sx = lx / IMG_W * iw
            sy = ly / IMG_H * ih
            mag = _mag_power[0]
            r = int((d / 2) / mag * (iw / IMG_W))
            x1, y1 = max(0, int(sx - r)), max(0, int(sy - r))
            x2, y2 = min(iw, int(sx + r)), min(ih, int(sy + r))
            if x2 <= x1 or y2 <= y1:
                return
            crop = src_img[y1:y2, x1:x2]
            zoomed = cv2.resize(crop, (d, d), interpolation=cv2.INTER_LINEAR)
            zoomed_rgba = cv2.cvtColor(zoomed, cv2.COLOR_BGR2BGRA)
            zoomed_rgba[:, :, 3] = _lens_mask_arr[0]
            _, buf = cv2.imencode(".png", zoomed_rgba)
            lens_img.src = f"data:image/png;base64,{base64.b64encode(buf).decode()}"

            half = d // 2
            lens_ring.left = lx - half
            lens_ring.top = ly - half
            lens_ring.visible = True
            lens_img.visible = True

            crosshair.left = lx - 6
            crosshair.top = ly - 6
            crosshair.visible = True

        def on_hover(e) -> None:
            _render_lens(e.local_position.x, e.local_position.y)
            page.update()

        def on_exit(e) -> None:
            lens_ring.visible = False
            lens_img.visible = False
            crosshair.visible = False
            _last_lens_cell[0] = -1
            _last_lens_cell[1] = -1
            page.update()

        gesture = ft.GestureDetector(
            content=img_stack,
            on_hover=on_hover,
            on_exit=on_exit,
            mouse_cursor=ft.MouseCursor.NONE,
        )

        img_container = ft.Container(
            content=gesture,
            width=IMG_W, height=IMG_H,
            border_radius=8,
            border=ft.Border.all(1, ft.Colors.WHITE10),
            bgcolor=ft.Colors.BLACK,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

        size_val = ft.Text(f"{_lens_d[0]} px", size=11, color=ft.Colors.WHITE54, width=48)
        zoom_val = ft.Text(f"{_mag_power[0]:.1f}×", size=11, color=ft.Colors.WHITE54, width=36)

        def _on_size(e) -> None:
            d = int(e.control.value)
            _resize_lens(d)
            size_val.value = f"{d} px"
            _last_lens_cell[0] = -1
            page.update()

        def _on_zoom(e) -> None:
            _mag_power[0] = round(e.control.value, 1)
            zoom_val.value = f"{_mag_power[0]:.1f}×"
            _last_lens_cell[0] = -1
            page.update()

        lens_controls = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Icon(ft.Icons.CIRCLE_OUTLINED, size=14, color=ft.Colors.WHITE38),
                    ft.Text("Размер", size=11, color=ft.Colors.WHITE54, width=48),
                    ft.Slider(min=80, max=280, value=_lens_d[0], divisions=20,
                              on_change=_on_size, expand=True),
                    size_val,
                    ft.VerticalDivider(width=1, color=ft.Colors.WHITE10),
                    ft.Icon(ft.Icons.ZOOM_IN, size=14, color=ft.Colors.WHITE38),
                    ft.Text("Оптика", size=11, color=ft.Colors.WHITE54, width=48),
                    ft.Slider(min=1.5, max=8.0, value=_mag_power[0], divisions=13,
                              on_change=_on_zoom, expand=True),
                    zoom_val,
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
            border_radius=6,
            bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.WHITE),
            border=ft.Border.all(1, ft.Colors.WHITE10),
        )

        if nearest:
            u = nearest.get("unit", "mm")
            data_rows = ft.Column(controls=[
                _build_data_row(ft.Icons.PLACE, "Координаты",
                                f"X: {nearest.get('x', 0):+.2f}   Y: {nearest.get('y', 0):+.2f} {u}"),
                _build_data_row(ft.Icons.STRAIGHTEN, "Расстояние",
                                f"{nearest.get('dist', 0):.2f} {u}  ({nearest.get('dist_px', 0):.0f} px)"),
                _build_data_row(ft.Icons.EXPLORE, "Направление",
                                f"{nearest.get('bearing_deg', 0):+.1f}°"),
                _build_data_row(ft.Icons.ROTATE_RIGHT, "Наклон",
                                f"{nearest.get('angle', 0):.1f}°"),
                _build_data_row(ft.Icons.ASPECT_RATIO, "Размер",
                                f"{nearest.get('w', 0):.2f} × {nearest.get('h', 0):.2f} {u}"),
                _build_data_row(ft.Icons.TAG, "Трек ID",
                                str(nearest.get("tid", "?"))),
                _build_data_row(ft.Icons.FLIP, "Сторона",
                                str(nearest.get("side", "?"))),
            ], spacing=6)
        else:
            data_rows = ft.Text(
                "Данные отсутствуют — мод particle_centers_nearest не активен",
                size=12, color=ft.Colors.WHITE38, italic=True,
            )

        def _close_capture(_) -> None:
            nonlocal _paused
            _paused = False
            pause_btn.icon = ft.Icons.PAUSE
            pause_btn.tooltip = "Пауза"
            status_state.value = "Воспроизведение"
            status_state.color = ft.Colors.GREEN_300
            page.pop_dialog()
            page.update()

        data_section = ft.Container(
            content=ft.Column(controls=[
                ft.Row(controls=[
                    ft.Icon(ft.Icons.ANALYTICS, size=18, color=ft.Colors.BLUE_200),
                    ft.Text("Данные ближайшего элемента", size=14, weight=ft.FontWeight.BOLD),
                ], spacing=8),
                ft.Divider(height=6, color=ft.Colors.WHITE10),
                data_rows,
            ], spacing=4),
            padding=ft.Padding.all(12),
            border_radius=8,
            bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.WHITE),
            border=ft.Border.all(1, ft.Colors.WHITE10),
        )

        dlg_content = ft.Column(
            controls=[img_container, lens_controls, ft.Container(height=4), data_section],
            spacing=4, scroll=ft.ScrollMode.AUTO, tight=True, width=IMG_W + 40,
        )

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row(controls=[
                ft.Icon(ft.Icons.CAMERA_ALT, color=ft.Colors.BLUE_200),
                ft.Text("Захват кадра", weight=ft.FontWeight.BOLD),
            ], spacing=8),
            content=dlg_content,
            actions=[ft.Button(content=ft.Text("Закрыть"), icon=ft.Icons.CLOSE, on_click=_close_capture)],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dlg)
        page.update()

    capture_btn = ft.IconButton(
        icon=ft.Icons.CAMERA_ALT, tooltip="Захват",
        icon_color=ft.Colors.WHITE70, icon_size=28, visible=False,
        on_click=on_capture,
    )

    # ── pin button ─────────────────────────────────────────────────

    def on_pin(e) -> None:
        if engine.pin_tid is not None:
            engine.pin_tid = None
            pin_btn.icon_color = ft.Colors.WHITE70
            pin_btn.tooltip = "Закрепить элемент (ПИН)"
        else:
            nearest = engine.last_shared.get("particle_nearest")
            if nearest and nearest.get("tid"):
                engine.pin_tid = nearest["tid"]
                pin_btn.icon_color = ft.Colors.YELLOW_400
                pin_btn.tooltip = f"Открепить ПИН #{nearest['tid']}"
        page.update()

    pin_btn = ft.IconButton(
        icon=ft.Icons.PUSH_PIN, tooltip="Закрепить элемент (ПИН)",
        icon_color=ft.Colors.WHITE70, icon_size=28, visible=False,
        on_click=on_pin,
    )

    # ── transform controller ──────────────────────────────────────

    slider_x_val = ft.Text("0°", size=11, color=ft.Colors.WHITE54, width=36)
    slider_y_val = ft.Text("0°", size=11, color=ft.Colors.WHITE54, width=36)
    slider_z_val = ft.Text("0°", size=11, color=ft.Colors.WHITE54, width=36)

    def _on_slider_x(e) -> None:
        engine.transform_angles[0] = round(e.control.value, 1)
        slider_x_val.value = f"{e.control.value:.0f}°"
        page.update()

    def _on_slider_y(e) -> None:
        engine.transform_angles[1] = round(e.control.value, 1)
        slider_y_val.value = f"{e.control.value:.0f}°"
        page.update()

    def _on_slider_z(e) -> None:
        engine.transform_angles[2] = round(e.control.value, 1)
        slider_z_val.value = f"{e.control.value:.0f}°"
        page.update()

    slider_x = ft.Slider(min=-45, max=45, value=0, divisions=18, on_change=_on_slider_x, expand=True)
    slider_y = ft.Slider(min=-45, max=45, value=0, divisions=18, on_change=_on_slider_y, expand=True)
    slider_z = ft.Slider(min=-180, max=180, value=0, divisions=72, on_change=_on_slider_z, expand=True)

    transform_mode_toggle = ft.SegmentedButton(
        selected=["before"],
        segments=[
            ft.Segment(value="before", label=ft.Text("До нейронки", size=11)),
            ft.Segment(value="after", label=ft.Text("После нейронки", size=11)),
        ],
        on_change=lambda e: setattr(engine, "transform_before_neural", "before" in e.control.selected),
    )

    def _reset_transform(e) -> None:
        engine.transform_angles[:] = [0.0, 0.0, 0.0]
        slider_x.value = 0
        slider_y.value = 0
        slider_z.value = 0
        slider_x_val.value = "0°"
        slider_y_val.value = "0°"
        slider_z_val.value = "0°"
        page.update()

    def _make_axis_row(label: str, slider, val_text) -> ft.Row:
        return ft.Row(
            controls=[
                ft.Text(label, size=11, color=ft.Colors.WHITE54, width=16),
                slider,
                val_text,
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    transform_panel = ft.Container(
        content=ft.Column(controls=[
            ft.Row(controls=[
                ft.Icon(ft.Icons.THREED_ROTATION, size=16, color=ft.Colors.BLUE_200),
                ft.Text("Трансформация кадра", size=12, weight=ft.FontWeight.W_600),
                ft.Container(expand=True),
                ft.IconButton(icon=ft.Icons.RESTART_ALT, tooltip="Сброс",
                              icon_size=18, icon_color=ft.Colors.WHITE38,
                              on_click=_reset_transform),
            ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            _make_axis_row("X", slider_x, slider_x_val),
            _make_axis_row("Y", slider_y, slider_y_val),
            _make_axis_row("Z", slider_z, slider_z_val),
            transform_mode_toggle,
        ], spacing=2, tight=True),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border_radius=8,
        bgcolor=ft.Colors.with_opacity(0.6, ft.Colors.BLACK),
        border=ft.Border.all(1, ft.Colors.WHITE10),
        visible=False,
    )

    def on_toggle_transform(e) -> None:
        transform_panel.visible = not transform_panel.visible
        if transform_panel.visible:
            transform_btn_wrap.bgcolor = ft.Colors.with_opacity(0.25, ft.Colors.BLUE_200)
            transform_btn.icon_color = ft.Colors.BLUE_200
        else:
            transform_btn_wrap.bgcolor = ft.Colors.TRANSPARENT
            transform_btn.icon_color = ft.Colors.WHITE70
        page.update()

    transform_btn = ft.IconButton(
        icon=ft.Icons.THREED_ROTATION, tooltip="Трансформация кадра",
        icon_color=ft.Colors.WHITE70, icon_size=28, visible=False,
        on_click=on_toggle_transform,
    )

    transform_btn_wrap = ft.Container(
        content=transform_btn,
        border_radius=8,
        bgcolor=ft.Colors.TRANSPARENT,
        padding=ft.Padding.all(0),
    )

    # ── machine / Mach3 control panel ─────────────────────────────

    _mach3_cfg = _raw_boot.get("movement", {})
    engine.mach3.configure(
        _mach3_cfg.get("mach3_ip", "192.168.1.125"),
        int(_mach3_cfg.get("mach3_port", 5555)),
    )

    _mach_connected = [False]
    _jog_step = [1.0]

    _JOG_STEPS = [0.01, 0.1, 1.0, 10.0]

    mach_appbar_icon = ft.Icon(ft.Icons.CIRCLE, size=10, color=ft.Colors.RED_400,
                               tooltip="Станок: отключён")

    mach_dro_x = ft.Text("X: --", size=12, color=ft.Colors.WHITE70, selectable=True, width=100)
    mach_dro_y = ft.Text("Y: --", size=12, color=ft.Colors.WHITE70, selectable=True, width=100)
    mach_dro_z = ft.Text("Z: --", size=12, color=ft.Colors.WHITE70, selectable=True, width=100)
    mach_dro_a = ft.Text("A: --", size=12, color=ft.Colors.WHITE70, selectable=True, width=100)

    mach_log_text = ft.Text("", size=11, color=ft.Colors.WHITE38, max_lines=2)

    def _update_dro_display(pos: dict | None) -> None:
        if pos is None:
            mach_dro_x.value = "X: --"
            mach_dro_y.value = "Y: --"
            mach_dro_z.value = "Z: --"
            mach_dro_a.value = "A: --"
        else:
            mach_dro_x.value = f"X: {pos['x']:.3f}"
            mach_dro_y.value = f"Y: {pos['y']:.3f}"
            mach_dro_z.value = f"Z: {pos['z']:.3f}"
            mach_dro_a.value = f"A: {pos['a']:.3f}"

    def _set_mach_status(state: str) -> None:
        _mach_connected[0] = (state == "ok")
        if state == "ok":
            mach_appbar_icon.color = ft.Colors.GREEN_400
            mach_appbar_icon.tooltip = "Станок: подключён"
        elif state == "busy":
            mach_appbar_icon.color = ft.Colors.YELLOW_400
            mach_appbar_icon.tooltip = "Станок: подключение..."
        elif state == "warn":
            mach_appbar_icon.color = ft.Colors.ORANGE_400
            mach_appbar_icon.tooltip = "Станок: ошибка приёмов"
        else:
            mach_appbar_icon.color = ft.Colors.RED_400
            mach_appbar_icon.tooltip = "Станок: отключён"

    async def _mach_auto_connect() -> None:
        _set_mach_status("busy")
        mach_log_text.value = "Автоподключение..."
        mach_log_text.color = ft.Colors.YELLOW_300
        page.update()
        ok = await ev_loop.run_in_executor(None, engine.mach3.connect)
        if ok:
            _set_mach_status("ok")
            _update_dro_display(engine.mach3.position)
            mach_log_text.value = "Подключено, обнуление..."
            mach_log_text.color = ft.Colors.WHITE38
            page.update()
            try:
                await ev_loop.run_in_executor(None, engine.mach3.send_named_command, "allzero")
                pos = await ev_loop.run_in_executor(None, engine.mach3.get_position)
                _update_dro_display(pos)
                mach_log_text.value = "Готов к работе"
                mach_log_text.color = ft.Colors.GREEN_300
            except Exception:
                _set_mach_status("warn")
                mach_log_text.value = "Подключён, но обнуление не удалось"
                mach_log_text.color = ft.Colors.ORANGE_300
        else:
            _set_mach_status("off")
            _update_dro_display(None)
            mach_log_text.value = "Станок недоступен — работа без станка"
            mach_log_text.color = ft.Colors.WHITE38
        page.update()

    async def _on_mach_connect(e) -> None:
        await _mach_auto_connect()

    async def _on_mach_disconnect(e) -> None:
        engine.mach3.disconnect()
        _set_mach_status("off")
        _update_dro_display(None)
        mach_log_text.value = "Отключено"
        page.update()

    async def _on_mach_refresh_dro(e) -> None:
        pos = await ev_loop.run_in_executor(None, engine.mach3.get_position)
        _update_dro_display(pos)
        page.update()

    async def _mach_jog(axis: str, delta: float) -> None:
        if not _mach_connected[0]:
            mach_log_text.value = "Станок не подключён"
            mach_log_text.color = ft.Colors.RED_300
            page.update()
            return
        feed = int(_mach3_cfg.get("feed_speed", 700))
        mach_log_text.value = f"{axis.upper()} {delta:+.3f}..."
        mach_log_text.color = ft.Colors.WHITE38
        page.update()
        try:
            await ev_loop.run_in_executor(
                None, engine.mach3.move_relative, axis, delta, feed
            )
            pos = engine.mach3.position
            _update_dro_display(pos)
            mach_log_text.value = f"{axis.upper()} {delta:+.3f} → OK"
            mach_log_text.color = ft.Colors.GREEN_300
        except Exception as exc:
            _set_mach_status("warn")
            mach_log_text.value = f"Ошибка: {exc}"
            mach_log_text.color = ft.Colors.RED_300
        page.update()

    def _jog_handler(axis: str, sign: int):
        return lambda e: asyncio.ensure_future(_mach_jog(axis, sign * _jog_step[0]))

    step_label = ft.Text(f"{_jog_step[0]}",
                         size=12, weight=ft.FontWeight.W_600,
                         color=ft.Colors.BLUE_200, text_align=ft.TextAlign.CENTER, width=44)

    def _on_step_change(e) -> None:
        idx = _JOG_STEPS.index(_jog_step[0]) if _jog_step[0] in _JOG_STEPS else 2
        idx = (idx + 1) % len(_JOG_STEPS)
        _jog_step[0] = _JOG_STEPS[idx]
        step_label.value = f"{_jog_step[0]}"
        page.update()

    _DPAD = ft.ButtonStyle(padding=ft.Padding.all(0))
    _DPAD_SZ = 36

    dpad_xy = ft.Column(controls=[
        ft.Row(controls=[
            ft.Container(width=_DPAD_SZ),
            ft.IconButton(icon=ft.Icons.ARROW_DROP_UP, tooltip="Y+",
                          icon_size=22, on_click=_jog_handler("y", 1),
                          style=_DPAD, width=_DPAD_SZ, height=_DPAD_SZ),
            ft.Container(width=_DPAD_SZ),
        ], spacing=0, alignment=ft.MainAxisAlignment.CENTER, tight=True),
        ft.Row(controls=[
            ft.IconButton(icon=ft.Icons.ARROW_LEFT, tooltip="X-",
                          icon_size=22, on_click=_jog_handler("x", -1),
                          style=_DPAD, width=_DPAD_SZ, height=_DPAD_SZ),
            ft.Container(
                content=ft.GestureDetector(
                    content=ft.Container(
                        content=step_label,
                        alignment=ft.Alignment.CENTER,
                        width=_DPAD_SZ, height=_DPAD_SZ,
                        border_radius=6,
                        bgcolor=ft.Colors.with_opacity(0.08, ft.Colors.WHITE),
                    ),
                    on_tap=_on_step_change,
                    mouse_cursor=ft.MouseCursor.CLICK,
                ),
                tooltip="Шаг (клик для смены)",
            ),
            ft.IconButton(icon=ft.Icons.ARROW_RIGHT, tooltip="X+",
                          icon_size=22, on_click=_jog_handler("x", 1),
                          style=_DPAD, width=_DPAD_SZ, height=_DPAD_SZ),
        ], spacing=0, alignment=ft.MainAxisAlignment.CENTER, tight=True),
        ft.Row(controls=[
            ft.Container(width=_DPAD_SZ),
            ft.IconButton(icon=ft.Icons.ARROW_DROP_DOWN, tooltip="Y-",
                          icon_size=22, on_click=_jog_handler("y", -1),
                          style=_DPAD, width=_DPAD_SZ, height=_DPAD_SZ),
            ft.Container(width=_DPAD_SZ),
        ], spacing=0, alignment=ft.MainAxisAlignment.CENTER, tight=True),
    ], spacing=0, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

    dpad_z = ft.Column(controls=[
        ft.IconButton(icon=ft.Icons.ARROW_DROP_UP, tooltip="Z+",
                      icon_size=22, on_click=_jog_handler("z", 1),
                      style=_DPAD, width=_DPAD_SZ, height=_DPAD_SZ),
        ft.Text("Z", size=11, color=ft.Colors.WHITE38, text_align=ft.TextAlign.CENTER, width=_DPAD_SZ),
        ft.IconButton(icon=ft.Icons.ARROW_DROP_DOWN, tooltip="Z-",
                      icon_size=22, on_click=_jog_handler("z", -1),
                      style=_DPAD, width=_DPAD_SZ, height=_DPAD_SZ),
    ], spacing=0, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

    dpad_a = ft.Column(controls=[
        ft.IconButton(icon=ft.Icons.ROTATE_LEFT, tooltip="A-",
                      icon_size=20, on_click=_jog_handler("a", -1),
                      style=_DPAD, width=_DPAD_SZ, height=_DPAD_SZ),
        ft.Text("A", size=11, color=ft.Colors.WHITE38, text_align=ft.TextAlign.CENTER, width=_DPAD_SZ),
        ft.IconButton(icon=ft.Icons.ROTATE_RIGHT, tooltip="A+",
                      icon_size=20, on_click=_jog_handler("a", 1),
                      style=_DPAD, width=_DPAD_SZ, height=_DPAD_SZ),
    ], spacing=0, tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

    jog_hint = ft.Text("← → ↑ ↓  PgUp/Dn=Z  Home/End=A",
                        size=10, color=ft.Colors.WHITE24, italic=True)

    async def _on_named_cmd(cmd_name: str) -> None:
        if not _mach_connected[0]:
            mach_log_text.value = "Станок не подключён"
            mach_log_text.color = ft.Colors.RED_300
            page.update()
            return
        mach_log_text.value = f"Команда: {cmd_name}..."
        mach_log_text.color = ft.Colors.WHITE38
        page.update()
        try:
            await ev_loop.run_in_executor(None, engine.mach3.send_named_command, cmd_name)
            pos = await ev_loop.run_in_executor(None, engine.mach3.get_position)
            _update_dro_display(pos)
            mach_log_text.value = f"{cmd_name} → OK"
            mach_log_text.color = ft.Colors.GREEN_300
        except Exception as exc:
            mach_log_text.value = f"Ошибка: {exc}"
            mach_log_text.color = ft.Colors.RED_300
        page.update()

    def _named_cmd_handler(cmd: str):
        return lambda e: asyncio.ensure_future(_on_named_cmd(cmd))

    async def _on_goto_crystal(e) -> None:
        if not _mach_connected[0]:
            mach_log_text.value = "Станок не подключён"
            mach_log_text.color = ft.Colors.RED_300
            page.update()
            return

        mv_data = engine.last_shared.get("particle_movement")
        if not mv_data:
            mach_log_text.value = "Нет данных о кристалле"
            mach_log_text.color = ft.Colors.ORANGE_300
            page.update()
            return

        wx = mv_data["crystal_world_x"]
        wy = mv_data["crystal_world_y"]
        vox = float(_mach3_cfg.get("vacuum_offset_x", -44.0))
        voy = float(_mach3_cfg.get("vacuum_offset_y", -6.0))
        feed = int(_mach3_cfg.get("feed_speed", 700))

        tx = round(wx + vox, 3)
        ty = round(wy + voy, 3)
        mach_log_text.value = f"→ Кристалл: ({tx:.2f}, {ty:.2f})..."
        mach_log_text.color = ft.Colors.WHITE38
        page.update()

        try:
            await ev_loop.run_in_executor(
                None, engine.mach3.move_to_crystal, wx, wy, vox, voy, feed
            )
            pos = await ev_loop.run_in_executor(None, engine.mach3.get_position)
            _update_dro_display(pos)
            mach_log_text.value = f"Кристалл → ({tx:.2f}, {ty:.2f}) OK"
            mach_log_text.color = ft.Colors.GREEN_300
        except Exception as exc:
            mach_log_text.value = f"Ошибка: {exc}"
            mach_log_text.color = ft.Colors.RED_300
        page.update()

    _named_cmds = [
        ("tozero", "Домой", ft.Icons.HOME),
        ("zerotocamera", "К камере", ft.Icons.CAMERA),
        ("zerotozond", "К зонду", ft.Icons.GAVEL),
        ("allzero", "Обнулить", ft.Icons.EXPOSURE_ZERO),
    ]

    named_cmd_row = ft.Row(
        controls=[
            ft.OutlinedButton(
                content=ft.Row(controls=[
                    ft.Icon(ic, size=14), ft.Text(lbl, size=10),
                ], spacing=4, tight=True),
                on_click=_named_cmd_handler(cmd),
                height=30,
                style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8, vertical=2)),
            )
            for cmd, lbl, ic in _named_cmds
        ],
        spacing=4,
        wrap=True,
    )

    machine_panel = ft.Container(
        content=ft.Column(controls=[
            ft.Row(controls=[
                ft.Icon(ft.Icons.PRECISION_MANUFACTURING, size=16, color=ft.Colors.BLUE_200),
                ft.Text("Станок Mach3", size=12, weight=ft.FontWeight.W_600),
                ft.Container(expand=True),
                mach_appbar_icon,
                ft.Container(width=4),
                ft.IconButton(icon=ft.Icons.LINK, tooltip="Подключить",
                              icon_size=16, icon_color=ft.Colors.GREEN_300,
                              on_click=_on_mach_connect,
                              style=ft.ButtonStyle(padding=ft.Padding.all(4))),
                ft.IconButton(icon=ft.Icons.LINK_OFF, tooltip="Отключить",
                              icon_size=16, icon_color=ft.Colors.RED_300,
                              on_click=_on_mach_disconnect,
                              style=ft.ButtonStyle(padding=ft.Padding.all(4))),
                ft.IconButton(icon=ft.Icons.REFRESH, tooltip="Обновить DRO",
                              icon_size=16, icon_color=ft.Colors.WHITE38,
                              on_click=_on_mach_refresh_dro,
                              style=ft.ButtonStyle(padding=ft.Padding.all(4))),
            ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(
                content=ft.Row(controls=[mach_dro_x, mach_dro_y, mach_dro_z, mach_dro_a],
                               spacing=8),
                padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                border_radius=6,
                bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.WHITE),
            ),
            ft.Divider(height=4, color=ft.Colors.WHITE10),
            ft.Row(controls=[
                ft.Text("Управление", size=11, color=ft.Colors.WHITE54),
                ft.Container(expand=True),
                jog_hint,
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row(controls=[
                dpad_xy,
                ft.VerticalDivider(width=1, color=ft.Colors.WHITE10),
                dpad_z,
                ft.VerticalDivider(width=1, color=ft.Colors.WHITE10),
                dpad_a,
                ft.Container(expand=True),
                ft.Column(controls=[
                    named_cmd_row,
                    ft.Row(controls=[
                        ft.Button(
                            content=ft.Text("→ К кристаллу"),
                            icon=ft.Icons.NEAR_ME,
                            on_click=_on_goto_crystal,
                            height=34,
                            color=ft.Colors.WHITE,
                            bgcolor=ft.Colors.TEAL_700,
                        ),
                    ]),
                ], spacing=6, tight=True),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            mach_log_text,
        ], spacing=4, tight=True),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border_radius=8,
        bgcolor=ft.Colors.with_opacity(0.6, ft.Colors.BLACK),
        border=ft.Border.all(1, ft.Colors.WHITE10),
        visible=False,
    )

    def on_toggle_machine(e) -> None:
        machine_panel.visible = not machine_panel.visible
        if machine_panel.visible:
            machine_btn_wrap.bgcolor = ft.Colors.with_opacity(0.25, ft.Colors.TEAL_200)
            machine_btn.icon_color = ft.Colors.TEAL_200
        else:
            machine_btn_wrap.bgcolor = ft.Colors.TRANSPARENT
            machine_btn.icon_color = ft.Colors.WHITE70
        page.update()

    machine_btn = ft.IconButton(
        icon=ft.Icons.PRECISION_MANUFACTURING, tooltip="Станок Mach3",
        icon_color=ft.Colors.WHITE70, icon_size=28, visible=False,
        on_click=on_toggle_machine,
    )

    machine_btn_wrap = ft.Container(
        content=machine_btn,
        border_radius=8,
        bgcolor=ft.Colors.TRANSPARENT,
        padding=ft.Padding.all(0),
    )

    # ── helpers ───────────────────────────────────────────────────

    def _current_ui_snapshot() -> dict:
        src_type = "camera" if "camera" in source_toggle.selected else "file"
        cam_idx = int(cam_dd.value) if cam_dd.value else 0
        mods = [m for m in available_mods if mod_checks[m].value]
        return {
            "type": src_type, "cam_idx": cam_idx,
            "file_path": file_path_tf.value or "",
            "loop": bool(loop_cb.value), "mods": mods,
        }

    def _apply_snapshot_to_config(snap: dict) -> None:
        config.source.type = snap["type"]
        config.source.camera.index = snap["cam_idx"]
        config.source.file.path = snap["file_path"]
        config.source.file.loop = snap["loop"]
        config.neural.mods = snap["mods"]

    # ── change tracking ───────────────────────────────────────────

    def _on_any_change() -> None:
        snap = _current_ui_snapshot()
        has_running_diff = (
            engine.running and running_snapshot is not None and snap != running_snapshot
        )
        restart_btn.visible = has_running_diff
        page.update()

    def _on_file_path_change() -> None:
        _on_any_change()
        path = (file_path_tf.value or "").strip()
        if path:
            asyncio.ensure_future(_update_file_info(path))

    # ── stream loop ───────────────────────────────────────────────

    _stream_task: asyncio.Task | None = None

    def _set_playing_ui() -> None:
        start_stop_btn.content = ft.Text("Стоп")
        start_stop_btn.icon = ft.Icons.STOP
        pause_btn.visible = True
        capture_btn.visible = True
        pin_btn.visible = True
        transform_btn.visible = True
        transform_btn_wrap.visible = True
        machine_btn.visible = True
        machine_btn_wrap.visible = True
        zoom_group.visible = True
        zoom_out_btn.visible = True
        zoom_in_btn.visible = True
        _scale_text.visible = True
        reset_size_btn.visible = True
        playback_bar.visible = True

    def _set_stopped_ui() -> None:
        nonlocal _paused
        _paused = False
        start_stop_btn.content = ft.Text("Старт")
        start_stop_btn.icon = ft.Icons.PLAY_ARROW
        status_state.value = "Остановлен"
        status_state.color = ft.Colors.ORANGE_300
        status_fps.value = "FPS: --"
        video_frame.visible = False
        video_scroll_detector.visible = False
        video_placeholder.visible = True
        restart_btn.visible = False
        pause_btn.visible = False
        pause_btn.icon = ft.Icons.PAUSE
        capture_btn.visible = False
        pin_btn.visible = False
        pin_btn.icon_color = ft.Colors.WHITE70
        engine.pin_tid = None
        transform_btn.visible = False
        transform_btn_wrap.visible = False
        transform_btn_wrap.bgcolor = ft.Colors.TRANSPARENT
        transform_btn.icon_color = ft.Colors.WHITE70
        machine_btn.visible = False
        machine_btn_wrap.visible = False
        machine_btn_wrap.bgcolor = ft.Colors.TRANSPARENT
        machine_btn.icon_color = ft.Colors.WHITE70
        machine_panel.visible = False
        zoom_group.visible = False
        zoom_in_btn.visible = False
        zoom_out_btn.visible = False
        _scale_text.visible = False
        reset_size_btn.visible = False
        _reset_video_size()
        transform_panel.visible = False
        playback_bar.visible = False

    async def _stream_loop() -> None:
        log.info(">>> stream_loop")
        frame_count = 0
        _scale_loaded = False
        try:
            while engine.running:
                b64 = await ev_loop.run_in_executor(None, engine.read_and_encode)
                if b64 is None:
                    break
                frame_count += 1
                if not _scale_loaded and engine.last_frame_w:
                    _load_scale_from_config()
                    _scale_loaded = True
                if not _paused:
                    video_image.src = f"data:image/jpeg;base64,{b64}"
                    video_frame.visible = True
                    video_scroll_detector.visible = True
                    video_placeholder.visible = False
                    status_fps.value = f"FPS: {engine.fps:.1f}"
                    if status_state.value not in ("Пауза", "Захват"):
                        status_state.value = "Воспроизведение"
                        status_state.color = ft.Colors.GREEN_300

                    pin_info = engine.last_shared.get("particle_pin")
                    if pin_info and pin_info.get("just_lost"):
                        engine.pin_tid = None
                        pin_btn.icon_color = ft.Colors.WHITE70
                        pin_btn.tooltip = "Закрепить элемент (ПИН)"

                    page.update()
                await asyncio.sleep(1.0 / TARGET_FPS)
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("stream_loop crash")
        log.info("<<< stream_loop frames=%d", frame_count)
        engine.stop()
        _set_stopped_ui()
        page.update()

    async def _do_start() -> None:
        nonlocal _stream_task, running_snapshot
        snap = _current_ui_snapshot()
        _apply_snapshot_to_config(snap)
        running_snapshot = copy.deepcopy(snap)
        src_label = (
            f"Камера {config.source.camera.index}"
            if config.source.type == "camera"
            else Path(config.source.file.path).name
        )
        status_source.value = f"Источник: {src_label}"
        status_mods.value = f"Моды: {len(config.neural.mods)}"
        status_state.value = "Запуск..."
        status_state.color = ft.Colors.YELLOW_300
        page.update()
        try:
            await ev_loop.run_in_executor(None, engine.start, config)
        except Exception:
            log.exception("engine.start() failed")
            _set_stopped_ui()
            page.update()
            return
        _set_playing_ui()
        restart_btn.visible = False
        page.update()
        await _mach_auto_connect()
        _stream_task = asyncio.ensure_future(_stream_loop())

    async def _do_stop() -> None:
        nonlocal _stream_task, running_snapshot
        engine.stop()
        if _stream_task and not _stream_task.done():
            _stream_task.cancel()
        _stream_task = None
        running_snapshot = None
        _set_stopped_ui()
        page.update()

    # ── save dialog ───────────────────────────────────────────────

    _start_future: asyncio.Future | None = None

    async def _show_save_dialog() -> str:
        nonlocal _start_future
        _start_future = ev_loop.create_future()

        def _answer(ans: str):
            def handler(e):
                if _start_future and not _start_future.done():
                    _start_future.set_result(ans)
            return handler

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Сохранить изменения?"),
            content=ft.Text("Параметры отличаются от сохранённых в конфиге."),
            actions=[
                ft.Button(content=ft.Text("Сохранить и запустить"), on_click=_answer("save")),
                ft.Button(content=ft.Text("Запустить без сохранения"), on_click=_answer("nosave")),
                ft.OutlinedButton(content=ft.Text("Отмена"), on_click=_answer("cancel")),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dlg)
        page.update()
        result = await _start_future
        page.pop_dialog()
        page.update()
        return result

    async def on_start_stop(e) -> None:
        nonlocal saved_snapshot
        if engine.running:
            await _do_stop()
            return
        snap = _current_ui_snapshot()
        if snap != saved_snapshot:
            answer = await _show_save_dialog()
            if answer == "cancel":
                return
            if answer == "save":
                _apply_snapshot_to_config(snap)
                try:
                    service.save(config)
                    saved_snapshot = copy.deepcopy(snap)
                    log.info("Config saved before start")
                except Exception:
                    log.exception("Config save failed")
        await _do_start()

    async def on_restart(e) -> None:
        await _do_stop()
        await _do_start()

    # ── control buttons ───────────────────────────────────────────

    start_stop_btn = ft.Button(
        content=ft.Text("Старт"), icon=ft.Icons.PLAY_ARROW,
        on_click=on_start_stop, height=38,
        color=ft.Colors.WHITE, bgcolor=ft.Colors.BLUE_GREY_700,
    )
    restart_btn = ft.Button(
        content=ft.Text("Перезапустить"), icon=ft.Icons.RESTART_ALT,
        on_click=on_restart, height=36,
        color=ft.Colors.WHITE, bgcolor=ft.Colors.ORANGE_900, visible=False,
    )

    def _section_card(icon, title: str, controls: list) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(controls=[
                        ft.Icon(icon, size=18, color=ft.Colors.BLUE_200),
                        ft.Text(title, size=13, weight=ft.FontWeight.W_600),
                    ], spacing=8),
                    ft.Divider(height=6, color=ft.Colors.WHITE10),
                    *controls,
                ],
                spacing=6,
            ),
            padding=ft.Padding.all(12),
            border_radius=10,
            bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.WHITE),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.08, ft.Colors.WHITE)),
        )

    # ── layout ────────────────────────────────────────────────────

    _crystal_mm = _raw_boot.get("app", {}).get("crystal_size_mm", 0.3)

    def on_calibrate(e) -> None:
        particles = engine.last_shared.get("particle_centers")
        if not engine.running:
            hint = (
                "Для калибровки нужен видеопоток.\n\n"
                "1. Выберите источник видео (камера или файл)\n"
                "2. Нажмите «Старт»\n"
                "3. Дождитесь, пока кристаллы появятся в кадре\n"
                "4. Нажмите эту кнопку ещё раз"
            )
        elif not particles or len(particles) < 2:
            hint = (
                "В кадре слишком мало элементов (нужно минимум 2).\n\n"
                "Убедитесь, что кристаллы видны в кадре\n"
                "и активирован мод «__particle_centers»."
            )
        else:
            hint = None

        if hint is not None:
            dlg = ft.AlertDialog(
                title=ft.Row(controls=[
                    ft.Icon(ft.Icons.INFO_OUTLINE, color=ft.Colors.BLUE_200, size=20),
                    ft.Text("Калибровка масштаба", weight=ft.FontWeight.BOLD),
                ], spacing=8),
                content=ft.Text(hint, size=13),
                actions=[ft.Button(content=ft.Text("Понятно"),
                                   on_click=lambda _: (page.pop_dialog(), page.update()))],
                actions_alignment=ft.MainAxisAlignment.END,
            )
            page.show_dialog(dlg)
            page.update()
            return

        sizes_px = []
        for p in particles:
            s = max(p.get("w", 0), p.get("h", 0))
            if s > 0:
                sizes_px.append(s)

        if not sizes_px:
            return

        avg_px = sum(sizes_px) / len(sizes_px)
        median_px = sorted(sizes_px)[len(sizes_px) // 2]
        ref_px = (avg_px + median_px) / 2.0
        new_scale = round(_crystal_mm / ref_px, 6)

        old_sx = _raw_cfg.get("particle_grid", {}).get("scale_x", 0.1)
        old_sy = _raw_cfg.get("particle_grid", {}).get("scale_y", 0.1)

        calib_result = ft.Text("", size=12, selectable=True)

        async def _apply_calib(_) -> None:
            _raw_cfg.setdefault("particle_grid", {})["scale_x"] = new_scale
            _raw_cfg["particle_grid"]["scale_y"] = new_scale
            try:
                with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
                    json.dump(_raw_cfg, fp, ensure_ascii=False, indent=2)
                    fp.write("\n")
                log.info("Calibration saved: scale=%.6f", new_scale)
            except Exception as ex:
                calib_result.value = f"Ошибка: {ex}"
                calib_result.color = ft.Colors.RED_300
                page.update()
                return

            page.pop_dialog()
            page.update()

            if engine.running:
                await _do_stop()
                await _do_start()

        info_col = ft.Column(controls=[
            ft.Row(controls=[
                ft.Icon(ft.Icons.STRAIGHTEN, size=14, color=ft.Colors.WHITE54),
                ft.Text(f"Элементов в кадре: {len(sizes_px)}", size=12),
            ], spacing=6),
            ft.Row(controls=[
                ft.Icon(ft.Icons.PHOTO_SIZE_SELECT_LARGE, size=14, color=ft.Colors.WHITE54),
                ft.Text(f"Средний размер: {avg_px:.1f} px  |  Медиана: {median_px:.1f} px", size=12),
            ], spacing=6),
            ft.Row(controls=[
                ft.Icon(ft.Icons.MEMORY, size=14, color=ft.Colors.WHITE54),
                ft.Text(f"Эталон кристалла: {_crystal_mm} мм", size=12),
            ], spacing=6),
            ft.Divider(height=8, color=ft.Colors.WHITE10),
            ft.Row(controls=[
                ft.Icon(ft.Icons.TUNE, size=14, color=ft.Colors.BLUE_200),
                ft.Text(f"Текущий масштаб: {old_sx}", size=12, color=ft.Colors.WHITE54),
            ], spacing=6),
            ft.Row(controls=[
                ft.Icon(ft.Icons.ARROW_FORWARD, size=14, color=ft.Colors.GREEN_300),
                ft.Text(f"Новый масштаб: {new_scale} мм/px", size=12,
                         weight=ft.FontWeight.W_600, color=ft.Colors.GREEN_300),
            ], spacing=6),
            ft.Container(height=4),
            calib_result,
        ], spacing=4, tight=True, width=380)

        dlg = ft.AlertDialog(
            title=ft.Row(controls=[
                ft.Icon(ft.Icons.SQUARE_FOOT, color=ft.Colors.BLUE_200, size=20),
                ft.Text("Калибровка масштаба", weight=ft.FontWeight.BOLD),
            ], spacing=8),
            content=info_col,
            actions=[
                ft.Button(content=ft.Text("Применить и сохранить"), icon=ft.Icons.CHECK,
                          on_click=_apply_calib),
                ft.OutlinedButton(content=ft.Text("Отмена"),
                                  on_click=lambda _: (page.pop_dialog(), page.update())),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dlg)
        page.update()

    calibrate_btn = ft.IconButton(
        icon=ft.Icons.SQUARE_FOOT, tooltip="Калибровка масштаба",
        icon_color=ft.Colors.WHITE70, icon_size=22,
        on_click=on_calibrate,
    )

    _cur_window_mode = _raw_boot.get("app", {}).get("window_mode", "normal")
    _cur_crystal_mm = _raw_boot.get("app", {}).get("crystal_size_mm", 0.3)

    def on_settings(e) -> None:
        nonlocal _crystal_mm

        fs_switch = ft.Switch(
            label="Полноэкранный режим",
            value=(_cur_window_mode == "fullscreen"),
            label_text_style=ft.TextStyle(size=13),
        )

        crystal_field = ft.TextField(
            value=str(_cur_crystal_mm),
            label="Размер кристалла (мм)",
            width=200, text_size=13, dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
            suffix=ft.Text("мм", size=12, color=ft.Colors.WHITE38),
        )

        _mv_cfg = {}
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as _mf:
                _mv_cfg = json.load(_mf).get("movement", {})
        except Exception:
            pass
        mach_ip_field = ft.TextField(
            value=_mv_cfg.get("mach3_ip", "192.168.1.125"),
            label="IP сервера", text_size=13, dense=True, expand=2,
        )
        mach_port_field = ft.TextField(
            value=str(_mv_cfg.get("mach3_port", 5555)),
            label="Порт", text_size=13, dense=True, expand=1,
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        vac_x_field = ft.TextField(
            value=str(_mv_cfg.get("vacuum_offset_x", -44.0)),
            label="X", text_size=13, dense=True, expand=1,
            keyboard_type=ft.KeyboardType.NUMBER,
            suffix=ft.Text("мм", size=11, color=ft.Colors.WHITE38),
        )
        vac_y_field = ft.TextField(
            value=str(_mv_cfg.get("vacuum_offset_y", -6.0)),
            label="Y", text_size=13, dense=True, expand=1,
            keyboard_type=ft.KeyboardType.NUMBER,
            suffix=ft.Text("мм", size=11, color=ft.Colors.WHITE38),
        )
        feed_field = ft.TextField(
            value=str(_mv_cfg.get("feed_speed", 700)),
            label="Подача", text_size=13, dense=True, expand=1,
            keyboard_type=ft.KeyboardType.NUMBER,
            suffix=ft.Text("мм/м", size=11, color=ft.Colors.WHITE38),
        )

        settings_result = ft.Text("", size=12, selectable=True)

        dlg_mod_switches: dict[str, ft.Switch] = {}
        for mn in available_mods:
            meta = MOD_META.get(mn, {})
            dlg_mod_switches[mn] = ft.Switch(
                value=mod_checks[mn].value,
                label=meta.get("title", mn.lstrip("_")),
                label_text_style=ft.TextStyle(size=12),
            )
            if meta.get("required"):
                dlg_mod_switches[mn].disabled = True

        def _enforce_deps(changed_name: str) -> None:
            changed_val = dlg_mod_switches[changed_name].value
            if changed_val:
                for dep in MOD_META.get(changed_name, {}).get("deps", []):
                    if dep in dlg_mod_switches:
                        dlg_mod_switches[dep].value = True
            else:
                for mn2, meta2 in MOD_META.items():
                    if changed_name in meta2.get("deps", []) and dlg_mod_switches.get(mn2, None):
                        dlg_mod_switches[mn2].value = False
            page.update()

        for mn in available_mods:
            _mn = mn
            dlg_mod_switches[mn].on_change = lambda e, m=_mn: _enforce_deps(m)

        def _build_mod_row(mn: str) -> ft.Container:
            meta = MOD_META.get(mn, {})
            dep_names = [MOD_META.get(d, {}).get("title", d.lstrip("_")) for d in meta.get("deps", [])]
            dep_text = f"Зависит от: {', '.join(dep_names)}" if dep_names else ""
            badges = []
            if meta.get("required"):
                badges.append(ft.Container(
                    content=ft.Text("ОБЯЗАТЕЛЬНЫЙ", size=9, color=ft.Colors.WHITE,
                                    weight=ft.FontWeight.BOLD),
                    bgcolor=ft.Colors.RED_700, border_radius=4,
                    padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                ))
            info_parts = [ft.Text(meta.get("desc", ""), size=11, color=ft.Colors.WHITE54)]
            if dep_text:
                info_parts.append(ft.Text(dep_text, size=10, color=ft.Colors.BLUE_200, italic=True))
            return ft.Container(
                border_radius=6,
                bgcolor=ft.Colors.with_opacity(0.04, ft.Colors.WHITE),
                padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                content=ft.Column(controls=[
                    ft.Row(controls=[dlg_mod_switches[mn], *badges],
                           spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Container(
                        padding=ft.Padding.only(left=48),
                        content=ft.Column(controls=info_parts, spacing=2, tight=True),
                    ),
                ], spacing=2, tight=True),
            )

        mod_rows = [_build_mod_row(mn) for mn in available_mods]

        is_cam_now = "camera" in source_toggle.selected
        cam_panel.visible = is_cam_now
        file_panel.visible = not is_cam_now

        def _save_settings(_) -> None:
            nonlocal _crystal_mm, _cur_window_mode, _cur_crystal_mm
            try:
                new_crystal = float(crystal_field.value.replace(",", "."))
                if new_crystal <= 0:
                    raise ValueError
            except (ValueError, TypeError):
                settings_result.value = "Некорректный размер кристалла"
                settings_result.color = ft.Colors.RED_300
                page.update()
                return

            new_wm = "fullscreen" if fs_switch.value else "normal"

            for mn, sw in dlg_mod_switches.items():
                mod_checks[mn].value = sw.value

            try:
                raw = {}
                with open(CONFIG_PATH, "r", encoding="utf-8") as fp:
                    raw = json.load(fp)
                raw.setdefault("app", {})["window_mode"] = new_wm
                raw["app"]["crystal_size_mm"] = new_crystal
                new_mods = [m for m in available_mods if mod_checks[m].value]
                raw.setdefault("neural", {})["mods"] = new_mods
                mv_sec = raw.setdefault("movement", {})
                mv_sec["mach3_ip"] = mach_ip_field.value.strip()
                try:
                    mv_sec["mach3_port"] = int(mach_port_field.value.strip())
                except (ValueError, TypeError):
                    mv_sec["mach3_port"] = 5555
                try:
                    mv_sec["vacuum_offset_x"] = float(vac_x_field.value.replace(",", "."))
                except (ValueError, TypeError):
                    pass
                try:
                    mv_sec["vacuum_offset_y"] = float(vac_y_field.value.replace(",", "."))
                except (ValueError, TypeError):
                    pass
                try:
                    mv_sec["feed_speed"] = int(feed_field.value.strip())
                except (ValueError, TypeError):
                    pass
                with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
                    json.dump(raw, fp, ensure_ascii=False, indent=2)
                    fp.write("\n")
            except Exception as ex:
                settings_result.value = f"Ошибка сохранения: {ex}"
                settings_result.color = ft.Colors.RED_300
                page.update()
                return

            _cur_window_mode = new_wm
            _cur_crystal_mm = new_crystal
            _crystal_mm = new_crystal
            page.window.full_screen = (new_wm == "fullscreen")

            _mach3_cfg.update(mv_sec)
            engine.mach3.configure(
                mv_sec.get("mach3_ip", "192.168.1.125"),
                int(mv_sec.get("mach3_port", 5555)),
            )

            _on_any_change()

            page.pop_dialog()
            snack = ft.SnackBar(
                ft.Text("Настройки сохранены", color=ft.Colors.WHITE),
                bgcolor=ft.Colors.GREEN_800, duration=2000,
            )
            page.overlay.append(snack)
            snack.open = True
            page.update()

        content_col = ft.Column(controls=[
            ft.Container(
                border_radius=8,
                bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.WHITE),
                padding=ft.Padding.all(14),
                content=ft.Column(controls=[
                    ft.Row(controls=[
                        ft.Icon(ft.Icons.CONNECTED_TV, size=18, color=ft.Colors.BLUE_200),
                        ft.Text("Источник видео", size=13, weight=ft.FontWeight.W_600),
                    ], spacing=8),
                    ft.Container(height=4),
                    source_toggle,
                    ft.Container(height=6),
                    cam_panel,
                    file_panel,
                ], spacing=4, tight=True),
            ),
            ft.Container(height=8),
            ft.Container(
                border_radius=8,
                bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.WHITE),
                padding=ft.Padding.all(14),
                content=ft.Column(controls=[
                    ft.Row(controls=[
                        ft.Icon(ft.Icons.FULLSCREEN, size=18, color=ft.Colors.BLUE_200),
                        ft.Text("Режим окна", size=13, weight=ft.FontWeight.W_600),
                    ], spacing=8),
                    ft.Container(height=4),
                    fs_switch,
                ], spacing=2, tight=True),
            ),
            ft.Container(height=8),
            ft.Container(
                border_radius=8,
                bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.WHITE),
                padding=ft.Padding.all(14),
                content=ft.Column(controls=[
                    ft.Row(controls=[
                        ft.Icon(ft.Icons.MEMORY, size=18, color=ft.Colors.BLUE_200),
                        ft.Text("Эталон кристалла", size=13, weight=ft.FontWeight.W_600),
                    ], spacing=8),
                    ft.Container(height=4),
                    crystal_field,
                    ft.Text(
                        "Используется при калибровке масштаба.",
                        size=11, color=ft.Colors.WHITE38, italic=True,
                    ),
                ], spacing=2, tight=True),
            ),
            ft.Container(height=8),
            ft.Container(
                border_radius=8,
                bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.WHITE),
                padding=ft.Padding.all(14),
                content=ft.Column(controls=[
                    ft.Row(controls=[
                        ft.Icon(ft.Icons.PRECISION_MANUFACTURING, size=18, color=ft.Colors.BLUE_200),
                        ft.Text("Станок Mach3", size=13, weight=ft.FontWeight.W_600),
                    ], spacing=8),
                    ft.Container(height=6),
                    ft.Text("Подключение", size=11, color=ft.Colors.WHITE54),
                    ft.Row(controls=[mach_ip_field, mach_port_field], spacing=8),
                    ft.Container(height=6),
                    ft.Text("Смещение вакуумной трубки от камеры", size=11, color=ft.Colors.WHITE54),
                    ft.Row(controls=[vac_x_field, vac_y_field], spacing=8),
                    ft.Container(height=6),
                    ft.Text("Скорость движения", size=11, color=ft.Colors.WHITE54),
                    ft.Row(controls=[feed_field], spacing=8),
                ], spacing=2, tight=True),
            ),
            ft.Container(height=8),
            ft.Container(
                border_radius=8,
                bgcolor=ft.Colors.with_opacity(0.05, ft.Colors.WHITE),
                padding=ft.Padding.all(14),
                content=ft.Column(controls=[
                    ft.Row(controls=[
                        ft.Icon(ft.Icons.AUTO_FIX_HIGH, size=18, color=ft.Colors.BLUE_200),
                        ft.Text("Нейро-моды", size=13, weight=ft.FontWeight.W_600),
                    ], spacing=8),
                    ft.Divider(height=6, color=ft.Colors.WHITE10),
                    *mod_rows,
                ], spacing=6, tight=True),
            ),
            ft.Container(height=4),
            settings_result,
        ], spacing=0, tight=True, width=460, scroll=ft.ScrollMode.AUTO, height=580)

        dlg = ft.AlertDialog(
            title=ft.Row(controls=[
                ft.Icon(ft.Icons.SETTINGS, color=ft.Colors.BLUE_200, size=20),
                ft.Text("Настройки приложения", weight=ft.FontWeight.BOLD),
            ], spacing=8),
            content=content_col,
            actions=[
                ft.Button(content=ft.Text("Сохранить"), icon=ft.Icons.CHECK,
                          on_click=_save_settings),
                ft.OutlinedButton(content=ft.Text("Отмена"),
                                  on_click=lambda _: (page.pop_dialog(), page.update())),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dlg)
        page.update()

    settings_btn = ft.IconButton(
        icon=ft.Icons.SETTINGS, tooltip="Настройки приложения",
        icon_color=ft.Colors.WHITE70, icon_size=22,
        on_click=on_settings,
    )

    appbar = ft.Container(
        height=56, bgcolor=ft.Colors.BLUE_GREY_900,
        padding=ft.Padding.symmetric(horizontal=20, vertical=8),
        content=ft.Row(
            controls=[
                ft.Image(src="logo.png", fit=ft.BoxFit.CONTAIN, height=40),
                ft.Container(expand=True),
                ft.Container(
                    content=start_stop_btn,
                    width=130, height=38,
                ),
                ft.Container(width=8),
                mach_appbar_icon,
                ft.Container(width=4),
                calibrate_btn,
                settings_btn,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    _raw_cfg = {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as _fp:
            _raw_cfg = json.load(_fp)
    except Exception:
        pass
    _app_version = _raw_cfg.get("app", {}).get("version", "")

    def _show_hotkeys(e=None) -> None:
        rows = []
        for label, key in HOTKEYS:
            if not label and not key:
                rows.append(ft.Divider(height=8, color=ft.Colors.WHITE10))
                continue
            rows.append(
                ft.Row(controls=[
                    ft.Text(label, size=13, width=200, color=ft.Colors.WHITE70),
                    ft.Container(
                        content=ft.Text(key, size=12, weight=ft.FontWeight.W_600),
                        padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                        border_radius=4,
                        bgcolor=ft.Colors.with_opacity(0.1, ft.Colors.WHITE),
                        border=ft.Border.all(1, ft.Colors.WHITE24),
                    ),
                ], spacing=12, alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
            )
        dlg = ft.AlertDialog(
            title=ft.Row(controls=[
                ft.Icon(ft.Icons.KEYBOARD, color=ft.Colors.BLUE_200, size=20),
                ft.Text("Горячие клавиши", weight=ft.FontWeight.BOLD),
            ], spacing=8),
            content=ft.Column(controls=rows, spacing=8, tight=True, width=340),
            actions=[ft.Button(content=ft.Text("Закрыть"), on_click=lambda _: (page.pop_dialog(), page.update()))],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dlg)
        page.update()

    hotkey_btn = ft.IconButton(
        icon=ft.Icons.HELP_OUTLINE, tooltip="Горячие клавиши (?)",
        icon_color=ft.Colors.WHITE38, icon_size=18,
        on_click=_show_hotkeys,
    )

    version_label = ft.Text(
        _app_version, size=11, color=ft.Colors.WHITE38,
        italic=True, weight=ft.FontWeight.W_500,
    )

    statusbar = ft.Container(
        height=32, bgcolor=ft.Colors.with_opacity(0.3, ft.Colors.BLACK),
        padding=ft.Padding.symmetric(horizontal=20),
        content=ft.Row(
            controls=[
                status_state,
                ft.VerticalDivider(width=1, color=ft.Colors.WHITE10),
                status_fps,
                ft.VerticalDivider(width=1, color=ft.Colors.WHITE10),
                status_source,
                ft.VerticalDivider(width=1, color=ft.Colors.WHITE10),
                status_mods,
                ft.Container(expand=True),
                hotkey_btn,
                ft.VerticalDivider(width=1, color=ft.Colors.WHITE10),
                version_label,
            ],
            spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    video_area = ft.Container(
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.4, ft.Colors.BLACK),
        padding=ft.Padding.all(16),
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            controls=[video_stack],
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        ),
    )

    playback_bar = ft.Container(
        height=48,
        bgcolor=ft.Colors.with_opacity(0.5, ft.Colors.BLACK),
        border=ft.Border.only(top=ft.BorderSide(1, ft.Colors.WHITE10)),
        padding=ft.Padding.symmetric(horizontal=16),
        visible=False,
        content=ft.Row(
            controls=[
                pause_btn,
                ft.VerticalDivider(width=1, color=ft.Colors.WHITE10),
                capture_btn,
                ft.VerticalDivider(width=1, color=ft.Colors.WHITE10),
                pin_btn,
                ft.Container(expand=True),
                restart_btn,
                ft.VerticalDivider(width=1, color=ft.Colors.WHITE10),
                zoom_group,
                ft.VerticalDivider(width=1, color=ft.Colors.WHITE10),
                machine_btn_wrap,
                ft.VerticalDivider(width=1, color=ft.Colors.WHITE10),
                transform_btn_wrap,
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    video_column = ft.Column(
        controls=[video_area, transform_panel, machine_panel, playback_bar],
        spacing=0, expand=True,
    )

    body = video_column

    page.add(ft.Column(controls=[appbar, body, statusbar], spacing=0, expand=True))

    if config.source.type == "camera" and config.source.camera.index >= 0:
        cam_dd.options.append(
            ft.dropdown.Option(str(config.source.camera.index),
                               f"Камера {config.source.camera.index}")
        )
        cam_dd.value = str(config.source.camera.index)

    if config.source.file.path:
        asyncio.ensure_future(_update_file_info(config.source.file.path))

    async def _on_keyboard(e: ft.KeyboardEvent) -> None:
        k = e.key
        ctrl = e.ctrl or e.meta

        if k == "?" or (k == "/" and e.shift):
            _show_hotkeys()
            return

        if ctrl and k == "Enter":
            await on_start_stop(e)
            return

        if k == " ":
            if engine.running:
                on_pause(e)
            return

        if ctrl and e.shift and k.upper() == "C":
            if engine.running:
                await asyncio.sleep(0.05)
                on_capture(e)
            return

        if ctrl and e.shift and k.upper() == "P":
            if engine.running:
                on_pin(e)
            return

        if ctrl and e.shift and k.upper() == "T":
            if engine.running:
                on_toggle_transform(e)
            return

        if ctrl and e.shift and k.upper() == "X":
            if engine.running:
                await on_restart(e)
            return

        if ctrl and e.shift and k.upper() == "M":
            if engine.running:
                on_toggle_machine(e)
            return

        if machine_panel.visible and _mach_connected[0]:
            if k == "Arrow Left":
                await _mach_jog("x", -_jog_step[0])
                return
            if k == "Arrow Right":
                await _mach_jog("x", _jog_step[0])
                return
            if k == "Arrow Up":
                await _mach_jog("y", _jog_step[0])
                return
            if k == "Arrow Down":
                await _mach_jog("y", -_jog_step[0])
                return
            if k == "Page Up":
                await _mach_jog("z", _jog_step[0])
                return
            if k == "Page Down":
                await _mach_jog("z", -_jog_step[0])
                return
            if k == "Home":
                await _mach_jog("a", -_jog_step[0])
                return
            if k == "End":
                await _mach_jog("a", _jog_step[0])
                return

    page.on_keyboard_event = _on_keyboard

    async def on_disconnect(e) -> None:
        engine.stop()

    page.on_disconnect = on_disconnect
    log.info("=== UI ready ===")


def _ui_snapshot_from_config(config, available_mods: list[str]) -> dict:
    return {
        "type": config.source.type,
        "cam_idx": config.source.camera.index,
        "file_path": config.source.file.path,
        "loop": config.source.file.loop,
        "mods": list(config.neural.mods),
    }


if __name__ == "__main__":
    ft.run(main)
