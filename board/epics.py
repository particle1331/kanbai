from __future__ import annotations

import json
from pathlib import Path

from paths import EPICS_PATH


def _load() -> dict:
    if not EPICS_PATH.exists():
        return {}
    return json.loads(EPICS_PATH.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    EPICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EPICS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def ensure_epics_file() -> None:
    if not EPICS_PATH.exists():
        _save({})


def list_epics(include_archived: bool = False) -> list[str]:
    data = _load()
    return [
        name
        for name, info in data.items()
        if include_archived or not info.get("archived", False)
    ]


def create_epic(name: str) -> None:
    name = name.strip()
    if not name:
        return
    data = _load()
    # Case-insensitive duplicate check — prevents bot from creating
    # "Epic Name" and "epic name" (or a trimmed vs un-trimmed variant) as
    # two separate entries.
    name_lower = name.lower()
    if any(k.strip().lower() == name_lower for k in data):
        return
    data[name] = {"archived": False, "archived_card_ids": []}
    _save(data)


def archive_epic(name: str, card_ids: list[str]) -> None:
    """Mark epic as archived and snapshot the card IDs archived with it."""
    data = _load()
    if name not in data:
        data[name] = {}
    data[name]["archived"] = True
    data[name]["archived_card_ids"] = card_ids
    _save(data)


def unarchive_epic(name: str) -> list[str]:
    """Mark epic as active. Returns the card IDs that were snapshotted with it."""
    data = _load()
    if name not in data:
        return []
    card_ids = list(data[name].get("archived_card_ids", []))
    data[name]["archived"] = False
    data[name]["archived_card_ids"] = []
    _save(data)
    return card_ids


def epic_exists(name: str) -> bool:
    return name in _load()


def is_epic_archived(name: str) -> bool:
    return _load().get(name, {}).get("archived", False)
