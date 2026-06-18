from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from board.models import Card, _UNSET
from paths import CACHE_DB as DB_PATH


# Human/agent-readable definitions for the metrics returned by get_board_summary.
# Keep these in sync with the computations in this module so any consumer
# (MCP clients, the in-app chat agent, the UI) interprets the numbers correctly.
METRIC_DEFINITIONS: dict[str, str] = {
    "by_lane": "Count of non-archived cards in each lane (backlog, in_progress, review, done).",
    "by_priority": "Count of non-archived cards at each priority level.",
    "total": "Total non-archived cards on the board (or within the epic, if filtered).",
    "done": "Number of cards currently in the 'done' lane.",
    "completion_pct": "done / total as a whole-number percentage.",
    "avg_age_days": (
        "Average age in days of OPEN cards only (backlog, in progress, review) — "
        "done and archived cards are excluded. Age is measured from each card's "
        "created_at to now. Drives the board health indicator (lower is healthier)."
    ),
    "top_tags": "Top 10 tags by card count, descending, as [tag, count] pairs.",
    "cfd_last_90_days": (
        "Cumulative flow data for the last 90 days: per-day counts of cards in each "
        "stage, used to visualize work-in-progress and flow over time."
    ),
    "completion_heatmap_this_year": (
        "Per-day counts of cards COMPLETED (moved to done) during the current "
        "calendar year — a throughput/activity heatmap."
    ),
}


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                lane TEXT NOT NULL,
                priority TEXT DEFAULT 'medium',
                tags TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                archived INTEGER DEFAULT 0,
                description TEXT DEFAULT '',
                attachments TEXT DEFAULT '',
                secrets TEXT DEFAULT '',
                epic TEXT DEFAULT NULL,
                number INTEGER DEFAULT NULL,
                links TEXT DEFAULT '[]',
                assigned_to TEXT DEFAULT '',
                position REAL DEFAULT 0,
                due_date TEXT DEFAULT NULL
            )
        """)
        # Migrate existing DB: add columns if missing
        existing = {row[1] for row in con.execute("PRAGMA table_info(cards)").fetchall()}
        for col, ddl in [
            ("epic", "ALTER TABLE cards ADD COLUMN epic TEXT DEFAULT NULL"),
            ("number", "ALTER TABLE cards ADD COLUMN number INTEGER DEFAULT NULL"),
            ("links", "ALTER TABLE cards ADD COLUMN links TEXT DEFAULT '[]'"),
            ("assigned_to", "ALTER TABLE cards ADD COLUMN assigned_to TEXT DEFAULT ''"),
            ("position", "ALTER TABLE cards ADD COLUMN position REAL DEFAULT 0"),
            ("due_date", "ALTER TABLE cards ADD COLUMN due_date TEXT DEFAULT NULL"),
        ]:
            if col not in existing:
                con.execute(ddl)
        con.execute("CREATE INDEX IF NOT EXISTS idx_cards_number ON cards(number)")
        # Lane-transition log cache. The durable source of truth is the JSONL
        # file (see board.storage.event_log); this table is rebuilt from it on
        # full_sync and exists only for fast flow-metric aggregation.
        con.execute("""
            CREATE TABLE IF NOT EXISTS transitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id TEXT NOT NULL,
                from_col TEXT,
                to_col TEXT NOT NULL,
                at TEXT NOT NULL
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_transitions_at ON transitions(at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_transitions_card ON transitions(card_id)")
        con.commit()


def upsert_card(card: Card) -> None:
    with _conn() as con:
        con.execute(
            """
            INSERT INTO cards
                (id, title, lane, priority, tags, created_at, updated_at,
                 archived, description, attachments, secrets, epic, number, links, assigned_to, position, due_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title       = excluded.title,
                lane        = excluded.lane,
                priority    = excluded.priority,
                tags        = excluded.tags,
                created_at  = excluded.created_at,
                updated_at  = excluded.updated_at,
                archived    = excluded.archived,
                description = excluded.description,
                attachments = excluded.attachments,
                secrets     = excluded.secrets,
                epic        = excluded.epic,
                number      = excluded.number,
                links       = excluded.links,
                assigned_to = excluded.assigned_to,
                position    = excluded.position,
                due_date    = excluded.due_date
            """,
            (
                card.id,
                card.title,
                card.lane,
                card.priority,
                ",".join(card.tags),
                card.created_at,
                card.updated_at,
                int(card.archived),
                card.description,
                ",".join(card.attachments),
                ",".join(card.secrets),
                card.epic,
                card.number,
                json.dumps(card.links),
                ",".join(card.assigned_to),
                card.position,
                card.due_date,
            ),
        )
        con.commit()


def delete_card(card_id: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        con.commit()


# ── Lane transitions (flow metrics) ──────────────────────────────────────────

def insert_transition(card_id: str, from_col: str | None, to_col: str, at: str) -> None:
    """Insert a single lane-transition row into the cache."""
    with _conn() as con:
        con.execute(
            "INSERT INTO transitions (card_id, from_col, to_col, at) VALUES (?, ?, ?, ?)",
            (card_id, from_col, to_col, at),
        )
        con.commit()


def replace_transitions(rows: list[tuple[str, str | None, str, str]]) -> None:
    """Replace the entire transitions cache with ``rows`` (card_id, from, to, at).

    Used by full_sync to rebuild the cache from the durable JSONL log.
    """
    with _conn() as con:
        con.execute("DELETE FROM transitions")
        con.executemany(
            "INSERT INTO transitions (card_id, from_col, to_col, at) VALUES (?, ?, ?, ?)",
            rows,
        )
        con.commit()


def transition_flow_counts(
    since_iso: str,
    until_iso: str,
    epic: str | None = None,
) -> dict[str, int]:
    """Count flow events in the half-open window ``[since_iso, until_iso)``.

    Returns a dict with:
      - ``created``   – cards entering the board (from_col IS NULL)
      - ``started``   – cards entering ``in_progress``
      - ``completed`` – cards entering ``done``

    When ``epic`` is given, only transitions whose card currently belongs to
    that epic are counted (epic is a property of the card, not the event).
    """
    join = ""
    epic_clause = ""
    params: list = [since_iso, until_iso]
    if epic is not None:
        join = "JOIN cards c ON c.id = t.card_id"
        epic_clause = " AND c.epic = ?"
        params.append(epic)

    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT
                SUM(CASE WHEN t.from_col IS NULL THEN 1 ELSE 0 END) AS created,
                SUM(CASE WHEN t.to_col = 'in_progress' THEN 1 ELSE 0 END) AS started,
                SUM(CASE WHEN t.to_col = 'done' THEN 1 ELSE 0 END) AS completed
            FROM transitions t
            {join}
            WHERE t.at >= ? AND t.at < ?{epic_clause}
            """,
            params,
        ).fetchone()

    return {
        "created": int(rows["created"] or 0),
        "started": int(rows["started"] or 0),
        "completed": int(rows["completed"] or 0),
    }


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["links"] = json.loads(d.get("links") or "[]")
    return d


def query_cards(
    lane: str | None = None,
    tags: list[str] | None = None,
    archived: bool | None = False,
    query: str | None = None,
    epic=_UNSET,
) -> list[dict]:
    sql = 'SELECT * FROM cards WHERE 1=1'
    params: list = []

    if lane is not None:
        sql += ' AND lane = ?'
        params.append(lane)
    if archived is not None:
        sql += " AND archived = ?"
        params.append(int(archived))
    if query:
        sql += " AND (title LIKE ? OR description LIKE ?)"
        params.extend([f"%{query}%", f"%{query}%"])
    if epic is not _UNSET:
        if epic is None:
            sql += " AND (epic IS NULL OR epic = '')"
        else:
            sql += " AND epic = ?"
            params.append(epic)

    sql += ' ORDER BY position ASC, created_at DESC'

    with _conn() as con:
        rows = con.execute(sql, params).fetchall()

    results = [_row_to_dict(r) for r in rows]

    if tags:
        results = [
            r for r in results
            if any(t in (r.get("tags") or "").split(",") for t in tags)
        ]

    return results


def get_card_by_id(card_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_card_by_number(number: int) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM cards WHERE number = ?", (number,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def max_number() -> int:
    """Highest card number currently in the cache (0 if no cards)."""
    with _conn() as con:
        row = con.execute("SELECT COALESCE(MAX(number), 0) AS n FROM cards").fetchone()
    return int(row["n"])



def count_cards_by_lane(epic: str | None = None) -> dict[str, int]:
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f'SELECT lane, COUNT(*) as cnt FROM cards WHERE archived = 0{clause} GROUP BY lane',
            params,
        ).fetchall()
    return {row["lane"]: row["cnt"] for row in rows}


def count_cards_by_priority(epic: str | None = None) -> dict[str, int]:
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f"SELECT priority, COUNT(*) as cnt FROM cards WHERE archived = 0{clause} GROUP BY priority",
            params,
        ).fetchall()
    return {row["priority"]: row["cnt"] for row in rows}


def count_cards_by_epic() -> dict[str, int]:
    with _conn() as con:
        rows = con.execute(
            "SELECT COALESCE(NULLIF(epic, ''), 'Unassigned') as epic_name, "
            "COUNT(*) as cnt FROM cards WHERE archived = 0 GROUP BY epic_name"
        ).fetchall()
    return {row["epic_name"]: row["cnt"] for row in rows}


def count_cards_in_epic(epic: str | None) -> int:
    """Count active (non-archived) cards in an epic. Pass None for the
    Unassigned bucket (cards with NULL or empty epic)."""
    if epic:
        clause = "epic = ?"
        params: tuple = (epic,)
    else:
        clause = "(epic IS NULL OR epic = '')"
        params = ()
    with _conn() as con:
        row = con.execute(
            f"SELECT COUNT(*) as cnt FROM cards WHERE archived = 0 AND {clause}",
            params,
        ).fetchone()
    return row["cnt"] if row else 0


def done_cards_by_date() -> list[dict]:
    """Non-archived done cards grouped by completion date (substr of updated_at)."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT substr(updated_at, 1, 10) as date, COUNT(*) as count
            FROM cards
            WHERE lane = 'done' AND archived = 0 AND updated_at != ''
            GROUP BY date
            ORDER BY date
            """
        ).fetchall()
    return [{"date": row["date"], "count": row["count"]} for row in rows]


def done_cards_heatmap(year: int, epic: str | None = None) -> dict[str, int]:
    """Done cards per day for a given year. Returns {date_str: count} (UTC dates)."""
    clause = " AND epic = ?" if epic is not None else ""
    params = (str(year), epic) if epic is not None else (str(year),)
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT substr(updated_at, 1, 10) as date, COUNT(*) as count
            FROM cards
            WHERE lane = 'done' AND archived = 0 AND updated_at != ''
              AND substr(updated_at, 1, 4) = ?{clause}
            GROUP BY date
            """,
            params,
        ).fetchall()
    return {row["date"]: row["count"] for row in rows}


def done_cards_heatmap_timestamps(year: int, epic: str | None = None) -> list[str]:
    """Raw updated_at timestamps for done cards near a given year.

    Fetches year-1 through year+1 so the caller can re-bucket by local time
    without missing cards that straddle the UTC/local year boundary.
    """
    clause = " AND epic = ?" if epic is not None else ""
    params = (str(year - 1), str(year), str(year + 1), epic) if epic is not None else (str(year - 1), str(year), str(year + 1))
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT updated_at FROM cards
            WHERE lane = 'done' AND archived = 0 AND updated_at != ''
              AND (
                substr(updated_at, 1, 4) = ?
                OR substr(updated_at, 1, 4) = ?
                OR substr(updated_at, 1, 4) = ?
              ){clause}
            """,
            params,
        ).fetchall()
    return [row["updated_at"] for row in rows]


def transition_activity_heatmap(year: int, epic: str | None = None) -> dict[str, int]:
    """Card movements per day for a given year. Returns {date_str: count} (UTC dates).

    Counts transitions between lanes only — card creation events
    (``from_col IS NULL``) are excluded.
    """
    join = ""
    epic_clause = ""
    params: list = [str(year)]
    if epic is not None:
        join = "JOIN cards c ON c.id = t.card_id"
        epic_clause = " AND c.epic = ?"
        params.append(epic)
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT substr(t.at, 1, 10) as date, COUNT(*) as count
            FROM transitions t
            {join}
            WHERE t.from_col IS NOT NULL
              AND substr(t.at, 1, 4) = ?{epic_clause}
            GROUP BY date
            """,
            params,
        ).fetchall()
    return {row["date"]: row["count"] for row in rows}


def transition_activity_heatmap_timestamps(year: int, epic: str | None = None) -> list[str]:
    """Raw ``at`` timestamps for card movements near a given year.

    Fetches year-1 through year+1 so the caller can re-bucket by local time
    without missing movements that straddle the UTC/local year boundary.
    Card creation events (``from_col IS NULL``) are excluded.
    """
    join = ""
    epic_clause = ""
    params: list = [str(year - 1), str(year), str(year + 1)]
    if epic is not None:
        join = "JOIN cards c ON c.id = t.card_id"
        epic_clause = " AND c.epic = ?"
        params.append(epic)
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT t.at FROM transitions t
            {join}
            WHERE t.from_col IS NOT NULL
              AND (
                substr(t.at, 1, 4) = ?
                OR substr(t.at, 1, 4) = ?
                OR substr(t.at, 1, 4) = ?
              ){epic_clause}
            """,
            params,
        ).fetchall()
    return [row["at"] for row in rows]


def count_archived(epic: str | None = None) -> int:
    """Total number of archived cards, optionally filtered by epic."""
    if epic is not None:
        clause = " AND epic = ?"
        params: tuple = (epic,)
    else:
        clause = ""
        params = ()
    with _conn() as con:
        row = con.execute(
            f"SELECT COUNT(*) as cnt FROM cards WHERE archived = 1{clause}",
            params,
        ).fetchone()
    return int(row["cnt"])


def count_archived_within(days: int, epic: str | None = None) -> int:
    """Count cards archived in the last ``days`` days (uses updated_at as proxy)."""
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    clause = " AND epic = ?" if epic is not None else ""
    params: tuple = (since, epic) if epic is not None else (since,)
    with _conn() as con:
        row = con.execute(
            f"SELECT COUNT(*) as cnt FROM cards WHERE archived = 1 AND updated_at >= ?{clause}",
            params,
        ).fetchone()
    return int(row["cnt"])


def count_due_within(days: int, epic: str | None = None) -> int:
    """Count active (non-archived, non-done) cards whose due_date is on or
    before *days* days from today. Overdue cards are included; done and
    archived cards are excluded.
    """
    import datetime as _dt
    cutoff = (_dt.date.today() + _dt.timedelta(days=days)).isoformat()
    clause = " AND epic = ?" if epic is not None else ""
    params = (cutoff, epic) if epic is not None else (cutoff,)
    with _conn() as con:
        row = con.execute(
            f"""
            SELECT COUNT(*) as cnt FROM cards
            WHERE archived = 0 AND lane != 'done'
              AND due_date IS NOT NULL AND due_date != ''
              AND due_date <= ?{clause}
            """,
            params,
        ).fetchone()
    return int(row["cnt"])


def due_soon_cards(n: int = 5, epic: str | None = None) -> list[dict]:
    """Active (non-archived, non-done) cards with a due_date, soonest first.

    Overdue cards are included (they sort to the top). Done and archived cards
    are excluded.
    """
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic, n) if epic is not None else (n,)
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT id, number, title, due_date, priority, lane, epic
            FROM cards
            WHERE archived = 0 AND lane != 'done'
              AND due_date IS NOT NULL AND due_date != ''{clause}
            ORDER BY due_date ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def count_with_due_date(epic: str | None = None) -> int:
    """Count active (non-archived, non-done) cards that have a due_date.

    Matches the population shown by ``due_soon_cards`` so a capped list can
    still report its true total.
    """
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        row = con.execute(
            f"""
            SELECT COUNT(*) as cnt FROM cards
            WHERE archived = 0 AND lane != 'done'
              AND due_date IS NOT NULL AND due_date != ''{clause}
            """,
            params,
        ).fetchone()
    return int(row["cnt"])


def count_updated_within(days: int, epic: str | None = None) -> int:
    """Count non-archived cards updated within the trailing *days* window."""
    import datetime as _dt
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat()
    clause = " AND epic = ?" if epic is not None else ""
    params = (cutoff, epic) if epic is not None else (cutoff,)
    with _conn() as con:
        row = con.execute(
            f"""
            SELECT COUNT(*) as cnt FROM cards
            WHERE archived = 0 AND updated_at != '' AND updated_at >= ?{clause}
            """,
            params,
        ).fetchone()
    return int(row["cnt"])


def count_completed_within(days: int, epic: str | None = None) -> int:
    """Count non-archived done cards updated within the trailing *days* window.

    Mirrors ``recently_completed_cards`` (done cards ordered by ``updated_at``)
    so its capped list can report a meaningful 7-day total.
    """
    import datetime as _dt
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat()
    clause = " AND epic = ?" if epic is not None else ""
    params = (cutoff, epic) if epic is not None else (cutoff,)
    with _conn() as con:
        row = con.execute(
            f"""
            SELECT COUNT(*) as cnt FROM cards
            WHERE archived = 0 AND lane = 'done'
              AND updated_at != '' AND updated_at >= ?{clause}
            """,
            params,
        ).fetchone()
    return int(row["cnt"])


def count_started_within(days: int, epic: str | None = None) -> int:
    """Count distinct cards that entered 'in_progress' within the trailing *days* window.

    Uses the transitions table with COUNT(DISTINCT card_id) so cards that were
    started, moved back, then started again are only counted once.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    join = ""
    epic_clause = ""
    params: list = [cutoff]
    if epic is not None:
        join = "JOIN cards c ON c.id = t.card_id"
        epic_clause = " AND c.epic = ?"
        params.append(epic)
    with _conn() as con:
        row = con.execute(
            f"""
            SELECT COUNT(DISTINCT t.card_id) AS cnt
            FROM transitions t
            {join}
            WHERE t.to_col = 'in_progress' AND t.at >= ?{epic_clause}
            """,
            params,
        ).fetchone()
    return int(row["cnt"])


