from __future__ import annotations

import logging
import socket
import sys
import time
from pathlib import Path

_lib_dir = str(Path(__file__).resolve().parent / "moch3_lib")
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from mach3_common import ColbaConfig, Connection, MyData  # noqa: E402

log = logging.getLogger("mach3bridge")

_DEFAULT_IP = "192.168.1.125"
_DEFAULT_PORT = 5555
_PROBE_TIMEOUT_S = 2.0
_GLOBAL_SOCKET_TIMEOUT_S = 5.0

NAMED_COMMANDS: tuple[str, ...] = (
    "tozero",
    "toworkzone",
    "fullcollibration",
    "allzero",
    "zerotocamera",
    "zerotozond",
    "sendcristal",
)

socket.setdefaulttimeout(_GLOBAL_SOCKET_TIMEOUT_S)


class Mach3Bridge:

    def __init__(self, ip: str = _DEFAULT_IP, port: int = _DEFAULT_PORT) -> None:
        self._ip = ip
        self._port = port
        self._connected = False
        self._last_x = 0.0
        self._last_y = 0.0
        self._last_z = 0.0
        self._last_a = 0.0
        self._data = MyData(ip=ip, port=port)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def position(self) -> dict[str, float]:
        return {
            "x": self._last_x,
            "y": self._last_y,
            "z": self._last_z,
            "a": self._last_a,
        }

    @property
    def colba(self) -> dict[str, float | None]:
        c = self._data.motion.colba_information
        return {"x": c.x, "y": c.y, "r": c.r, "h": c.h}

    @property
    def grid(self) -> dict:
        g = self._data.grid
        return {
            "rows": g.rows,
            "cols": g.cols,
            "row_spacing": g.row_spacing,
            "col_spacing": g.col_spacing,
            "current_row": g.current_row,
            "current_col": g.current_col,
            "finished": g.finished,
        }

    def grid_coords(self) -> tuple[float, float]:
        return self._data.grid.get_current_coordinates()

    def configure(self, ip: str, port: int) -> None:
        self._ip = ip
        self._port = port
        self._data.motion.set_connection(Connection(ip, port))

    def set_colba(self, x: float, y: float, r: float, h: float) -> None:
        try:
            self._data.motion.colba_information = ColbaConfig(
                x=float(x), y=float(y), r=float(r), h=float(h)
            )
        except (TypeError, ValueError) as exc:
            log.warning("set_colba failed: %s", exc)

    def set_grid(self, rows: int, cols: int,
                 row_spacing: float, col_spacing: float) -> None:
        try:
            g = self._data.grid
            g.reset()
            g.rows = int(rows)
            g.cols = int(cols)
            g.row_spacing = float(row_spacing)
            g.col_spacing = float(col_spacing)
        except (TypeError, ValueError) as exc:
            log.warning("set_grid failed: %s", exc)

    def grid_set_position(self, row: int, col: int) -> str:
        try:
            self._data.grid.current_row = int(row)
            self._data.grid.current_col = int(col)
            return "OK"
        except (TypeError, ValueError) as exc:
            return f"ОШИБКА: {exc}"

    def grid_next(self) -> bool:
        try:
            return bool(self._data.grid.next())
        except Exception:
            log.exception("grid_next failed")
            return False

    def grid_reset(self, row: int = 0, col: int = 0) -> None:
        try:
            self._data.grid.reset(int(row), int(col))
        except Exception:
            log.exception("grid_reset failed")

    def is_in_working_zone(self) -> bool:
        if not self._connected:
            return False
        try:
            return bool(self._data.motion.is_in_working_zone())
        except Exception:
            log.exception("is_in_working_zone failed")
            return False

    def is_in_working_zone_local(self) -> bool | None:
        c = self._data.motion.colba_information
        if c is None or c.h is None or c.r is None or c.x is None or c.y is None:
            return None
        if self._last_z >= c.h - 35:
            return False
        dx = self._last_x - c.x
        dy = self._last_y - c.y
        return (dx * dx + dy * dy) <= (c.r * c.r)

    def probe(self, timeout: float = _PROBE_TIMEOUT_S) -> bool:
        try:
            with socket.create_connection((self._ip, self._port), timeout=timeout):
                return True
        except OSError as exc:
            log.info("Mach3 probe %s:%d failed: %s", self._ip, self._port, exc)
            return False

    def connect(self) -> bool:
        if not self.probe():
            self._connected = False
            return False
        try:
            pos = self.get_position()
            self._connected = pos is not None
            if self._connected:
                log.info("Mach3 connected: %s:%d pos=%s", self._ip, self._port, pos)
            return self._connected
        except Exception:
            log.exception("Mach3 connect failed")
            self._connected = False
            return False

    def disconnect(self) -> None:
        self._connected = False
        log.info("Mach3 disconnected")

    def get_position(self) -> dict[str, float] | None:
        try:
            resp = self._data.motion.get_DRO()
        except Exception:
            log.exception("get_position failed")
            return None
        if resp is None:
            return None
        if self._data.motion.X is not None:
            self._last_x = float(self._data.motion.X)
        if self._data.motion.Y is not None:
            self._last_y = float(self._data.motion.Y)
        if self._data.motion.Z is not None:
            self._last_z = float(self._data.motion.Z)
        if self._data.motion.A is not None:
            self._last_a = float(self._data.motion.A)
        return self.position

    def move_to(self, x: float | None = None, y: float | None = None,
                z: float | None = None, a: float | None = None,
                feed: int = 700) -> str:
        try:
            self._data.motion.clear()
            if x is not None:
                self._data.motion.set_x(float(x))
            if y is not None:
                self._data.motion.set_y(float(y))
            if z is not None:
                self._data.motion.set_z(float(z))
            if a is not None:
                self._data.motion.set_a(float(a))
            if feed:
                self._data.motion.set_feed(int(feed))
            resp = self._data.motion.send()
            self._refresh_after_move()
            return resp or ""
        except ValueError as exc:
            log.warning("move_to rejected: %s", exc)
            return f"ОШИБКА: {exc}"
        except Exception as exc:
            log.exception("move_to failed")
            return f"ОШИБКА: {exc}"

    def move_relative(self, axis: str, delta: float, feed: int = 700) -> str:
        ax = axis.lower()
        if ax not in ("x", "y", "z", "a"):
            return f"ОШИБКА: неизвестная ось {axis}"
        try:
            self._data.motion.move_on(ax, float(delta))
            self._refresh_after_move()
            return f"OK: {ax}{delta:+.3f}"
        except ValueError as exc:
            log.warning("move_relative rejected: %s", exc)
            return f"ОШИБКА: {exc}"
        except Exception as exc:
            log.exception("move_relative failed")
            return f"ОШИБКА: {exc}"

    def send_raw(self, gcode: str) -> str:
        try:
            self._data.motion.clear()
            self._data.motion.set_command(gcode)
            resp = self._data.motion.send()
            self._refresh_after_move()
            return resp or ""
        except Exception as exc:
            log.exception("send_raw failed")
            return f"ОШИБКА: {exc}"

    def send_named_command(self, name: str) -> str:
        if name not in NAMED_COMMANDS:
            return f"ОШИБКА: неизвестная команда {name}"
        try:
            self._data.motion.send_command(name)
            time.sleep(0.3)
            self._refresh_after_move()
            return f"OK: {name}"
        except Exception as exc:
            log.exception("send_named_command failed")
            return f"ОШИБКА: {exc}"

    def move_to_crystal(self, world_x: float, world_y: float,
                        vacuum_offset_x: float, vacuum_offset_y: float,
                        feed: int = 700) -> str:
        delta_x = world_x + vacuum_offset_x
        delta_y = world_y + vacuum_offset_y
        log.info(
            "move_to_crystal: world=(%.2f,%.2f) + vac=(%.1f,%.1f) -> dx=%.2f dy=%.2f",
            world_x, world_y, vacuum_offset_x, vacuum_offset_y, delta_x, delta_y,
        )
        try:
            self._data.motion.move_on("x", float(delta_x))
            self._data.motion.move_on("y", float(delta_y))
            self._refresh_after_move()
            return f"OK: X{delta_x:+.3f} Y{delta_y:+.3f}"
        except ValueError as exc:
            log.warning("move_to_crystal rejected: %s", exc)
            return f"ОШИБКА: {exc}"
        except Exception as exc:
            log.exception("move_to_crystal failed")
            return f"ОШИБКА: {exc}"

    def move_to_grid(self, feed: int = 700) -> str:
        try:
            gx, gy = self._data.grid.get_current_coordinates()
        except Exception as exc:
            log.exception("move_to_grid coords failed")
            return f"ОШИБКА: {exc}"
        return self.move_to(x=float(gx), y=float(gy), feed=feed)

    def step_grid(self, feed: int = 700) -> str:
        if not self.grid_next():
            return "Сетка пройдена"
        return self.move_to_grid(feed=feed)

    def _refresh_after_move(self) -> None:
        try:
            self.get_position()
        except Exception:
            pass
