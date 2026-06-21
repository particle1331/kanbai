"""CLI card/board management commands for kanbai."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer

from kanbai.cli import app, require_kanbai_root, _set_project_env


def _ensure_project() -> None:
    """Set up the project environment and sync the board."""
    root = require_kanbai_root()
    _set_project_env(root)
    from kanbai.board.storage.sync import full_sync
    full_sync()


# ─── Card commands ──────────────────────────────────────────────────────────────


@app.command()
def add(
    title: str = typer.Argument(..., help="Card title."),
    priority: str = typer.Option("medium", "--priority", "-p", help="Priority: low, medium, high, critical."),
    tags: Optional[str] = typer.Option(None, "--tags", "-t", help="Comma-separated tags."),
    epic: str = typer.Option(..., "--epic", "-e", help="Epic name (required)."),
    description: str = typer.Option("", "--desc", "-d", help="Card description."),
    due_date: Optional[str] = typer.Option(None, "--due", help="Due date (YYYY-MM-DD)."),
) -> None:
    """Create a new card on the board."""
    _ensure_project()
    from kanbai.board import operations

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    try:
        card = operations.create_card(
            title=title,
            lane="backlog",
            description=description,
            tags=tag_list,
            priority=priority,
            epic=epic,
            due_date=due_date,
        )
    except (ValueError, Exception) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"✓ Created #{card.number}: {card.title} [{card.lane}]")
    if card.epic:
        typer.echo(f"  Epic: {card.epic}")


@app.command()
def ls(
    lane: Optional[str] = typer.Option(None, "--lane", "-l", help="Filter by lane."),
    epic: Optional[str] = typer.Option(None, "--epic", "-e", help="Filter by epic."),
    tag: Optional[str] = typer.Option(None, "--tag", "-t", help="Filter by tag."),
    archived: bool = typer.Option(False, "--archived", "-a", help="Show archived cards."),
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Full-text search."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max cards to show."),
) -> None:
    """List cards on the board."""
    _ensure_project()
    from kanbai.board import operations
    from kanbai.board.models import LANE_LABELS

    from kanbai.board.models import _UNSET

    tags_filter = [tag] if tag else None
    epic_filter = epic if epic is not None else _UNSET
    cards = operations.list_cards(
        lane=lane,
        tags=tags_filter,
        archived=archived,
        query=query,
        epic=epic_filter,
    )

    if not cards:
        typer.echo("No cards found.")
        return

    # Sort by lane order then number
    lane_order = {"backlog": 0, "in_progress": 1, "review": 2, "done": 3}
    cards.sort(key=lambda c: (lane_order.get(c.get("lane", ""), 99), -(c.get("number") or 0)))

    displayed = cards[:limit]
    
    # Table output
    typer.echo(f"{'#':<6} {'Lane':<14} {'Pri':<8} {'Title':<50} {'Epic':<20}")
    typer.echo("─" * 98)
    for c in displayed:
        num = f"#{c.get('number', '?')}"
        lane_label = (LANE_LABELS.get(c.get("lane", "")) or c.get("lane") or "")[:12]
        pri = (c.get("priority") or "medium")[:6]
        title_str = (c.get("title") or "")[:48]
        epic_str = (c.get("epic") or "—")[:18]
        typer.echo(f"{num:<6} {lane_label:<14} {pri:<8} {title_str:<50} {epic_str:<20}")

    if len(cards) > limit:
        typer.echo(f"\n… and {len(cards) - limit} more (use --limit to show more)")
    else:
        typer.echo(f"\n{len(cards)} card(s)")


@app.command()
def show(
    number: int = typer.Argument(..., help="Card number (e.g. 42)."),
) -> None:
    """Show full details of a card."""
    _ensure_project()
    from kanbai.board.storage import sqlite_cache as _sc
    from kanbai.board import operations

    row = _sc.get_card_by_number(number)
    if not row:
        typer.echo(f"Error: Card #{number} not found.", err=True)
        raise typer.Exit(1)

    card = operations.get_card(row["id"])
    if not card:
        typer.echo(f"Error: Card #{number} not found.", err=True)
        raise typer.Exit(1)

    typer.echo(f"#{card.number} — {card.title}")
    typer.echo(f"{'─' * 60}")
    typer.echo(f"  Lane:     {card.lane}")
    typer.echo(f"  Priority: {card.priority}")
    typer.echo(f"  Epic:     {card.epic or '—'}")
    typer.echo(f"  Tags:     {', '.join(card.tags) if card.tags else '—'}")
    typer.echo(f"  Due:      {card.due_date or '—'}")
    typer.echo(f"  Created:  {card.created_at}")
    typer.echo(f"  Updated:  {card.updated_at}")
    typer.echo(f"  Archived: {card.archived}")
    if card.links:
        typer.echo(f"  Links:")
        for link in card.links:
            typer.echo(f"    {link.get('type', '?')} → #{link.get('number', '?')}")
    if card.description:
        typer.echo(f"\n  Description:\n    {card.description[:500]}")
    if card.comments:
        typer.echo(f"\n  Comments ({len(card.comments)}):")
        for c in card.comments[-5:]:
            typer.echo(f"    [{c.get('created_at', '')[:10]}] {c.get('text', '')[:80]}")


@app.command()
def move(
    number: int = typer.Argument(..., help="Card number."),
    lane: str = typer.Argument(..., help="Target lane: backlog, in_progress, review, done."),
) -> None:
    """Move a card to a different lane."""
    _ensure_project()
    from kanbai.board.storage import sqlite_cache as _sc
    from kanbai.board import operations

    row = _sc.get_card_by_number(number)
    if not row:
        typer.echo(f"Error: Card #{number} not found.", err=True)
        raise typer.Exit(1)

    card = operations.move_card(row["id"], lane)
    if not card:
        typer.echo(f"Error: Could not move card #{number}.", err=True)
        raise typer.Exit(1)

    typer.echo(f"✓ #{card.number} moved to {card.lane}")


@app.command()
def edit(
    number: int = typer.Argument(..., help="Card number."),
    title: Optional[str] = typer.Option(None, "--title", help="New title."),
    description: Optional[str] = typer.Option(None, "--desc", "-d", help="New description."),
    priority: Optional[str] = typer.Option(None, "--priority", "-p", help="New priority."),
    tags: Optional[str] = typer.Option(None, "--tags", "-t", help="Replace tags (comma-separated)."),
    epic: Optional[str] = typer.Option(None, "--epic", "-e", help="Set epic (use 'none' to clear)."),
    due_date: Optional[str] = typer.Option(None, "--due", help="Set due date (YYYY-MM-DD, 'none' to clear)."),
) -> None:
    """Update a card's fields."""
    _ensure_project()
    from kanbai.board.storage import sqlite_cache as _sc
    from kanbai.board import operations
    from kanbai.board.models import _UNSET

    row = _sc.get_card_by_number(number)
    if not row:
        typer.echo(f"Error: Card #{number} not found.", err=True)
        raise typer.Exit(1)

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags is not None else None

    # Handle epic: 'none' → clear, otherwise set
    epic_arg = _UNSET
    if epic is not None:
        epic_arg = None if epic.lower() == "none" else epic

    # Handle due_date: 'none' → clear
    due_arg = _UNSET
    if due_date is not None:
        due_arg = None if due_date.lower() == "none" else due_date

    try:
        card = operations.update_card(
            row["id"],
            title=title,
            description=description,
            tags=tag_list,
            priority=priority,
            epic=epic_arg,
            due_date=due_arg,
        )
    except (ValueError, Exception) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if not card:
        typer.echo(f"Error: Could not update card #{number}.", err=True)
        raise typer.Exit(1)

    typer.echo(f"✓ #{card.number} updated: {card.title} [{card.lane}]")


