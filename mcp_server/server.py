from __future__ import annotations

import os
from pathlib import Path

from fastmcp import FastMCP

from board import epics as _epics
from board import operations

mcp = FastMCP(
    "kanbai",
    instructions=(
        "Kanbai is an agent-compatible Kanban board. "
        "Use the tools to manage cards across lanes (backlog, in_progress, review, done). "
        "Call list_docs to discover available documentation (kanban principles, flow metrics, etc.) "
        "and get_doc to read the full content of any doc. "
        "Always consult the kanban principles before making board decisions."
    ),
)

# --- Access tiers -----------------------------------------------------------
# KANBAI_MCP_ACCESS controls which tools external agents may call:
#   read  -> read-only tools only (list/get/summary)
#   write -> read + create/update/move/archive/link/epics (default)
#   yolo  -> write + permanent deletion (delete_card)
# Tools above the configured tier are never registered, so a jailbroken prompt
# cannot reach them — they simply aren't offered to the client.
_TIER_RANK = {"read": 0, "write": 1, "yolo": 2}

_ACCESS = os.environ.get("KANBAI_MCP_ACCESS", "write").strip().lower()
if _ACCESS not in _TIER_RANK:
    _ACCESS = "write"


def tool(tier: str = "read"):
    """Register an MCP tool only if the configured access tier permits it."""
    def decorator(fn):
        if _TIER_RANK[tier] <= _TIER_RANK[_ACCESS]:
            return mcp.tool()(fn)
        return fn  # not registered for this access tier
    return decorator


@tool("write")
def create_card(
    title: str,
    lane: str,
    description: str = "",
    tags: list[str] | None = None,
    priority: str = "medium",
    epic: str | None = None,
    due_date: str | None = None,
) -> dict:
    """Create a new card on the Kanban board. lane must be one of: backlog, in_progress, review, done. due_date is an optional YYYY-MM-DD deadline."""
    card = operations.create_card(title, lane, description, tags, priority, epic=epic, due_date=due_date)
    return {"id": card.id, "number": card.number, "title": card.title, "lane": card.lane, "epic": card.epic, "due_date": card.due_date}


@tool("read")
def list_cards(
    lane: str | None = None,
    tags: list[str] | None = None,
    archived: bool = False,
    query: str | None = None,
    epic: str | None = None,
) -> list[dict]:
    """List cards, optionally filtered by lane, tags, archived status, full-text query, or epic. Pass epic=None for no filter; 'Unassigned' for cards with no epic."""
    from board.models import _UNSET
    if epic is None:
        return operations.list_cards(lane=lane, tags=tags, archived=archived, query=query)
    epic_val = None if epic == "Unassigned" else epic
    return operations.list_cards(lane=lane, tags=tags, archived=archived, query=query, epic=epic_val)


@tool("read")
def get_card(card_id: str) -> dict | None:
    """Get full details of a single card by its ID."""
    card = operations.get_card(card_id)
    if not card:
        return None
    return {
        "id": card.id,
        "number": card.number,
        "title": card.title,
        "lane": card.lane,
        "priority": card.priority,
        "tags": card.tags,
        "description": card.description,
        "created_at": card.created_at,
        "updated_at": card.updated_at,
        "archived": card.archived,
        "epic": card.epic,
        "links": card.links,
        "attachments": card.attachments,
        "due_date": card.due_date,
    }


@tool("write")
def update_card(
    card_id: str,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    priority: str | None = None,
    epic: str | None = None,
    set_epic: bool = False,
    due_date: str | None = None,
    set_due_date: bool = False,
) -> dict | None:
    """Update card fields. Only provided fields are changed. To set epic, pass set_epic=True and the desired epic value (or None for Unassigned). To set the deadline, pass set_due_date=True and due_date as YYYY-MM-DD (or None to clear)."""
    from board.models import _UNSET
    epic_arg = epic if set_epic else _UNSET
    due_arg = due_date if set_due_date else _UNSET
    card = operations.update_card(card_id, title, description, tags, priority, epic=epic_arg, due_date=due_arg)
    if not card:
        return None
    return {"id": card.id, "title": card.title, "lane": card.lane, "updated_at": card.updated_at, "due_date": card.due_date}


@tool("write")
def move_card(card_id: str, target_lane: str) -> dict | None:
    """Move a card to a different lane."""
    card = operations.move_card(card_id, target_lane)
    if not card:
        return None
    return {"id": card.id, "lane": card.lane}


@tool("write")
def archive_card(card_id: str) -> dict | None:
    """Archive a card (soft-delete — stays in git history, hidden from default views)."""
    card = operations.archive_card(card_id)
    if not card:
        return None
    return {"id": card.id, "archived": card.archived}


