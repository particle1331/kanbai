"""Durable lane-transition event log.

Card movements between lanes cannot be reconstructed from the markdown
source of truth (a card only stores its *current* lane), so transitions are
recorded here as an append-only JSONL file under ``board_data/``. The file is
the durable source of truth for flow metrics; the SQLite ``transitions`` table
is a rebuildable cache populated from it (see ``replay_into_cache``).

Each line is a JSON object::

    {"card_id": "<uuid>", "from": null, "to": "backlog", "at": "<iso8601>"}

``from`` is ``null`` for the creation event (a card entering the board).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from kanbai.paths import TRANSITIONS_LOG
from kanbai.board.storage import sqlite_cache


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_transition(
    card_id: str,
    from_col: str | None,
    to_col: str,
    at: str | None = None,
) -> None:
    """Append a lane transition to the durable log and mirror it into cache.

    A no-op when ``from_col == to_col`` (no actual lane change occurred).
    """
    if from_col == to_col:
        return
    at = at or _now()
    event = {"card_id": card_id, "from": from_col, "to": to_col, "at": at}
    TRANSITIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(TRANSITIONS_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")
    sqlite_cache.insert_transition(card_id, from_col, to_col, at)


def read_all() -> list[dict]:
    """Return every logged transition in file order. Missing file → empty list."""
    if not TRANSITIONS_LOG.exists():
        return []
    events: list[dict] = []
    with open(TRANSITIONS_LOG, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # skip corrupt lines rather than failing the whole load
    return events


def replay_into_cache() -> int:
    """Rebuild the SQLite ``transitions`` table from the JSONL log.

    Called during ``full_sync`` so the queryable cache always reflects the
    durable log. Returns the number of events replayed.
    """
    events = read_all()
    sqlite_cache.replace_transitions(
        [
            (e["card_id"], e.get("from"), e["to"], e["at"])
            for e in events
            if e.get("card_id") and e.get("to") and e.get("at")
        ]
    )
    return len(events)


def backfill_from_cards(clear: bool = True) -> int:
    """Seed the log with best-effort historical transitions from current cards.

    Each card only stores its *current* lane, so the exact path it took is
    unknown. This reconstructs a plausible linear path
    (``backlog → in_progress → review → done``) up to the card's current
    lane, placing the creation event at ``created_at`` and spacing the
    remaining transitions evenly between ``created_at`` and ``updated_at``.

    This is an approximation intended for development/testing — the ``started``
    and ``review`` timestamps in particular are synthesised, not real. Returns
    the number of events written.

    When ``clear`` is True (default) the existing log is replaced rather than
    appended to, so repeated runs are idempotent.
    """
    from kanbai.board.models import LANES
    from kanbai.board.storage.markdown_store import load_all_cards

    col_index = {c: i for i, c in enumerate(LANES)}
    events: list[dict] = []

    for card in load_all_cards():
        if not card.created_at:
            continue
        target = col_index.get(card.lane)
        if target is None:
            continue

        start = _parse(card.created_at)
        end = _parse(card.updated_at) or start
        if end < start:
            end = start

        # The path of lanes the card passed through, in order, ending at its
        # current lane: [backlog, in_progress, ...].
        path = LANES[: target + 1]
        steps = len(path)  # number of transitions incl. the creation event

        for step, to_col in enumerate(path):
            from_col = None if step == 0 else path[step - 1]
            if step == 0:
                at = start
            elif steps == 1:
                at = start
            else:
                frac = step / (steps - 1)
                at = start + (end - start) * frac
            events.append(
                {
                    "card_id": card.id,
                    "from": from_col,
                    "to": to_col,
                    "at": at.isoformat(),
                }
            )

    events.sort(key=lambda e: e["at"])

    TRANSITIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if clear else "a"
    with open(TRANSITIONS_LOG, mode, encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")

    replay_into_cache()
    return len(events)


def _parse(iso_str: str | None) -> datetime | None:
    if not iso_str:
        return None
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts

