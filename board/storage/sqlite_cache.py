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
    """Non-archived cards that block at least one other card.

    Each returned dict gains a ``block_count`` field (number of outgoing
    ``blocks`` links), sorted by block_count desc then most recently updated.
    """
    return _link_cards("blocks", "block_count", n, epic)


def count_blocker_cards(epic: str | None = None) -> int:
    """Count non-archived cards that block at least one non-archived card.

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
            WHERE archived = 0 AND links LIKE '%"blocks"%'{clause}
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


def _link_cards(link_type: str, count_field: str, n: int, epic: str | None) -> list[dict]:
    """Return non-archived cards holding at least one link of *link_type*.

    Only links whose target card is itself non-archived are counted, so the
    numbers reconcile across the blocker/blocked views. Each returned dict
    gains *count_field* (number of matching live links), sorted by that count
    desc then most recently updated.
    """
    clause = " AND epic = ?" if epic is not None else ""
    params = (epic,) if epic is not None else ()
    with _conn() as con:
        rows = con.execute(
            f"""
            SELECT id, number, title, updated_at, priority, lane, epic, links
            FROM cards
            WHERE archived = 0 AND links LIKE '%"{link_type}"%'{clause}
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