@tool("write")
def unarchive_card(card_id: str) -> dict | None:
    """Restore an archived card back to the board."""
    card = operations.unarchive_card(card_id)
    if not card:
        return None
    return {"id": card.id, "archived": card.archived}


@tool("yolo")
def delete_card(card_id: str) -> dict:
    """Permanently delete a card. The card MUST be archived first. Irreversible."""
    ok = operations.delete_card(card_id)
    return {"deleted": ok, "error": None if ok else "not_found_or_not_archived"}


@tool("read")
def list_epics(include_archived: bool = False) -> list[str]:
    """List epic names on the board."""
    return _epics.list_epics(include_archived=include_archived)


@tool("write")
def set_card_epic(card_number: int, epic: str | None = None) -> dict:
    """Assign an epic to a card by its task number. Pass epic=None for Unassigned."""
    from board.storage import sqlite_cache as _sc
    row = _sc.get_card_by_number(card_number)
    if not row:
        return {"error": f"Card #{card_number} not found"}
    card = operations.update_card(row["id"], epic=epic)
    return {"updated": bool(card)}


@tool("write")
def archive_epic(epic_name: str) -> dict:
    """Archive an epic and all its active cards."""
    count = operations.archive_epic_op(epic_name)
    return {"archived_cards": count}


@tool("write")
def unarchive_epic(epic_name: str) -> dict:
    """Restore an archived epic and its snapshotted cards."""
    count = operations.unarchive_epic_op(epic_name)
    return {"restored_cards": count}


@tool("write")
def link_cards(from_number: int, to_number: int, link_type: str) -> dict:
    """Create a typed link between two cards. link_type: blocks, blocked_by, parent_of, child_of, duplicate, relates_to."""
    err = operations.link_cards(from_number, to_number, link_type)
    return {"ok": err is None, "error": err}


@tool("write")
def unlink_cards(from_number: int, to_number: int, link_type: str) -> dict:
    """Remove a typed link between two cards (also removes the inverse link)."""
    err = operations.unlink_cards(from_number, to_number, link_type)
    return {"ok": err is None, "error": err}


@tool("read")
def project_health(epic: str | None = None) -> dict:
    """Return overall project health (good/watch/bad) plus the 8 A-series signals. Filter by epic or omit for board-wide."""
    from board.health import project_health as _ph
    return _ph(epic=epic)


@tool("read")
def health_model() -> str:
    """Return the full health model documentation as Markdown — thresholds, definitions, motivations, and design principles."""
    from board.health_doc import health_model_doc
    return health_model_doc()


@tool("read")
def get_board_summary(epic: str | None = None) -> dict:
    """Return aggregated analytics: counts by lane/priority, avg age, top tags, CFD (90d), completion heatmap (this year). Filter by epic or omit for board-wide."""
    import datetime
    from board.storage import sqlite_cache as _sc
    by_lane = _sc.count_cards_by_lane(epic=epic)
    by_priority = _sc.count_cards_by_priority(epic=epic)
    total = sum(by_lane.values())
    done = by_lane.get("done", 0)
    return {
        "by_lane": by_lane,
        "by_priority": by_priority,
        "total": total,
        "done": done,
        "completion_pct": round(done / total * 100) if total else 0,
        "avg_age_days": _sc.avg_card_age_days(epic=epic),
        "top_tags": _sc.count_cards_by_tag(10, epic=epic),
        "cfd_last_90_days": _sc.cfd_data(90, epic=epic),
        "completion_heatmap_this_year": _sc.done_cards_heatmap(datetime.date.today().year, epic=epic),
        "_metric_definitions": _sc.METRIC_DEFINITIONS,
    }


_DOCS_DIR = Path(__file__).parent.parent / "docs"

_DOC_META: dict[str, str] = {
    "mcp": "MCP server setup, available tools, and access tier documentation.",
}


@tool("read")
def list_docs() -> list[dict]:
    """List the documentation files available on this Kanbai server. Returns name and description for each doc. Use get_doc(name) to read full content."""
    return [
        {"name": name, "description": desc}
        for name, desc in _DOC_META.items()
        if (_DOCS_DIR / f"{name}.md").exists()
    ]


@tool("read")
def get_doc(name: str) -> str:
    """Read a Kanbai documentation file by name. Use list_docs() to see available names. Returns the full Markdown content."""
    safe_name = Path(name).name  # strip any path traversal
    path = _DOCS_DIR / f"{safe_name}.md"
    if not path.exists():
        available = ", ".join(_DOC_META.keys())
        return f"Doc '{name}' not found. Available docs: {available}"
    return path.read_text(encoding="utf-8")


def run_mcp_server() -> None:
    mcp.run(transport="stdio")