@app.command()
def archive(
    number: int = typer.Argument(..., help="Card number to archive."),
) -> None:
    """Archive a card (soft-delete)."""
    _ensure_project()
    from kanbai.board.storage import sqlite_cache as _sc
    from kanbai.board import operations

    row = _sc.get_card_by_number(number)
    if not row:
        typer.echo(f"Error: Card #{number} not found.", err=True)
        raise typer.Exit(1)

    card = operations.archive_card(row["id"])
    if not card:
        typer.echo(f"Error: Could not archive card #{number}.", err=True)
        raise typer.Exit(1)

    typer.echo(f"✓ #{card.number} archived")


@app.command()
def unarchive(
    number: int = typer.Argument(..., help="Card number to restore."),
) -> None:
    """Restore an archived card."""
    _ensure_project()
    from kanbai.board.storage import sqlite_cache as _sc
    from kanbai.board import operations

    row = _sc.get_card_by_number(number)
    if not row:
        typer.echo(f"Error: Card #{number} not found.", err=True)
        raise typer.Exit(1)

    card = operations.unarchive_card(row["id"])
    if not card:
        typer.echo(f"Error: Could not unarchive card #{number}.", err=True)
        raise typer.Exit(1)

    typer.echo(f"✓ #{card.number} restored to {card.lane}")


