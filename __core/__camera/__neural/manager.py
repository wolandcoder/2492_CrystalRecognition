from __future__ import annotations

import importlib
from typing import Any

from .base import FrameContext, NeuralMod


class NeuralManager:
    def __init__(self, mod_names: list[str]) -> None:
        self._mods: list[NeuralMod] = []
        for mod_name in mod_names:
            self._mods.append(self._load_mod(mod_name))

    @property
    def loaded_mod_names(self) -> list[str]:
        return [mod.name for mod in self._mods]

    def apply(self, frame: Any, context: FrameContext) -> Any:
        out = frame
        for mod in self._mods:
            out = mod.apply(out, context)
        return out

    @staticmethod
    def list_available() -> list[str]:
        from .mods import AVAILABLE_MODS

        return list(AVAILABLE_MODS)

    def _load_mod(self, mod_name: str) -> NeuralMod:
        if not mod_name.startswith("__"):
            raise ValueError(f"Mod name must start with '__': {mod_name}")

        module_path = f"__core.__camera.__neural.mods.{mod_name}"
        try:
            module = importlib.import_module(module_path)
        except Exception as exc:                    
            raise RuntimeError(f"Cannot import neural mod '{mod_name}': {exc}") from exc

        mod_class = getattr(module, "Mod", None)
        if mod_class is None:
            raise RuntimeError(f"Neural mod '{mod_name}' must define class Mod")

        mod_instance = mod_class()
        if not isinstance(mod_instance, NeuralMod):
            raise RuntimeError(f"Neural mod '{mod_name}' Mod class must inherit NeuralMod")

        return mod_instance
