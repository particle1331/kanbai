from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable

from fastmcp import FastMCP
from fastmcp.tools.function_tool import FunctionTool

from kanbai.board import epics as _epics
from kanbai.board import operations
from kanbai.board.agent_rules import SHARED_RULES

# ── Direct board-refresh hook ────────────────────────────────────────────────
# When the MCP server runs in the same process as the Flet UI (kanbai serve),
# this callback lets write tools immediately refresh the board without waiting
# for the file-watcher debounce (~1 second delay).
_board_refresh_callback: Callable[[], None] | None = None


def register_board_refresh(callback: Callable[[], None]) -> None:
    """Register a callback to be called after any board-mutating MCP tool.

    Called by the Flet app so that in-process MCP mutations (SSE transport)
    trigger an immediate UI refresh instead of waiting ~1 s for the watcher.
    The callback is run in a daemon thread so it never blocks the MCP response.
    """
    global _board_refresh_callback
    _board_refresh_callback = callback


def _notify_board_changed() -> None:
    """Fire the registered refresh callback (if any) in a background thread."""
    cb = _board_refresh_callback
    if cb is None:
        return
    threading.Thread(target=cb, daemon=True).start()


mcp = FastMCP(
    "kanbai",
    instructions=(
        "Kanbai is an agent-compatible Kanban board. "
        "Use the tools to manage cards across lanes (backlog, in_progress, review, done). "
        "Call list_docs to discover available documentation (kanban principles, flow metrics, etc.) "
        "and get_doc to read the full content of any doc. "
        "Always consult the kanban principles before making board decisions.\n\n"
        + SHARED_RULES
    ),
)

# --- Access tiers -----------------------------------------------------------
# KANBAI_MCP_ACCESS controls which tools external MCP clients may call:
#   read  -> read-only tools only (list/get/summary)
#   write -> read + create/update/move/archive/link/epics (default)
#   yolo  -> write + permanent deletion (delete_card)
# Tools above the configured tier are never registered with the mcp transport,
# so a jailbroken prompt cannot reach them from external clients.
#
# Kanbot (the in-process chat agent) bypasses this and calls tools directly
# via call_tool_sync() with its own mode-based tier filtering.
_TIER_RANK = {"read": 0, "write": 1, "yolo": 2}

_ACCESS = os.environ.get("KANBAI_MCP_ACCESS", "write").strip().lower()
if _ACCESS not in _TIER_RANK:
    _ACCESS = "write"

# Registry of every tool: name -> {tier, fn, schema}
# Populated by the @tool decorator regardless of _ACCESS, so Kanbot can
# always reach all tools when its mode permits it.
_TOOL_REGISTRY: dict[str, dict] = {}


def tool(tier: str = "read"):
    """Register a tool with an access tier.

    External MCP transport: only tools with tier <= _ACCESS are registered.
    Kanbot chat agent: uses _TOOL_REGISTRY directly and filters by its mode.
    """
    def decorator(fn):
        ft = FunctionTool.from_function(fn)
        _TOOL_REGISTRY[fn.__name__] = {
            "tier": tier,
            "fn": fn,
            "schema": {
                "name": ft.name,
                "description": ft.description or "",
                "parameters": ft.parameters,
            },
        }
        if _TIER_RANK[tier] <= _TIER_RANK[_ACCESS]:
            return mcp.tool()(fn)
        return fn
    return decorator


def get_openai_tools(max_tier: str = "write") -> list[dict]:
    """Return OpenAI-format tool definitions for all tools up to *max_tier*.

    Used by the Kanbot chat agent to build its tool list based on the current
    mode (ask/plan -> read, agent -> write, yolo -> yolo).
    """
    rank = _TIER_RANK.get(max_tier, 1)
    return [
        {"type": "function", "function": info["schema"]}
        for info in _TOOL_REGISTRY.values()
        if _TIER_RANK[info["tier"]] <= rank
    ]


def call_tool_sync(name: str, args: dict) -> str:
    """Call a registered board tool by name and return a JSON string.

    Called directly by the Kanbot chat agent -- no MCP transport involved.
    Returns a JSON error string if the tool name is not found.
    """
    info = _TOOL_REGISTRY.get(name)
    if info is None:
        return json.dumps({"error": f"unknown tool: {name}"})
    try:
        result = info["fn"](**args)
        return result if isinstance(result, str) else json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("write")