@app.command()
def comment(
    number: int = typer.Argument(..., help="Card number."),
    text: str = typer.Argument(..., help="Comment text."),
) -> None:
    """Add a comment to a card."""
    _ensure_project()
    from kanbai.board.storage import sqlite_cache as _sc
    from kanbai.board import operations

    row = _sc.get_card_by_number(number)
    if not row:
        typer.echo(f"Error: Card #{number} not found.", err=True)
        raise typer.Exit(1)

    try:
        card = operations.add_comment(row["id"], text)
    except ValueError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)

    if not card:
        typer.echo(f"Error: Could not add comment.", err=True)
        raise typer.Exit(1)

    typer.echo(f"✓ Comment added to #{number}")


@app.command()
def link(
    from_number: int = typer.Argument(..., help="Source card number."),
    to_number: int = typer.Argument(..., help="Target card number."),
    link_type: str = typer.Argument(..., help="Link type: blocks, blocked_by, parent_of, child_of, duplicate, relates_to."),
) -> None:
    """Create a typed link between two cards."""
    _ensure_project()
    from kanbai.board import operations

    err = operations.link_cards(from_number, to_number, link_type)
    if err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(1)

    typer.echo(f"✓ #{from_number} —[{link_type}]→ #{to_number}")


@app.command()
def unlink(
    from_number: int = typer.Argument(..., help="Source card number."),
    to_number: int = typer.Argument(..., help="Target card number."),
    link_type: str = typer.Argument(..., help="Link type to remove."),
) -> None:
    """Remove a link between two cards."""
    _ensure_project()
    from kanbai.board import operations

    err = operations.unlink_cards(from_number, to_number, link_type)
    if err:
        typer.echo(f"Error: {err}", err=True)
        raise typer.Exit(1)

    typer.echo(f"✓ Removed {link_type} link between #{from_number} and #{to_number}")


# ─── Epic commands ──────────────────────────────────────────────────────────────

epic_app = typer.Typer(help="Manage epics.")
app.add_typer(epic_app, name="epic")


@epic_app.command("list")
def epic_list(
    include_archived: bool = typer.Option(False, "--archived", "-a", help="Include archived epics."),
) -> None:
    """List all epics."""
    _ensure_project()
    from kanbai.board import epics as _epics

    names = _epics.list_epics(include_archived=include_archived)
    if not names:
        typer.echo("No epics found.")
        return

    for name in names:
        detail = _epics.get_epic_detail(name)
        status = " [archived]" if detail and detail.get("archived") else ""
        typer.echo(f"  • {name}{status}")


@epic_app.command("create")
def epic_create(
    name: str = typer.Argument(..., help="Epic name."),
    description: str = typer.Option("", "--desc", "-d", help="Epic description."),
) -> None:
    """Create a new epic."""
    _ensure_project()
    from kanbai.board import epics as _epics

    if _epics.epic_exists(name):
        typer.echo(f"Error: Epic '{name}' already exists.", err=True)
        raise typer.Exit(1)

    _epics.create_epic(name, description)
    typer.echo(f"✓ Created epic: {name}")


@epic_app.command("archive")
def epic_archive(
    name: str = typer.Argument(..., help="Epic name to archive."),
) -> None:
    """Archive an epic and all its cards."""
    _ensure_project()
    from kanbai.board import operations

    count = operations.archive_epic_op(name)
    typer.echo(f"✓ Archived epic '{name}' ({count} cards archived)")


