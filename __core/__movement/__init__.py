from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

_lib_dir = str(Path(__file__).resolve().parent / "moch3_lib")
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from mach3_common import MyData, send_command_string  # noqa: E402

log = logging.getLogger("mach3bridge")


class Mach3Bridge:

    def __init__(self, ip: str = "192.168.1.125", port: int = 5555):
        self._ip = ip
        self._port = port
        self._connected = False
        self._last_x: float = 0.0
        self._last_y: float = 0.0
        self._last_z: float = 0.0
        self._last_a: float = 0.0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def position(self) -> dict[str, float]:
        return {
            "x": self._last_x, "y": self._last_y,
            "z": self._last_z, "a": self._last_a,
        }

    def configure(self, ip: str, port: int) -> None:
        self._ip = ip
        self._port = port

    def connect(self) -> bool:
        try:
            pos = self.get_position()
            if pos is not None:
                self._connected = True
                log.info("Mach3 connected: %s:%d  pos=%s", self._ip, self._port, pos)
                return True
        except Exception:
            log.exception("Mach3 connect failed")
        self._connected = False
        return False

    def disconnect(self) -> None:
        self._connected = False
        log.info("Mach3 disconnected")

    def get_position(self) -> dict[str, float] | None:
        data = MyData(ip=self._ip, port=self._port)
        data.motion.set_command("GET_DRO")
        resp = send_command_string(data)
        if not resp or resp.startswith("ОШИБКА") or resp == "NO_DATA":
            return None
        try:
            resp = resp.strip()
            if "," in resp:
                parts = resp.split(",")
                self._last_x = float(parts[0].strip())
                self._last_y = float(parts[1].strip())
                self._last_z = float(parts[2].strip())
                self._last_a = float(parts[3].strip())
            else:
                for tok in resp.split():
                    if len(tok) > 1 and tok[0] in "XYZA":
                        val = float(tok[1:])
                        if tok[0] == "X": self._last_x = val
                        elif tok[0] == "Y": self._last_y = val
                        elif tok[0] == "Z": self._last_z = val
                        elif tok[0] == "A": self._last_a = val
            return self.position
        except Exception:
            log.exception("Failed to parse DRO: %s", resp)
            return None

    def move_to(self, x: float | None = None, y: float | None = None,
                z: float | None = None, a: float | None = None,
                feed: int = 700) -> str:
        data = MyData(ip=self._ip, port=self._port)
        if x is not None:
            data.motion.set_x(x)
        if y is not None:
            data.motion.set_y(y)
        if z is not None:
            data.motion.set_z(z)
        if a is not None:
            data.motion.set_a(a)
        data.motion.set_feed(feed)
        resp = send_command_string(data)
        log.info("move_to resp: %s", resp)
        self._refresh_after_move()
        return resp or ""

    def move_relative(self, axis: str, delta: float, feed: int = 700) -> str:
        ax = axis.lower()
        if ax not in ("x", "y", "z", "a"):
            return f"ERROR: unknown axis {axis}"
        data = MyData(ip=self._ip, port=self._port)
        data.motion.move_on(ax, float(delta), data.connection)
        self._refresh_after_move()
        return f"OK: {ax}{delta:+.3f}"

    def send_raw(self, gcode: str) -> str:
        data = MyData(ip=self._ip, port=self._port)
        data.motion.set_command(gcode)
        resp = send_command_string(data)
        log.info("send_raw(%s) -> %s", gcode, resp)
        self._refresh_after_move()
        return resp or ""

    def send_named_command(self, name: str) -> str:
        data = MyData(ip=self._ip, port=self._port)
        data.motion.send_command(name)
        time.sleep(0.3)
        self._refresh_after_move()
        return f"OK: {name}"

    def move_to_crystal(self, world_x: float, world_y: float,
                        vacuum_offset_x: float, vacuum_offset_y: float,
                        feed: int = 700) -> str:
        delta_x = world_x + vacuum_offset_x
        delta_y = world_y + vacuum_offset_y
        log.info("move_to_crystal: world=(%.2f, %.2f) + vacuum=(%.1f, %.1f) = delta=(%.2f, %.2f)",
                 world_x, world_y, vacuum_offset_x, vacuum_offset_y, delta_x, delta_y)
        data = MyData(ip=self._ip, port=self._port)
        data.motion.move_on("x", float(delta_x), data.connection)
        data.motion.move_on("y", float(delta_y), data.connection)
        self._refresh_after_move()
        return f"OK: X{delta_x:+.3f} Y{delta_y:+.3f}"

    def _refresh_after_move(self) -> None:
        try:
            self.get_position()
        except Exception:
            pass