def create_card(
    title: str,
    lane: str,
    epic: str,
    description: str = "",
    tags: list[str] | None = None,
    priority: str = "medium",
    due_date: str | None = None,
) -> dict:
    """Create a new card on the Kanban board.

    lane must be one of: backlog, in_progress, review, done.
    epic is required and must already exist — call list_epics to see available
    epics. An unknown or archived epic returns {"error": "..."}.
    due_date is an optional YYYY-MM-DD deadline.

    Tags: always include one type tag — 'bugfix', 'feature', or 'enhancement' —
    plus 1-2 additional tags derived from the title, description, and epic.
    """
    from kanbai.board.operations import EpicNotFoundError, EpicArchivedError
    try:
        card = operations.create_card(title, lane, description, tags, priority, epic=epic, due_date=due_date)
    except EpicNotFoundError as exc:
        available = ", ".join(_epics.list_epics()) or "(none yet)"
        return {"error": str(exc), "available_epics": available}
    except EpicArchivedError as exc:
        return {"error": str(exc)}
    except ValueError as exc:
        return {"error": str(exc)}
    _notify_board_changed()
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
    from kanbai.board.models import _UNSET
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
    checklist = card.checklist or []
    done_count = sum(1 for item in checklist if item.get("done"))
    total_count = len(checklist)
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
        "comments": card.comments,
        "checklist": [{"index": i, "text": item["text"], "done": item.get("done", False)} for i, item in enumerate(checklist)],
        "checklist_completion": f"{done_count}/{total_count}",
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
    from kanbai.board.models import _UNSET
    epic_arg = epic if set_epic else _UNSET
    due_arg = due_date if set_due_date else _UNSET
    card = operations.update_card(card_id, title, description, tags, priority, epic=epic_arg, due_date=due_arg)
    if not card:
        return None
    _notify_board_changed()
    return {"id": card.id, "title": card.title, "lane": card.lane, "updated_at": card.updated_at, "due_date": card.due_date}


@tool("write")
def move_card(card_id: str, target_lane: str) -> dict | None:
    """Move a card to a different lane."""
    card = operations.move_card(card_id, target_lane)
    if not card:
        return None
    _notify_board_changed()
    return {"id": card.id, "lane": card.lane}


@tool("write")
def archive_card(card_id: str) -> dict | None:
    """Archive a card (soft-delete — stays in git history, hidden from default views)."""
    card = operations.archive_card(card_id)
    if not card:
        return None
    _notify_board_changed()
    return {"id": card.id, "archived": card.archived}


@tool("write")
def unarchive_card(card_id: str) -> dict | None:
    """Restore an archived card back to the board."""
    card = operations.unarchive_card(card_id)
    if not card:
        return None
    _notify_board_changed()
    return {"id": card.id, "archived": card.archived}


@tool("yolo")
def delete_card(card_id: str) -> dict:
    """Permanently delete a card. Irreversible (git history is the fallback)."""
    ok = operations.delete_card(card_id)
    if ok:
        _notify_board_changed()
    return {"deleted": ok, "error": None if ok else "not_found_or_not_archived"}


@tool("read")
def list_epics(include_archived: bool = False) -> list[str]:
    """List epic names on the board."""
    return _epics.list_epics(include_archived=include_archived)


@tool("write")
def set_card_epic(card_number: int, epic: str | None = None) -> dict:
    """Assign an epic to a card by its task number. Pass epic=None for Unassigned."""
    from kanbai.board.storage import sqlite_cache as _sc
    row = _sc.get_card_by_number(card_number)
    if not row:
        return {"error": f"Card #{card_number} not found"}
    card = operations.update_card(row["id"], epic=epic)
    if card:
        _notify_board_changed()
    return {"updated": bool(card)}


@tool("write")
def archive_epic(epic_name: str) -> dict:
    """Archive an epic and all its active cards."""
    count = operations.archive_epic_op(epic_name)
    _notify_board_changed()
    return {"archived_cards": count}


@tool("write")
def unarchive_epic(epic_name: str) -> dict:
    """Restore an archived epic and its snapshotted cards."""
    count = operations.unarchive_epic_op(epic_name)
    _notify_board_changed()
    return {"restored_cards": count}


@tool("write")
def create_epic(name: str, description: str = "") -> dict:
    """Create a new epic with an optional description (markdown supported)."""
    if _epics.epic_exists(name):
        return {"error": f"Epic '{name}' already exists."}
    _epics.create_epic(name, description)
    _notify_board_changed()
    return {"created": name}


@tool("read")
def get_epic(epic_name: str) -> dict | None:
    """Get full details of an epic: name, archived status, description, and comments."""
    return _epics.get_epic_detail(epic_name)


@tool("write")
def set_epic_description(epic_name: str, description: str) -> dict:
    """Set or replace the markdown description of an epic. Use this to document goals, context, and commentary for the epic."""
    try:
        ok = _epics.set_epic_description(epic_name, description)
    except ValueError as ex:
        return {"error": str(ex)}
    if not ok:
        return {"error": f"Epic '{epic_name}' not found."}
    _notify_board_changed()
    return {"updated": True}


@tool("write")
def add_epic_comment(epic_name: str, text: str) -> dict:
    """Add a comment to an epic. Use this to log progress notes, decisions, or status updates about the epic."""
    try:
        comment = _epics.add_epic_comment(epic_name, text)
    except ValueError as ex:
        return {"error": str(ex)}
    if comment is None:
        return {"error": f"Epic '{epic_name}' not found."}
    _notify_board_changed()
    return comment