@epic_app.command("unarchive")
def epic_unarchive(
    name: str = typer.Argument(..., help="Epic name to restore."),
) -> None:
    """Restore an archived epic and its cards."""
    _ensure_project()
    from kanbai.board import operations

    count = operations.unarchive_epic_op(name)
    typer.echo(f"✓ Restored epic '{name}' ({count} cards restored)")


# ─── Board summary commands ─────────────────────────────────────────────────────


@app.command()
def status(
    epic: Optional[str] = typer.Option(None, "--epic", "-e", help="Filter by epic."),
) -> None:
    """Show board summary: card counts, health status, WIP."""
    _ensure_project()
    from kanbai.board.storage import sqlite_cache as _sc
    from kanbai.board.health import project_health
    from kanbai.board.models import LANE_LABELS

    by_lane = _sc.count_cards_by_lane(epic=epic)
    total = sum(by_lane.values())
    h = project_health(epic=epic)

    typer.echo(f"Board Status" + (f" (epic: {epic})" if epic else ""))
    typer.echo("─" * 40)
    for lane_key, label in LANE_LABELS.items():
        count = by_lane.get(lane_key, 0)
        bar = "█" * min(count, 30)
        typer.echo(f"  {label:<14} {count:>4}  {bar}")
    typer.echo(f"  {'Total':<14} {total:>4}")

    typer.echo(f"\nHealth: {h['label']} ({h['status']})")
    if h.get("warnings"):
        for w in h["warnings"]:
            icon = "⚠" if w["band"] == "watch" else "✖"
            typer.echo(f"  {icon} {w['label']}: {w['message']}")


@app.command()
def health(
    epic: Optional[str] = typer.Option(None, "--epic", "-e", help="Filter by epic."),
) -> None:
    """Show detailed health signals."""
    _ensure_project()
    from kanbai.board.health import project_health

    h = project_health(epic=epic)

    typer.echo(f"Health: {h['label']} ({h['status']})")
    typer.echo("─" * 50)

    for signal in h.get("signals", []):
        band = signal.get("band", "good")
        icon = {"good": "✓", "watch": "⚠", "bad": "✖"}.get(band, "?")
        label = signal.get("label", "")
        message = signal.get("message", "")
        typer.echo(f"  {icon} {label:<24} {message}")


@app.command()
def sync() -> None:
    """Commit and push .kanbai/ changes to git."""
    _ensure_project()
    from kanbai.board.storage import git_ops
    from kanbai.board import settings as _s

    if not _s._load().get("git_tracking", True):
        typer.echo("Git tracking is disabled for this project.")
        raise typer.Exit(0)

    if not git_ops.is_git_repo():
        typer.echo("Error: Not inside a git repository.", err=True)
        raise typer.Exit(1)

    if not git_ops.is_dirty():
        typer.echo("Nothing to commit — board is clean.")
        return

    ok, msg = git_ops.sync_push()
    if ok:
        typer.echo(f"✓ {msg}")
    else:
        typer.echo(f"✖ {msg}", err=True)
        raise typer.Exit(1)


@app.command()
def config(
    key: Optional[str] = typer.Argument(None, help="Setting key to get/set."),
    value: Optional[str] = typer.Argument(None, help="Value to set (omit to read)."),
) -> None:
    """View or update board settings."""
    _ensure_project()
    import json as _json
    from kanbai.paths import SETTINGS_PATH

    data = _json.loads(SETTINGS_PATH.read_text()) if SETTINGS_PATH.exists() else {}

    if key is None:
        # Show all settings
        typer.echo("Board settings:")
        for k, v in sorted(data.items()):
            typer.echo(f"  {k} = {v}")
        return

    if value is None:
        # Read a single key
        if key in data:
            typer.echo(f"{key} = {data[key]}")
        else:
            typer.echo(f"Key '{key}' not set.", err=True)
            raise typer.Exit(1)
        return

    # Set a value (auto-convert types)
    if value.lower() == "true":
        data[key] = True
    elif value.lower() == "false":
        data[key] = False
    elif value.isdigit():
        data[key] = int(value)
    else:
        try:
            data[key] = float(value)
        except ValueError:
            data[key] = value

    SETTINGS_PATH.write_text(_json.dumps(data, indent=2))
    typer.echo(f"✓ {key} = {data[key]}")