def count_completed_from_window(days: int, epic: str | None = None) -> int:
    """Count cards that were both *started* and *completed* within the trailing
    *days* window.

    This is the correct numerator for throughput: cards that entered
    ``in_progress`` **and** ``done`` within the same period, so we never
    count completions of work that was started in an earlier window.
    """
    from datetime import datetime, timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    join = ""
    epic_clause = ""
    params: list = [cutoff, cutoff]
    if epic is not None:
        join = "JOIN cards c ON c.id = s.card_id"
        epic_clause = " AND c.epic = ?"
        params.append(epic)
    with _conn() as con:
        row = con.execute(
            f"""
            SELECT COUNT(DISTINCT s.card_id) AS cnt
            FROM transitions s
            {join}
            JOIN transitions d ON d.card_id = s.card_id
            WHERE s.to_col = 'in_progress' AND s.at >= ?
              AND d.to_col = 'done'        AND d.at >= ?
              AND d.at >= s.at
              {epic_clause}
            """,
            params,
        ).fetchone()
    return int(row["cnt"])


def recently_created_cards(n: int = 5, epic: str | None = None) -> list[dict]:
    """Most recently created non-archived cards."""
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic, n) if epic is not None else (n,)
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT id, number, title, created_at, priority, lane, epic
            FROM cards
            WHERE archived = 0 AND created_at != ''{clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def recently_updated_cards(n: int = 5, epic: str | None = None) -> list[dict]:
    """Most recently updated non-archived cards."""
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic, n) if epic is not None else (n,)
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT id, number, title, updated_at, priority, lane, epic
            FROM cards
            WHERE archived = 0 AND updated_at != ''{clause}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def recently_completed_cards(n: int = 5, epic: str | None = None) -> list[dict]:
    """Most recently completed (done) non-archived cards, by update time."""
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic, n) if epic is not None else (n,)
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT id, number, title, updated_at, priority, lane, epic
            FROM cards
            WHERE archived = 0 AND lane = 'done' AND updated_at != ''{clause}
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def blocker_cards(n: int = 5, epic: str | None = None) -> list[dict]:
    """Non-archived WIP cards that block at least one other card.

    Only cards in ``in_progress`` or ``review`` are counted — backlog cards
    with ``blocks`` links are just tracking future dependencies, not active
    blockers.

    Each returned dict gains a ``block_count`` field (number of outgoing
    ``blocks`` links), sorted by block_count desc then most recently updated.
    """
    return _link_cards("blocks", "block_count", n, epic,
                       lanes=("in_progress", "review"))


def count_blocker_cards(epic: str | None = None) -> int:
    """Count non-archived WIP cards that block at least one non-archived card.

    Only cards in ``in_progress`` or ``review`` are counted — backlog cards
    with ``blocks`` links are just tracking future dependencies, not active
    blockers.

    Matches ``blocker_cards`` (a card counts only when it holds at least one
    live ``blocks`` link), but selects just the ``links`` column and skips dict
    building / sorting — cheaper for callers (e.g. health) that need only the
    total.
    """
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT links FROM cards
            WHERE archived = 0
              AND lane IN ('in_progress', 'review')
              AND links LIKE '%"blocks"%'{clause}
            """,
            params,
        ).fetchall()
        archived_numbers = {
            r["number"]
            for r in con.execute(
                "SELECT number FROM cards WHERE archived = 1"
            ).fetchall()
        }
    count = 0
    for r in rows:
        try:
            links = json.loads(r["links"] or "[]")
        except (TypeError, ValueError):
            links = []
        if any(
            isinstance(l, dict)
            and l.get("type") == "blocks"
            and l.get("number") not in archived_numbers
            for l in links
        ):
            count += 1
    return count


def blocked_cards(n: int = 5, epic: str | None = None) -> list[dict]:
    """Non-archived cards that are blocked by at least one other card.

    Each returned dict gains a ``blocked_count`` field (number of incoming
    ``blocked_by`` links), sorted by blocked_count desc then most recently
    updated.
    """
    return _link_cards("blocked_by", "blocked_count", n, epic)


def _link_cards(
    link_type: str,
    count_field: str,
    n: int,
    epic: str | None,
    lanes: tuple[str, ...] | None = None,
) -> list[dict]:
    """Return non-archived cards holding at least one link of *link_type*.

    When *lanes* is given, only cards in those lanes are included.  Only links
    whose target card is itself non-archived are counted, so the numbers
    reconcile across the blocker/blocked views.  Each returned dict gains
    *count_field* (number of matching live links), sorted by that count desc
    then most recently updated.
    """
    clause = " AND epic = ?" if epic is not None else ""
    params: list = [epic] if epic is not None else []
    lane_clause = ""
    if lanes:
        placeholders = ", ".join("?" for _ in lanes)
        lane_clause = f" AND lane IN ({placeholders})"
        params = list(lanes) + params
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT id, number, title, updated_at, priority, lane, epic, links
            FROM cards
            WHERE archived = 0{lane_clause} AND links LIKE '%"{link_type}"%'{clause}
            """,
            params,
        ).fetchall()
        archived_numbers = {
            r["number"]
            for r in con.execute(
                "SELECT number FROM cards WHERE archived = 1"
            ).fetchall()
        }
    result: list[dict] = []
    for r in rows:
        d = dict(r)
        try:
            links = json.loads(d.pop("links") or "[]")
        except (TypeError, ValueError):
            links = []
        count = sum(
            1
            for l in links
            if isinstance(l, dict)
            and l.get("type") == link_type
            and l.get("number") not in archived_numbers
        )
        if count:
            d[count_field] = count
            result.append(d)
    result.sort(key=lambda d: (d[count_field], d.get("updated_at") or ""), reverse=True)
    return result[:n]


