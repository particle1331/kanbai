from __future__ import annotations

import json
from pathlib import Path

from kanbai.board.storage import sqlite_cache
from kanbai.paths import COUNTER_PATH


def ensure_counter_file() -> None:
    if not COUNTER_PATH.exists():
        COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
        COUNTER_PATH.write_text(json.dumps({"next": 1}), encoding="utf-8")


def next_number() -> int:
    """Return the next card number as a monotonic high-water mark.

    The stored counter is reconciled against the highest existing card number,
    so it never reuses a number after a deletion and self-heals if the counter
    file drifts (manual edit, partial reseed, etc.).
    """
    ensure_counter_file()
    data = json.loads(COUNTER_PATH.read_text(encoding="utf-8"))
    stored = data.get("next", 1)
    n = max(stored, sqlite_cache.max_number() + 1)
    COUNTER_PATH.write_text(json.dumps({"next": n + 1}), encoding="utf-8")
    return n
