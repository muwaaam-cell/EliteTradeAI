"""Persistent, fail-closed manual strategy selection state."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Final

VALID_STRATEGY_MODES: Final[frozenset[str]] = frozenset({"SCALPING", "INTRADAY"})


def load_selected_mode(path: str | Path) -> str | None:
    """Return a valid persisted selection; missing or corrupt state means WAIT."""
    target = Path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    mode = payload.get("mode")
    return mode if isinstance(mode, str) and mode in VALID_STRATEGY_MODES else None


def save_selected_mode(path: str | Path, mode: str) -> str:
    """Atomically persist only an explicitly selected supported strategy mode."""
    if not isinstance(mode, str) or mode not in VALID_STRATEGY_MODES:
        raise ValueError("Strategy mode must be SCALPING or INTRADAY.")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    try:
        temporary.write_text(json.dumps({"mode": mode}, separators=(",", ":")), encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, target)
        target.chmod(0o600)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError("Strategy mode could not be saved; candidate generation remains blocked.") from None
    return mode