def avg_card_age_days(epic: str | None = None) -> float:
    """Average age in days of non-archived, non-done cards (based on created_at)."""
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT created_at FROM cards
            WHERE archived = 0 AND lane != 'done' AND created_at != ''{clause}
            """,
            params,
        ).fetchall()
    if not rows:
        return 0.0
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    total = 0.0
    count = 0
    for row in rows:
        raw = row["created_at"]
        try:
            # Support both "YYYY-MM-DDTHH:MM:SS..." and "YYYY-MM-DD" formats
            if "T" in raw:
                dt_str = raw[:19]
                dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            else:
                dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            total += (now - dt).total_seconds() / 86400
            count += 1
        except ValueError:
            continue
    return round(total / count, 1) if count else 0.0


def count_cards_by_tag(limit: int = 10, epic: str | None = None) -> list[tuple[str, int]]:
    """Return top-N tags by card count, descending. Tags are comma-separated in DB."""
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f"SELECT tags FROM cards WHERE archived = 0 AND tags != ''{clause}",
            params,
        ).fetchall()
    tag_counts: dict[str, int] = {}
    for row in rows:
        for tag in row["tags"].split(","):
            tag = tag.strip()
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
    sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
    return sorted_tags[:limit]


def cfd_data(days: int = 90, epic: str | None = None) -> list[dict]:
    """Approximate Cumulative Flow Diagram data.

    Non-done cards are projected from their creation date using their current lane
    (best approximation without a full lane-change history table).
    Done cards use updated_at as their completion date.
    Returns one dict per day from the earliest card creation (or `days` ago) to today.

    NOTE: Dates are extracted from UTC timestamps and compared against UTC today.
    This means the CFD x-axis uses UTC calendar dates.  For users far from UTC a
    card completed near midnight may appear on the next UTC day — acceptable given
    that the CFD is already a coarse approximation.
    """
    from datetime import date as _date, datetime, timedelta, timezone

    with _conn() as con:
        epic_clause = " AND epic = ?" if epic is not None else ""
        epic_params = (epic,) if epic is not None else ()
        rows = con.execute(
            f'SELECT lane, created_at, updated_at FROM cards WHERE archived = 0{epic_clause}',
            epic_params,
        ).fetchall()

    if not rows:
        return []

    def _parse(raw: str) -> _date | None:
        if not raw:
            return None
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    cards = [
        {
            "lane": r["lane"],
            "created": _parse(r["created_at"]),
            "updated": _parse(r["updated_at"]),
        }
        for r in rows
    ]

    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=days)
    all_created = [c["created"] for c in cards if c["created"]]
    if not all_created:
        return []

    start = max(min(all_created), cutoff)
    result = []
    d = start
    while d <= today:
        result.append({
            "date": str(d),
            "backlog":     sum(1 for c in cards if c["lane"] == "backlog"     and c["created"] and c["created"] <= d),
            "in_progress": sum(1 for c in cards if c["lane"] == "in_progress" and c["created"] and c["created"] <= d),
            "review":      sum(1 for c in cards if c["lane"] == "review"      and c["created"] and c["created"] <= d),
            "done":        sum(1 for c in cards if c["lane"] == "done"        and c["updated"] and c["updated"] <= d),
        })
        d += timedelta(days=1)
    return result


def task_age_timeseries(days: int = 90, epic: str | None = None) -> list[dict]:
    """Daily minimum, average and maximum age (in days) of open cards over a window.

    A card is considered "open" on day *d* if it was created on or before *d*
    and is not currently in the done column. Done cards are excluded entirely
    from age calculations. Age is ``d - created_at`` in whole days.
    Returns one dict per day from the earliest card creation (or *days* ago) to
    today, each ``{"date", "min_age", "avg_age", "max_age"}``. Days with no open
    cards report zeros.
    """
    from datetime import date as _date, datetime, timedelta, timezone

    with _conn() as con:
        epic_clause = " AND epic = ?" if epic is not None else ""
        epic_params = (epic,) if epic is not None else ()
        rows = con.execute(
            f'SELECT lane, created_at, updated_at FROM cards WHERE archived = 0{epic_clause}',
            epic_params,
        ).fetchall()

    if not rows:
        return []

    def _parse(raw: str) -> _date | None:
        if not raw:
            return None
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    cards = [
        {
            "lane": r["lane"],
            "created": _parse(r["created_at"]),
            "updated": _parse(r["updated_at"]),
        }
        for r in rows
    ]

    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=days)
    all_created = [c["created"] for c in cards if c["created"]]
    if not all_created:
        return []

    start = max(min(all_created), cutoff)
    result = []
    d = start
    while d <= today:
        ages = []
        for c in cards:
            created = c["created"]
            if not created or created > d:
                continue
            # Exclude done cards from age calculations entirely.
            if c["lane"] == "done":
                continue
            ages.append((d - created).days)
        if ages:
            min_age = min(ages)
            avg_age = round(sum(ages) / len(ages), 1)
            max_age = max(ages)
        else:
            min_age = 0
            avg_age = 0.0
            max_age = 0
        result.append({"date": str(d), "min_age": min_age, "avg_age": avg_age, "max_age": max_age})
        d += timedelta(days=1)
    return result


def days_since_last_completed(epic: str | None = None) -> int | None:
    """Whole days since the most recently completed card, or None if none exist."""
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        row = con.execute(
            f"""
            SELECT updated_at FROM cards
            WHERE archived = 0 AND lane = 'done' AND updated_at != ''{clause}
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    if not row or not row["updated_at"]:
        return None
    from datetime import datetime, timezone
    raw = row["updated_at"]
    try:
        if "T" in raw:
            dt = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, (datetime.now(timezone.utc) - dt).days)


def open_card_age_stats(epic: str | None = None) -> dict:
    """Min / avg / max age (days) and count of open cards in one pass.

    "Open" = non-archived, non-done cards. Ages are ``now - created_at``.
    Returns ``{"count", "min_age", "avg_age", "max_age"}``; the age values are
    ``None`` when there are no open cards.
    """
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT created_at FROM cards
            WHERE archived = 0 AND lane != 'done' AND created_at != ''{clause}
            """,
            params,
        ).fetchall()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    ages: list[float] = []
    for row in rows:
        raw = row["created_at"]
        try:
            if "T" in raw:
                dt = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            else:
                dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        ages.append((now - dt).total_seconds() / 86400)
    if not ages:
        return {"count": 0, "min_age": None, "avg_age": None, "max_age": None}
    return {
        "count": len(ages),
        "min_age": round(min(ages), 1),
        "avg_age": round(sum(ages) / len(ages), 1),
        "max_age": round(max(ages), 1),
    }


def backlog_age_stats(epic: str | None = None) -> dict:
    """Median age and outlier count for cards in the backlog lane.

    Returns ``{"count", "median_age", "outlier_count"}`` where
    ``outlier_count`` is the number of cards with age > 2 * median_age.
    All age values are ``None`` / 0 when there are no backlog cards.
    """
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT created_at FROM cards
            WHERE archived = 0 AND lane = 'backlog'
              AND created_at != ''{clause}
            """,
            params,
        ).fetchall()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    ages: list[float] = []
    for row in rows:
        raw = row["created_at"]
        try:
            if "T" in raw:
                d = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            else:
                d = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        ages.append((now - d).total_seconds() / 86400)
    if not ages:
        return {"count": 0, "median_age": None, "outlier_count": 0}
    ages.sort()
    n = len(ages)
    mid = n // 2
    median = ages[mid] if n % 2 else (ages[mid - 1] + ages[mid]) / 2
    outliers = sum(1 for a in ages if a > 2 * median)
    outliers_1_5x = sum(1 for a in ages if a > 1.5 * median)
    return {
        "count": n,
        "median_age": round(median, 1),
        "outlier_count": outliers,
        "outlier_count_1_5x": outliers_1_5x,
    }


