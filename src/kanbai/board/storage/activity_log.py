"""Append-only board activity log.

Unlike ``event_log``, which is lane-transition specific and feeds flow metrics,
this log records board-wide notable actions so they can survive card deletion.
Each line is a JSON object with at least:

    {"type": "card_deleted", "at": "<iso8601>"}

When available, card metadata is included so deleted cards remain identifiable
in the audit trail.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from kanbai.board.models import Card
from kanbai.paths import ACTIVITY_LOG


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _card_snapshot(card: Card) -> dict[str, Any]:
    return {
        "card_id": card.id,
        "card_number": card.number,
        "card_title": card.title,
        "card_lane": card.lane,
        "card_epic": card.epic,
    }


def record_event(event_type: str, *, card: Card | None = None, at: str | None = None, **details: Any) -> None:
    """Append a generic board activity event to the durable JSONL log."""
    event: dict[str, Any] = {"type": event_type, "at": at or _now()}
    if card is not None:
        event.update(_card_snapshot(card))
    if details:
        event.update(details)
    ACTIVITY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIVITY_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_all() -> list[dict[str, Any]]:
    """Return every logged activity event in file order. Missing file → empty list."""
    if not ACTIVITY_LOG.exists():
        return []
    events: list[dict[str, Any]] = []
    with open(ACTIVITY_LOG, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events
