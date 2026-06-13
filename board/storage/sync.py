from __future__ import annotations

from board import counter as _counter
from board import epics as _epics
from board.storage import event_log, sqlite_cache
from board.storage.markdown_store import load_all_cards, write_card


def full_sync() -> int:
    """Rebuild the SQLite cache from all markdown files. Returns count of cards loaded."""
    sqlite_cache.init_db()
    _epics.ensure_epics_file()
    _counter.ensure_counter_file()

    cards = load_all_cards()

    # Populate the cache with already-numbered cards first so the counter's
    # high-water-mark reconciliation can see them during backfill below.
    for card in cards:
        if card.number is not None:
            sqlite_cache.upsert_card(card)

    # Backfill: assign numbers to cards that are missing them (deterministic: sort by created_at)
    unnumbered = [c for c in cards if c.number is None]
    if unnumbered:
        unnumbered.sort(key=lambda c: c.created_at)
        for card in unnumbered:
            card.number = _counter.next_number()
            write_card(card)  # rewrite frontmatter in-place, no rename
            sqlite_cache.upsert_card(card)

    # Rebuild the transitions cache from the durable JSONL event log.
    event_log.replay_into_cache()

    return len(cards)