def wip_age_stats(epic: str | None = None) -> dict:
    """Min / median / avg / max age (days) of cards currently in WIP lanes.

    WIP = ``in_progress`` + ``review``.  Ages are measured from each card's
    most recent transition **into** ``in_progress`` (i.e. time actually spent
    in WIP), not from ``created_at``.

    Cards that are in a WIP lane but have no recorded ``in_progress``
    transition (e.g. moved directly to ``review``) fall back to
    ``created_at``.

    Returns ``{"count", "min_age", "median_age", "avg_age", "max_age"}``;
    age values are ``None`` when there are no WIP cards.
    """
    clause = " AND c.epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT
                c.id,
                c.created_at,
                (
                    SELECT MAX(t.at)
                    FROM transitions t
                    WHERE t.card_id = c.id
                      AND t.to_col = 'in_progress'
                ) AS started_at
            FROM cards c
            WHERE c.archived = 0
              AND c.lane IN ('in_progress', 'review')
              AND c.created_at != ''{clause}
            """,
            params,
        ).fetchall()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    ages: list[float] = []
    for row in rows:
        # Prefer started_at (most recent in_progress transition); fall back
        # to created_at if no in_progress transition exists.
        raw = row["started_at"] or row["created_at"]
        try:
            if "T" in raw:
                dt = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            else:
                dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        ages.append((now - dt).total_seconds() / 86400)
    if not ages:
        return {"count": 0, "min_age": None, "median_age": None, "avg_age": None, "max_age": None}
    ages.sort()
    n = len(ages)
    mid = n // 2
    median = ages[mid] if n % 2 else (ages[mid - 1] + ages[mid]) / 2
    return {
        "count": n,
        "min_age":    round(min(ages), 1),
        "median_age": round(median, 1),
        "avg_age":    round(sum(ages) / n, 1),
        "max_age":    round(max(ages), 1),
    }


def days_since_last_transition(epic: str | None = None) -> int | None:
    """Whole days since the most recent column transition, or None if none logged.

    When ``epic`` is given, only transitions for cards currently in that epic
    are considered.
    """
    join = ""
    clause = ""
    params: list = []
    if epic is not None:
        join = "JOIN cards c ON c.id = t.card_id"
        clause = " WHERE c.epic = ?"
        params.append(epic)
    with _conn() as con:
        row = con.execute(
            f"SELECT MAX(t.at) AS last_at FROM transitions t {join}{clause}",
            params,
        ).fetchone()
    if not row or not row["last_at"]:
        return None
    from datetime import datetime, timezone
    raw = row["last_at"]
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


def days_since_last_started(epic: str | None = None) -> int | None:
    """Whole days since the most recent card entered 'in_progress', or None."""
    join = ""
    clause = ""
    params: list = []
    if epic is not None:
        join = "JOIN cards c ON c.id = t.card_id"
        clause = " AND c.epic = ?"
        params.append(epic)
    with _conn() as con:
        row = con.execute(
            f"SELECT MAX(t.at) AS last_at FROM transitions t {join}"
            f" WHERE t.to_col = 'in_progress'{clause}",
            params,
        ).fetchone()
    if not row or not row["last_at"]:
        return None
    from datetime import datetime, timezone
    raw = row["last_at"]
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


def days_since_last_created(epic: str | None = None) -> int | None:
    """Whole days since the most recent card was created (entered the board), or None."""
    join = ""
    clause = ""
    params: list = []
    if epic is not None:
        join = "JOIN cards c ON c.id = t.card_id"
        clause = " AND c.epic = ?"
        params.append(epic)
    with _conn() as con:
        row = con.execute(
            f"SELECT MAX(t.at) AS last_at FROM transitions t {join}"
            f" WHERE t.from_col IS NULL{clause}",
            params,
        ).fetchone()
    if not row or not row["last_at"]:
        return None
    from datetime import datetime, timezone
    raw = row["last_at"]
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - dt).days)


def avg_cycle_time_days(days: int, epic: str | None = None) -> float | None:
    """Average cycle time in days for cards completed in the last ``days`` days.

    Cycle time = elapsed days from the most recent 'in_progress' transition to
    the 'done' transition for each completion event in the window.  Cards that
    were never started (no 'in_progress' transition) are excluded.
    Returns ``None`` when there are no qualifying completions.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    until = now.isoformat()

    join = ""
    epic_clause = ""
    params: list = [since, until]
    if epic is not None:
        join = "JOIN cards c ON c.id = t.card_id"
        epic_clause = " AND c.epic = ?"
        params.append(epic)

    with _conn() as con:
        rows = con.execute(
            f"""
            WITH done_events AS (
                SELECT t.card_id, t.at AS done_at
                FROM transitions t
                {join}
                WHERE t.to_col = 'done' AND t.at >= ? AND t.at < ?{epic_clause}
            ),
            cycle_pairs AS (
                SELECT d.card_id, d.done_at, MAX(s.at) AS started_at
                FROM done_events d
                JOIN transitions s ON s.card_id = d.card_id
                WHERE s.to_col = 'in_progress' AND s.at < d.done_at
                GROUP BY d.card_id, d.done_at
            )
            SELECT done_at, started_at FROM cycle_pairs
            """,
            params,
        ).fetchall()

    if not rows:
        return None

    total = 0.0
    count = 0
    for row in rows:
        try:
            done = datetime.fromisoformat(row["done_at"].replace("Z", "+00:00"))
            start = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
            if done.tzinfo is None:
                done = done.replace(tzinfo=timezone.utc)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            total += (done - start).total_seconds() / 86400
            count += 1
        except (ValueError, AttributeError):
            continue

    return round(total / count, 1) if count else None


