"""Per-character roster package.

Each character lives in its own module here and exposes a single
``CHARACTER`` dict. This file auto-discovers them so adding a new character is
just dropping a new file in this folder — no central list to edit. Scales to
hundreds of characters without one giant module.

Order is by ``id`` for stable, deterministic seeding/diagnostics.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Dict, List


def _load_roster() -> List[Dict[str, Any]]:
    chars: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for mod_info in pkgutil.iter_modules(__path__):
        if mod_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{mod_info.name}")
        char = getattr(module, "CHARACTER", None)
        if char is None:
            continue
        cid = char.get("id")
        if cid in seen:
            raise ValueError(f"Duplicate character id {cid!r} (module {mod_info.name})")
        seen.add(cid)
        chars.append(char)
    chars.sort(key=lambda c: c["id"])
    return chars


ROSTER: List[Dict[str, Any]] = _load_roster()
