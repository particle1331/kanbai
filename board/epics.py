from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from paths import EPICS_PATH

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_EPIC_DESCRIPTION = 20_000
MAX_EPIC_COMMENTS = 200
MAX_EPIC_COMMENT_LEN = 4_000


def _load() -> dict:
    if not EPICS_PATH.exists():
        return {}
    return json.loads(EPICS_PATH.read_text(encoding="utf-8"))


def _save(data: dict) -> None:
    EPICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EPICS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def create_epic(name: str, description: str = "") -> None:
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
    data[name] = {
        "archived": False,
        "archived_card_ids": [],
        "description": description.strip(),
        "comments": [],
    }
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


# ── Description ───────────────────────────────────────────────────────────────

def get_epic_description(name: str) -> str:
    """Return the markdown description of an epic (empty string if none)."""
    data = _load()
    if name not in data:
        return ""
    return data[name].get("description", "")


def set_epic_description(name: str, description: str) -> bool:
    """Set the markdown description of an epic. Returns False if epic not found."""
    description = (description or "").strip()
    if len(description) > MAX_EPIC_DESCRIPTION:
        raise ValueError(
            f"Epic description: maximum {MAX_EPIC_DESCRIPTION} characters allowed "
            f"(got {len(description)})."
        )
    data = _load()
    if name not in data:
        return False
    data[name]["description"] = description
    _save(data)
    return True


# ── Comments ──────────────────────────────────────────────────────────────────

def get_epic_comments(name: str) -> list[dict]:
    """Return all comments for an epic (list of {id, text, created_at})."""
    data = _load()
    if name not in data:
        return []
    return list(data[name].get("comments", []))


def add_epic_comment(name: str, text: str) -> dict | None:
    """Add a comment to an epic. Returns the new comment dict or None if epic not found."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Epic comment: text must not be empty.")
    if len(text) > MAX_EPIC_COMMENT_LEN:
        raise ValueError(
            f"Epic comment: maximum {MAX_EPIC_COMMENT_LEN} characters allowed "
            f"(got {len(text)})."
        )
    data = _load()
    if name not in data:
        return None
    comments = data[name].setdefault("comments", [])
    if len(comments) >= MAX_EPIC_COMMENTS:
        raise ValueError(
            f"Epic comments: maximum {MAX_EPIC_COMMENTS} comments reached."
        )
    comment = {"id": str(uuid.uuid4()), "text": text, "created_at": _now()}
    comments.append(comment)
    _save(data)
    return comment


def delete_epic_comment(name: str, comment_id: str) -> bool:
    """Delete a comment from an epic by comment ID. Returns True if deleted."""
    data = _load()
    if name not in data:
        return False
    comments = data[name].get("comments", [])
    before = len(comments)
    data[name]["comments"] = [c for c in comments if c.get("id") != comment_id]
    if len(data[name]["comments"]) == before:
        return False
    _save(data)
    return True


def get_epic_detail(name: str) -> dict | None:
    """Return full epic info dict or None if not found."""
    data = _load()
    if name not in data:
        return None
    info = data[name]
    return {
        "name": name,
        "archived": info.get("archived", False),
        "description": info.get("description", ""),
        "comments": info.get("comments", []),
    }