def median_cycle_time_days(days: int, epic: str | None = None) -> float | None:
    """Median days-to-complete for cards done in the last ``days`` days.

    Days = done_at − created_at (same metric as cycle_time_histogram /
    cycle_time_weekly). Returns a dict {median, p25, p75} or all-None when
    there are no qualifying completions.
    """
    from datetime import datetime, timedelta, timezone

    now   = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    until = now.isoformat()

    epic_clause = " AND c.epic = ?" if epic is not None else ""
    params: list = [since, until]
    if epic is not None:
        params.append(epic)

    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT c.created_at, t.at AS done_at
            FROM transitions t
            JOIN cards c ON c.id = t.card_id
            WHERE t.to_col = 'done'
              AND t.at >= ? AND t.at < ?
              AND c.created_at != ''{epic_clause}
            """,
            params,
        ).fetchall()

    durations: list[float] = []
    for row in rows:
        try:
            done    = datetime.fromisoformat(row["done_at"].replace("Z", "+00:00"))
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if done.tzinfo is None:
                done = done.replace(tzinfo=timezone.utc)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            durations.append(max(0.0, (done - created).total_seconds() / 86400))
        except (ValueError, AttributeError):
            continue

    if not durations:
        return {"median": None, "p25": None, "p75": None}
    durations.sort()
    n = len(durations)

    def _percentile(p: float) -> float:
        idx = p / 100 * (n - 1)
        lo, hi = int(idx), min(int(idx) + 1, n - 1)
        return round(durations[lo] + (durations[hi] - durations[lo]) * (idx - lo), 1)

    mid = n // 2
    median = durations[mid] if n % 2 else (durations[mid - 1] + durations[mid]) / 2
    return {
        "median": round(median, 1),
        "p25":    _percentile(25),
        "p75":    _percentile(75),
    }


def transition_flow_weekly(weeks: int = 12, epic: str | None = None) -> list[dict]:
    """Per-week flow-event counts for the trailing ``weeks`` window.

    Weeks start on Monday (ISO). Returns one dict per week in chronological
    order: ``{"week_start", "created", "started", "completed"}``. Weeks with no
    events are still emitted (all zeros) so the chart x-axis is continuous.
    """
    from datetime import datetime, timedelta, timezone

    join = ""
    epic_clause = ""
    params: list = []
    if epic is not None:
        join = "JOIN cards c ON c.id = t.card_id"
        epic_clause = " AND c.epic = ?"

    now = datetime.now(timezone.utc)
    # Monday of the current week, then step back to cover the window.
    this_monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = this_monday - timedelta(weeks=weeks - 1)

    sql_params = [start.isoformat()] + ([epic] if epic is not None else [])
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT t.from_col AS from_col, t.to_col AS to_col, t.at AS at
            FROM transitions t {join}
            WHERE t.at >= ?{epic_clause}
            """,
            sql_params,
        ).fetchall()

    # Pre-seed every week bucket so gaps render as zero, not missing.
    buckets: dict[str, dict] = {}
    for i in range(weeks):
        wk = (start + timedelta(weeks=i)).date().isoformat()
        buckets[wk] = {"week_start": wk, "created": 0, "started": 0, "completed": 0}

    for row in rows:
        try:
            at = datetime.fromisoformat(str(row["at"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        wk_monday = (at - timedelta(days=at.weekday())).date().isoformat()
        bucket = buckets.get(wk_monday)
        if bucket is None:
            continue
        if row["from_col"] is None:
            bucket["created"] += 1
        if row["to_col"] == "in_progress":
            bucket["started"] += 1
        if row["to_col"] == "done":
            bucket["completed"] += 1

    return [buckets[k] for k in sorted(buckets)]


# ── Health page metrics ────────────────────────────────────────────────────────

def stale_wip_cards(n: int = 10, epic: str | None = None) -> list[dict]:
    """Cards in WIP (in_progress or review) sorted by days since last lane
    transition, descending.

    Each dict: {id, title, tags: list[str], priority: str, days_stale: int}
    """
    from datetime import datetime, timezone

    clause = " AND c.epic = ?" if epic is not None else ""
    params: list = ([epic] if epic is not None else [])

    with _conn() as con:
        card_rows = con.execute(
            f"""
            SELECT c.id, c.title, c.tags, c.priority,
                   MAX(t.at) AS last_at
            FROM cards c
            LEFT JOIN transitions t ON t.card_id = c.id
            WHERE c.archived = 0
              AND c.lane IN ('in_progress', 'review')
              {clause}
            GROUP BY c.id
            ORDER BY last_at ASC NULLS FIRST
            LIMIT ?
            """,
            params + [n],
        ).fetchall()

    now = datetime.now(timezone.utc)
    results = []
    for r in card_rows:
        last_at_str = r["last_at"]
        if last_at_str:
            try:
                last_at = datetime.fromisoformat(last_at_str.replace("Z", "+00:00"))
                if last_at.tzinfo is None:
                    last_at = last_at.replace(tzinfo=timezone.utc)
                days_stale = max(0, (now - last_at).days)
            except ValueError:
                days_stale = 0
        else:
            days_stale = 0
        raw_tags = r["tags"] or ""
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
        results.append({
            "id": r["id"],
            "title": r["title"],
            "tags": tags,
            "priority": r["priority"] or "medium",
            "days_stale": days_stale,
        })
    return results


def stale_wip_cards_started(min_days: int = 5, epic: str | None = None) -> list[dict]:
    """WIP cards (in_progress or review) where days since last in_progress
    transition exceeds *min_days*, sorted by that age descending.

    Each dict: {id, number, title, tags, priority, lane, days_started}
    """
    from datetime import datetime, timezone

    clause = " AND c.epic = ?" if epic is not None else ""
    params: list = [epic] if epic is not None else []

    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT c.id, c.number, c.title, c.tags, c.priority, c.lane,
                   MAX(t.at) AS started_at
            FROM cards c
            JOIN transitions t ON t.card_id = c.id
            WHERE c.archived = 0
              AND c.lane IN ('in_progress', 'review')
              AND t.to_col = 'in_progress'
              {clause}
            GROUP BY c.id
            ORDER BY started_at ASC NULLS FIRST
            """,
            params,
        ).fetchall()

    now = datetime.now(timezone.utc)
    results = []
    for r in rows:
        started_at_str = r["started_at"]
        if started_at_str:
            try:
                started_at = datetime.fromisoformat(
                    started_at_str.replace("Z", "+00:00")
                )
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                days_started = max(0, (now - started_at).days)
            except ValueError:
                days_started = 0
        else:
            days_started = 0
        if days_started > min_days:
            raw_tags = r["tags"] or ""
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
            results.append({
                "id": r["id"],
                "number": r["number"],
                "title": r["title"],
                "tags": tags,
                "priority": r["priority"] or "medium",
                "lane": r["lane"],
                "days_started": days_started,
            })

    results.sort(key=lambda x: x["days_started"], reverse=True)
    return results


def backlog_aged_cards(epic: str | None = None) -> list[dict]:
    """Backlog cards with age > 1.5 × median backlog age, sorted by age desc.

    Each dict: {id, number, title, priority, epic, days_old}
    Returns an empty list when there are no backlog cards.
    """
    from datetime import datetime, timezone

    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT id, number, title, created_at, priority, epic
            FROM cards
            WHERE archived = 0 AND lane = 'backlog'
              AND created_at != ''{clause}
            """,
            params,
        ).fetchall()

    now = datetime.now(timezone.utc)
    cards_with_age: list[tuple[float, dict]] = []
    for r in rows:
        raw = r["created_at"]
        try:
            if "T" in raw:
                d = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
            else:
                d = datetime.strptime(raw[:10], "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
        except ValueError:
            continue
        age = (now - d).total_seconds() / 86400
        cards_with_age.append((age, dict(r)))

    if not cards_with_age:
        return []

    ages = sorted(a for a, _ in cards_with_age)
    n = len(ages)
    mid = n // 2
    median = ages[mid] if n % 2 else (ages[mid - 1] + ages[mid]) / 2
    threshold = 1.5 * median

    result = []
    for age, r in cards_with_age:
        if age > threshold:
            result.append({
                "id": r["id"],
                "number": r["number"],
                "title": r["title"],
                "priority": r["priority"] or "medium",
                "epic": r["epic"] or "",
                "days_old": round(age, 1),
            })

    result.sort(key=lambda x: x["days_old"], reverse=True)
    return result


def backlog_aging_timeseries(days: int = 28, epic: str | None = None) -> list[dict]:
    """Daily min/avg/max/median/p25/p75 age of cards currently in the Backlog lane.

    Spec: backlog-only, age from created_at. Projects current state backward
    (same approximation as cfd_data).

    Returns: [{"date": str, "min_age": int, "avg_age": float, "max_age": int,
              "median_age": float, "p25_age": float, "p75_age": float}, ...]
    """
    from datetime import date as _date, datetime, timedelta, timezone
    import statistics

    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT created_at FROM cards
            WHERE archived = 0 AND lane = 'backlog' AND created_at != ''{clause}
            """,
            params,
        ).fetchall()

    def _parse(raw: str) -> _date | None:
        if not raw:
            return None
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    created_dates = [d for d in (_parse(r["created_at"]) for r in rows) if d is not None]
    today = datetime.now(timezone.utc).date()
    result = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        ages = [(d - cd).days for cd in created_dates if cd <= d and (d - cd).days >= 0]
        if ages:
            sorted_ages = sorted(ages)
            n = len(sorted_ages)
            median = statistics.median(sorted_ages)
            p25 = sorted_ages[n // 4] if n >= 4 else sorted_ages[0]
            p75 = sorted_ages[(3 * n) // 4] if n >= 4 else sorted_ages[-1]
            result.append({
                "date": str(d),
                "min_age": min(ages),
                "avg_age": round(sum(ages) / len(ages), 1),
                "max_age": max(ages),
                "median_age": median,
                "p25_age": float(p25),
                "p75_age": float(p75),
            })
        else:
            result.append({"date": str(d), "min_age": 0, "avg_age": 0.0, "max_age": 0,
                          "median_age": 0.0, "p25_age": 0.0, "p75_age": 0.0})
    return result


def cycle_time_histogram(days: int = 28, epic: str | None = None) -> dict:
    """Distribution of days-to-complete for cards done in the last ``days`` days.

    Days = done_at − created_at, ceiling to nearest whole day.
    Returns {"1": n, "2": n, "3": n, "4": n, "5": n, "6+": n}.
    """
    import math
    from datetime import datetime, timedelta, timezone

    now   = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    until = now.isoformat()

    epic_clause = " AND c.epic = ?" if epic is not None else ""
    params: list = [since, until]
    if epic is not None:
        params.append(epic)

    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT c.created_at, t.at AS done_at
            FROM transitions t
            JOIN cards c ON c.id = t.card_id
            WHERE t.to_col = 'done'
              AND t.at >= ? AND t.at < ?
              AND c.created_at != ''{epic_clause}
            """,
            params,
        ).fetchall()

    buckets: dict[str, int] = {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6+": 0}
    for row in rows:
        try:
            done    = datetime.fromisoformat(row["done_at"].replace("Z", "+00:00"))
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if done.tzinfo is None:
                done = done.replace(tzinfo=timezone.utc)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            ct = (done - created).total_seconds() / 86400
            b  = math.ceil(max(ct, 0.01))
            if b >= 6:
                buckets["6+"] += 1
            else:
                buckets[str(b)] = buckets.get(str(b), 0) + 1
        except (ValueError, AttributeError):
            continue
    return buckets


def wip_composition(epic: str | None = None) -> dict:
    """WIP (in_progress + review) breakdown by tag-type and priority.

    Returns:
        {"by_tag": {"bugfix": int, "feature": int, "enhancement": int, "other": int},
         "by_priority": {"high": int, "medium": int, "low": int},
         "total": int}
    """
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT tags, priority FROM cards
            WHERE archived = 0 AND lane IN ('in_progress', 'review'){clause}
            """,
            params,
        ).fetchall()
    _VALID_PRI = {"high", "medium", "low"}
    TYPE_TAGS = {"bugfix", "feature", "enhancement"}
    by_tag: dict[str, int] = {"bugfix": 0, "feature": 0, "enhancement": 0, "other": 0}
    by_priority: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for r in rows:
        pri = (r["priority"] or "medium").lower()
        if pri not in _VALID_PRI:
            pri = "medium"
        by_priority[pri] += 1
        raw = r["tags"] or ""
        tags = [t.strip() for t in raw.split(",") if t.strip()]
        tag = next((t for t in tags if t in TYPE_TAGS), None)
        by_tag[tag if tag else "other"] = by_tag.get(tag if tag else "other", 0) + 1
    return {"by_tag": by_tag, "by_priority": by_priority, "total": len(rows)}


def backlog_composition(epic: str | None = None) -> dict:
    """Backlog breakdown by tag-type and priority.

    Same structure as wip_composition().
    """
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT tags, priority FROM cards
            WHERE archived = 0 AND lane = 'backlog'{clause}
            """,
            params,
        ).fetchall()
    _VALID_PRI = {"high", "medium", "low"}
    TYPE_TAGS = {"bugfix", "feature", "enhancement"}
    by_tag: dict[str, int] = {"bugfix": 0, "feature": 0, "enhancement": 0, "other": 0}
    by_priority: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for r in rows:
        pri = (r["priority"] or "medium").lower()
        if pri not in _VALID_PRI:
            pri = "medium"
        by_priority[pri] += 1
        raw = r["tags"] or ""
        tags = [t.strip() for t in raw.split(",") if t.strip()]
        tag = next((t for t in tags if t in TYPE_TAGS), None)
        by_tag[tag if tag else "other"] = by_tag.get(tag if tag else "other", 0) + 1
    return {"by_tag": by_tag, "by_priority": by_priority, "total": len(rows)}


def completed_composition(days: int = 28, epic: str | None = None) -> dict:
    """Completed-cards breakdown by tag-type and priority for the last ``days`` days.

    Same structure as wip_composition().
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat()
    epic_clause = " AND c.epic = ?" if epic is not None else ""
    params: list = [since] + ([epic] if epic is not None else [])
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT DISTINCT c.id, c.tags, c.priority
            FROM transitions t
            JOIN cards c ON c.id = t.card_id
            WHERE t.to_col = 'done' AND t.at >= ?{epic_clause}
            """,
            params,
        ).fetchall()
    _VALID_PRI = {"high", "medium", "low"}
    TYPE_TAGS = {"bugfix", "feature", "enhancement"}
    by_tag: dict[str, int] = {"bugfix": 0, "feature": 0, "enhancement": 0, "other": 0}
    by_priority: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for r in rows:
        pri = (r["priority"] or "medium").lower()
        if pri not in _VALID_PRI:
            pri = "medium"
        by_priority[pri] += 1
        raw = r["tags"] or ""
        tags = [t.strip() for t in raw.split(",") if t.strip()]
        tag = next((t for t in tags if t in TYPE_TAGS), None)
        by_tag[tag if tag else "other"] = by_tag.get(tag if tag else "other", 0) + 1
    return {"by_tag": by_tag, "by_priority": by_priority, "total": len(rows)}


def daily_throughput_wip(days: int = 28, epic: str | None = None) -> list[dict]:
    """Per-day WIP count and 7-day rolling throughput for the trend chart.

    Returns [{"date": str, "wip": int, "throughput_7d": float | None}, ...] oldest first.
    throughput_7d = completed / started over trailing 7 days; None when no starts.
    """
    from datetime import datetime, timedelta, timezone

    cfd = cfd_data(days + 7, epic=epic)
    cfd_by_date: dict[str, dict] = {r["date"]: r for r in cfd}

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days + 7)).isoformat()
    join = ""
    epic_clause = ""
    sql_params: list = [cutoff]
    if epic is not None:
        join = "JOIN cards c ON c.id = t.card_id"
        epic_clause = " AND c.epic = ?"
        sql_params.append(epic)
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT date(t.at) AS day, t.to_col, count(*) AS cnt
            FROM transitions t {join}
            WHERE t.at >= ?{epic_clause}
            GROUP BY date(t.at), t.to_col
            """,
            sql_params,
        ).fetchall()
    daily_completed: dict[str, int] = {}
    daily_started: dict[str, int] = {}
    for r in rows:
        day = r["day"]
        if r["to_col"] == "done":
            daily_completed[day] = daily_completed.get(day, 0) + r["cnt"]
        elif r["to_col"] == "in_progress":
            daily_started[day] = daily_started.get(day, 0) + r["cnt"]

    today = datetime.now(timezone.utc).date()
    result = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        d_str = str(d)
        cfd_row = cfd_by_date.get(d_str, {})
        wip = cfd_row.get("in_progress", 0) + cfd_row.get("review", 0)
        w_completed = sum(daily_completed.get(str(d - timedelta(days=j)), 0) for j in range(7))
        w_started = sum(daily_started.get(str(d - timedelta(days=j)), 0) for j in range(7))
        tp = round(w_completed / w_started, 2) if w_started > 0 else None
        result.append({"date": d_str, "wip": wip, "throughput_7d": tp})
    return result


def daily_pull_rate(days: int = 28, epic: str | None = None) -> list[dict]:
    """Per-day 7-day rolling pull rate (started ÷ created) for the trend chart.

    Returns [{"date": str, "pull_rate_7d": float | None}, ...] oldest first.
    pull_rate_7d = started / created over trailing 7 days; None when no creates.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days + 7)).isoformat()
    join = ""
    epic_clause = ""
    sql_params: list = [cutoff]
    if epic is not None:
        join = "JOIN cards c ON c.id = t.card_id"
        epic_clause = " AND c.epic = ?"
        sql_params.append(epic)
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT date(t.at) AS day, t.from_col, t.to_col, count(*) AS cnt
            FROM transitions t {join}
            WHERE t.at >= ?{epic_clause}
            GROUP BY date(t.at), t.from_col, t.to_col
            """,
            sql_params,
        ).fetchall()
    daily_created: dict[str, int] = {}
    daily_started: dict[str, int] = {}
    for r in rows:
        day = r["day"]
        if r["from_col"] is None:  # card entering board = created
            daily_created[day] = daily_created.get(day, 0) + r["cnt"]
        if r["to_col"] == "in_progress":
            daily_started[day] = daily_started.get(day, 0) + r["cnt"]

    today = datetime.now(timezone.utc).date()
    result = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        d_str = str(d)
        w_created = sum(daily_created.get(str(d - timedelta(days=j)), 0) for j in range(7))
        w_started = sum(daily_started.get(str(d - timedelta(days=j)), 0) for j in range(7))
        pr = round(w_started / w_created, 2) if w_created > 0 else None
        result.append({"date": d_str, "pull_rate_7d": pr})
    return result


def cycle_time_rolling(days: int = 84, window: int = 7, epic: str | None = None) -> list[dict]:
    """Daily rolling-window median / P25 / P75 of days-to-complete.

    For each day in the last *days* days, aggregates all cards whose
    done_at falls within the trailing *window* days (inclusive). Returns
    oldest-first: [{"date": "YYYY-MM-DD", "median": float|None,
    "p25": float|None, "p75": float|None, "count": int}, ...]
    """
    from datetime import datetime, timedelta, timezone

    now    = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days + window)).isoformat()

    epic_clause = " AND c.epic = ?" if epic is not None else ""
    params: list = [cutoff]
    if epic is not None:
        params.append(epic)

    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT c.created_at, t.at AS done_at
            FROM transitions t
            JOIN cards c ON c.id = t.card_id
            WHERE t.to_col = 'done'
              AND t.at >= ?
              AND c.created_at != ''{epic_clause}
            """,
            params,
        ).fetchall()

    def _percentile(sorted_vals: list[float], p: float) -> float:
        if not sorted_vals:
            return 0.0
        idx = p * (len(sorted_vals) - 1)
        lo, hi = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
        return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)

    # index completed cards by done date
    by_date: dict[str, list[float]] = {}
    for row in rows:
        try:
            done    = datetime.fromisoformat(row["done_at"].replace("Z", "+00:00"))
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if done.tzinfo is None:
                done = done.replace(tzinfo=timezone.utc)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            day  = str(done.date())
            days_taken = max(0.0, (done - created).total_seconds() / 86400)
            by_date.setdefault(day, []).append(days_taken)
        except (ValueError, AttributeError):
            continue

    today  = now.date()
    result = []
    for i in range(days - 1, -1, -1):
        d      = today - timedelta(days=i)
        window_vals: list[float] = []
        for j in range(window):
            window_vals.extend(by_date.get(str(d - timedelta(days=j)), []))
        cts = sorted(window_vals)
        if cts:
            n      = len(cts)
            mid    = n // 2
            median = cts[mid] if n % 2 else (cts[mid - 1] + cts[mid]) / 2
            result.append({
                "date":   str(d),
                "median": round(median, 2),
                "p25":    round(_percentile(cts, 0.25), 2),
                "p75":    round(_percentile(cts, 0.75), 2),
                "count":  n,
            })
        else:
            result.append({"date": str(d), "median": None, "p25": None, "p75": None, "count": 0})
    return result