@tool("write")
def delete_epic_comment(epic_name: str, comment_id: str) -> dict:
    """Delete a comment from an epic by its comment ID."""
    ok = _epics.delete_epic_comment(epic_name, comment_id)
    if ok:
        _notify_board_changed()
    return {"deleted": ok}


@tool("write")
def add_card_comment(card_id: str, text: str, role: str = "note") -> dict:
    """Add a typed comment to a card.

    role controls the comment's purpose and display colour:
      • note        — general remark (default, grey)
      • instruction — human tells agent what to do (blue)
      • update      — agent reports progress (grey)
      • decision    — records a choice made (green)
      • blocker     — flags something needing human input (red)
      • question    — agent asks for clarification (orange)
    """
    try:
        card = operations.add_comment(card_id, text, role=role)
    except ValueError as ex:
        return {"error": str(ex)}
    if card is None:
        return {"error": f"Card '{card_id}' not found."}
    _notify_board_changed()
    return card.comments[-1]


@tool("write")
def delete_card_comment(card_id: str, comment_id: str) -> dict:
    """Delete a comment from a card by its comment ID."""
    card = operations.delete_comment(card_id, comment_id)
    if card is not None:
        _notify_board_changed()
    return {"deleted": card is not None}


# ── Checklist tools ────────────────────────────────────────────────────────────────

@tool("write")
def add_checklist_item(card_id: str, text: str, position: int | None = None) -> dict:
    """Add an item to a card's checklist. Optionally insert at a 0-based position; omit to append."""
    try:
        card = operations.add_checklist_item(card_id, text, position=position)
    except ValueError as ex:
        return {"error": str(ex)}
    if card is None:
        return {"error": f"Card '{card_id}' not found."}
    _notify_board_changed()
    idx = len(card.checklist) - 1 if position is None else position
    return {"index": idx, "text": card.checklist[idx]["text"], "done": card.checklist[idx]["done"]}


@tool("write")
def toggle_checklist_item(card_id: str, item_index: int) -> dict:
    """Toggle the done/undone state of a checklist item by its 0-based index."""
    card = operations.toggle_checklist_item(card_id, item_index)
    if card is None:
        return {"error": f"Card '{card_id}' not found or index out of range."}
    _notify_board_changed()
    item = card.checklist[item_index]
    return {"index": item_index, "text": item["text"], "done": item.get("done", False)}


@tool("write")
def remove_checklist_item(card_id: str, item_index: int) -> dict:
    """Remove a checklist item by its 0-based index."""
    card = operations.remove_checklist_item(card_id, item_index)
    if card is None:
        return {"error": f"Card '{card_id}' not found or index out of range."}
    _notify_board_changed()
    return {"deleted": True, "remaining": len(card.checklist)}


@tool("read")
def get_checklist(card_id: str) -> list[dict] | dict:
    """Return all checklist items for a card with their index, text, done state, and overall completion stats."""
    card = operations.get_card(card_id)
    if card is None:
        return {"error": f"Card '{card_id}' not found."}
    checklist = card.checklist or []
    done = sum(1 for item in checklist if item.get("done"))
    return {
        "items": [{"index": i, "text": item["text"], "done": item.get("done", False)} for i, item in enumerate(checklist)],
        "completion": f"{done}/{len(checklist)}",
    }


@tool("write")
def link_cards(from_number: int, to_number: int, link_type: str) -> dict:
    """Create a typed link between two cards. link_type: blocks, blocked_by, parent_of, child_of, duplicate, relates_to."""
    err = operations.link_cards(from_number, to_number, link_type)
    if err is None:
        _notify_board_changed()
    return {"ok": err is None, "error": err}


@tool("write")
def unlink_cards(from_number: int, to_number: int, link_type: str) -> dict:
    """Remove a typed link between two cards (also removes the inverse link)."""
    err = operations.unlink_cards(from_number, to_number, link_type)
    if err is None:
        _notify_board_changed()
    return {"ok": err is None, "error": err}


@tool("read")
def project_health(epic: str | None = None) -> dict:
    """Return overall project health (good/watch/bad) plus the 8 A-series signals. Filter by epic or omit for board-wide."""
    from kanbai.board.health import project_health as _ph
    return _ph(epic=epic)


@tool("read")
def health_model() -> str:
    """Return the full health model documentation as Markdown — thresholds, definitions, motivations, and design principles."""
    from kanbai.board.health_doc import health_model_doc
    return health_model_doc()


@tool("read")
def get_board_summary(epic: str | None = None) -> dict:
    """Return aggregated analytics: counts by lane/priority, avg age, top tags, CFD (90d), completion heatmap (this year). Filter by epic or omit for board-wide."""
    import datetime
    from kanbai.board.storage import sqlite_cache as _sc
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