def cycle_time_weekly(weeks: int = 12, epic: str | None = None) -> list[dict]:
    """Per-ISO-week median / P25 / P75 days-to-complete for cards done that week.

    Days = done_at − created_at for every card that transitioned to 'done'
    in the given Monday–Sunday week. Returns oldest-first:
    [{"week_start": "YYYY-MM-DD", "median": float|None,
      "p25": float|None, "p75": float|None, "count": int}, ...]
    """
    from datetime import datetime, timedelta, timezone

    now    = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=weeks * 7)).isoformat()

    epic_clause = " AND c.epic = ?" if epic is not None else ""
    params: list = [cutoff]
    if epic is not None:
        params.append(epic)

    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT c.created_at, t.at AS done_at
            FROM transitions t
            JOIN cards c ON c.id = t.card_id
            WHERE t.to_col = 'done'
              AND t.at >= ?
              AND c.created_at != ''{epic_clause}
            """,
            params,
        ).fetchall()

    def _percentile(sorted_vals: list[float], p: float) -> float:
        if not sorted_vals:
            return 0.0
        idx = p * (len(sorted_vals) - 1)
        lo, hi = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
        return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)

    # bucket by ISO-week start (Monday of the done_at date)
    by_week: dict[str, list[float]] = {}
    for row in rows:
        try:
            done    = datetime.fromisoformat(row["done_at"].replace("Z", "+00:00"))
            created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if done.tzinfo is None:
                done = done.replace(tzinfo=timezone.utc)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            days   = max(0.0, (done - created).total_seconds() / 86400)
            monday = str(done.date() - timedelta(days=done.weekday()))
            by_week.setdefault(monday, []).append(days)
        except (ValueError, AttributeError):
            continue

    # build result for the last *weeks* weeks, oldest first
    today       = now.date()
    this_monday = today - timedelta(days=today.weekday())
    result      = []
    for w in range(weeks - 1, -1, -1):
        monday = this_monday - timedelta(weeks=w)
        cts    = sorted(by_week.get(str(monday), []))
        if cts:
            n      = len(cts)
            mid    = n // 2
            median = cts[mid] if n % 2 else (cts[mid - 1] + cts[mid]) / 2
            result.append({
                "week_start": str(monday),
                "median": round(median, 2),
                "p25":    round(_percentile(cts, 0.25), 2),
                "p75":    round(_percentile(cts, 0.75), 2),
                "count":  n,
            })
        else:
            result.append({
                "week_start": str(monday),
                "median": None,
                "p25":    None,
                "p75":    None,
                "count":  0,
            })
    return result


def cycle_time_trend(days: int = 28, epic: str | None = None) -> list[dict]:
    """Per-day 7-day rolling average cycle time for the trend chart.

    Returns [{"date": str, "avg_ct_7d": float | None}, ...] oldest first.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days + 7)).isoformat()
    join = ""
    epic_clause = ""
    params: list = [cutoff]
    if epic is not None:
        join = "JOIN cards c ON c.id = t.card_id"
        epic_clause = " AND c.epic = ?"
        params.append(epic)
    with _conn() as con:
        rows = con.execute(
            f"""
            WITH done_events AS (
                SELECT t.card_id, t.at AS done_at
                FROM transitions t {join}
                WHERE t.to_col = 'done' AND t.at >= ?{epic_clause}
            ),
            cycle_pairs AS (
                SELECT d.card_id, d.done_at, MAX(s.at) AS started_at
                FROM done_events d
                JOIN transitions s ON s.card_id = d.card_id
                WHERE s.to_col = 'in_progress' AND s.at < d.done_at
                GROUP BY d.card_id, d.done_at
            )
            SELECT date(done_at) AS day, done_at, started_at FROM cycle_pairs
            """,
            params,
        ).fetchall()
    by_date: dict[str, list[float]] = {}
    for row in rows:
        day = row["day"]
        try:
            done = datetime.fromisoformat(row["done_at"].replace("Z", "+00:00"))
            start = datetime.fromisoformat(row["started_at"].replace("Z", "+00:00"))
            if done.tzinfo is None:
                done = done.replace(tzinfo=timezone.utc)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            ct = (done - start).total_seconds() / 86400
            by_date.setdefault(day, []).append(max(ct, 0.0))
        except (ValueError, AttributeError):
            continue
    today = datetime.now(timezone.utc).date()
    result = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        d_str = str(d)
        window: list[float] = []
        for j in range(7):
            window.extend(by_date.get(str(d - timedelta(days=j)), []))
        avg_ct = round(sum(window) / len(window), 2) if window else None
        result.append({"date": d_str, "avg_ct_7d": avg_ct})
    return result

