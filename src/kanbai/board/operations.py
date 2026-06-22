from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from kanbai.board import counter as _counter
from kanbai.board import epics as _epics
from kanbai.board import validation as _validation
from kanbai.board.models import Card, _UNSET
from kanbai.board.storage import event_log, markdown_store, sqlite_cache
from kanbai.paths import BOARD_DIR, ATTACHMENTS_DIR

LINK_INVERSES: dict[str, str] = {
    "blocks": "blocked_by",
    "blocked_by": "blocks",
    "parent_of": "child_of",
    "child_of": "parent_of",
    "duplicate": "duplicate",
    "relates_to": "relates_to",
}
VALID_LINK_TYPES = frozenset(LINK_INVERSES)


class EpicArchivedError(ValueError):
    """Raised when an active card would be placed in an archived epic.

    An archived epic must contain only archived cards, so active cards may
    only be assigned to active epics.
    """


class EpicNotFoundError(ValueError):
    """Raised when a card references an epic that does not exist.

    Epics must be created explicitly (via ``create_epic``) before cards can be
    assigned to them, so callers cannot conjure an epic by naming it on a card.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedup_ci(names: list[str] | None) -> list[str]:
    """Strip, drop blanks, and dedupe names case-insensitively (first casing wins)."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in names or []:
        name = raw.strip()
        if not name:
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def create_card(
    title: str,
    lane: str,
    description: str = "",
    tags: list[str] | None = None,
    priority: str = "medium",
    epic: str | None = None,
    assigned_to: list[str] | None = None,
    due_date: str | None = None,
) -> Card:
    _validation.validate_card_fields(
        title=title,
        description=description,
        tags=tags,
        assigned_to=assigned_to,
        epic=epic,
    )
    _validation.validate_due_date(due_date)
    if epic and not _epics.epic_exists(epic):
        raise EpicNotFoundError(
            f"Epic '{epic}' does not exist — create it first with create_epic."
        )
    if epic and _epics.is_epic_archived(epic):
        raise EpicArchivedError(
            f"Epic '{epic}' is archived — unarchive it before adding cards."
        )
    _bucket = epic or "Unassigned"
    if sqlite_cache.count_cards_in_epic(epic) >= _validation.MAX_CARDS_PER_EPIC:
        raise ValueError(
            f"'{_bucket}' already has the maximum of "
            f"{_validation.MAX_CARDS_PER_EPIC} active cards."
        )
    card = Card(
        id=str(uuid.uuid4()),
        title=title,
        lane=lane,
        description=description,
        tags=tags or [],
        priority=priority,
        created_at=_now(),
        updated_at=_now(),
        epic=epic,
        number=_counter.next_number(),
        assigned_to=_dedup_ci(assigned_to),
        due_date=due_date or None,
    )
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    event_log.record_transition(card.id, None, card.lane, at=card.created_at)
    return card


def get_card(card_id: str) -> Card | None:
    return markdown_store.read_card(card_id)


def list_cards(
    lane: str | None = None,
    tags: list[str] | None = None,
    archived: bool | None = False,
    query: str | None = None,
    epic=_UNSET,
) -> list[dict]:
    return sqlite_cache.query_cards(
        lane=lane, tags=tags, archived=archived, query=query, epic=epic
    )


def update_card(
    card_id: str,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    priority: str | None = None,
    epic=_UNSET,
    assigned_to: list[str] | None = None,
    due_date=_UNSET,
) -> Card | None:
    from kanbai.board.models import _Unset as _UnsetT  # local import avoids private-export lint
    _validation.validate_card_fields(
        title=title,
        description=description,
        tags=tags,
        assigned_to=assigned_to,
        epic=None if isinstance(epic, _UnsetT) else epic,  # type: ignore[arg-type]
    )
    if not isinstance(due_date, _UnsetT):
        _validation.validate_due_date(due_date)  # type: ignore[arg-type]
    card = markdown_store.read_card(card_id)
    if not card:
        return None
    if not isinstance(epic, _UnsetT) and epic and not _epics.epic_exists(epic):  # type: ignore[arg-type]
        raise EpicNotFoundError(
            f"Epic '{epic}' does not exist — create it first with create_epic."
        )
    if not isinstance(epic, _UnsetT) and epic and not card.archived and _epics.is_epic_archived(epic):  # type: ignore[arg-type]
        raise EpicArchivedError(
            f"Epic '{epic}' is archived — unarchive it before assigning active cards."
        )
    if not isinstance(epic, _UnsetT) and not card.archived and (epic or None) != (card.epic or None):
        _bucket = epic or "Unassigned"
        if sqlite_cache.count_cards_in_epic(epic or None) >= _validation.MAX_CARDS_PER_EPIC:  # type: ignore[arg-type]
            raise ValueError(
                f"'{_bucket}' already has the maximum of "
                f"{_validation.MAX_CARDS_PER_EPIC} active cards."
            )
    if title is not None:
        card.title = title
    if description is not None:
        card.description = description
    if tags is not None:
        card.tags = tags
    if priority is not None:
        card.priority = priority
    if not isinstance(epic, _UnsetT):
        card.epic = epic  # type: ignore[assignment]
    if assigned_to is not None:
        card.assigned_to = _dedup_ci(assigned_to)
    if not isinstance(due_date, _UnsetT):
        card.due_date = due_date or None  # type: ignore[assignment]
    card.updated_at = _now()
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    return card


def add_comment(card_id: str, text: str, role: str = "note") -> Card | None:
    """Append a timestamped comment (with an optional role) to a card."""
    text = (text or "").strip()
    if not text:
        return None
    card = markdown_store.read_card(card_id)
    if not card:
        return None
    _validation.validate_comment(text, current_count=len(card.comments))
    _validation.validate_comment_role(role)
    card.comments.append({"id": str(uuid.uuid4()), "text": text, "created_at": _now(), "role": role})
    card.updated_at = _now()
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    return card


def delete_comment(card_id: str, comment_id: str) -> Card | None:
    """Remove a comment by its id. Returns the card (unchanged if not found)."""
    card = markdown_store.read_card(card_id)
    if not card:
        return None
    before = len(card.comments)
    card.comments = [c for c in card.comments if c.get("id") != comment_id]
    if len(card.comments) != before:
        card.updated_at = _now()
        markdown_store.write_card(card)
        sqlite_cache.upsert_card(card)
    return card


# ── Checklist operations ──────────────────────────────────────────────────────

def add_checklist_item(
    card_id: str,
    text: str,
    position: int | None = None,
) -> Card | None:
    """Add a checklist item to a card, optionally at a specific 0-based position."""
    text = (text or "").strip()
    if not text:
        return None
    card = markdown_store.read_card(card_id)
    if not card:
        return None
    _validation.validate_checklist_item(text, current_count=len(card.checklist))
    item = {"text": text, "done": False}
    if position is not None and 0 <= position <= len(card.checklist):
        card.checklist.insert(position, item)
    else:
        card.checklist.append(item)
    card.updated_at = _now()
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    return card


def toggle_checklist_item(card_id: str, index: int) -> Card | None:
    """Toggle the done/undone state of a checklist item by its 0-based index."""
    card = markdown_store.read_card(card_id)
    if not card:
        return None
    if index < 0 or index >= len(card.checklist):
        return None
    card.checklist[index]["done"] = not card.checklist[index].get("done", False)
    card.updated_at = _now()
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    return card


def remove_checklist_item(card_id: str, index: int) -> Card | None:
    """Remove a checklist item by its 0-based index."""
    card = markdown_store.read_card(card_id)
    if not card:
        return None
    if index < 0 or index >= len(card.checklist):
        return None
    card.checklist.pop(index)
    card.updated_at = _now()
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    return card


def move_card(card_id: str, target_lane: str) -> Card | None:
    card = markdown_store.read_card(card_id)
    if not card:
        return None
    old_lane = card.lane
    card.lane = target_lane
    card.updated_at = _now()
    markdown_store.move_card_file(card, old_lane)
    sqlite_cache.upsert_card(card)
    event_log.record_transition(card.id, old_lane, target_lane, at=card.updated_at)
    return card


def reorder_cards(target_lane: str, ordered_ids: list[str]) -> None:
    """Set the top-to-bottom order of cards within ``target_lane``.

    ``ordered_ids`` is the desired order; any card not already in
    ``target_lane`` is moved into it. Positions are renumbered 0..n.
    Only cards whose lane or position actually changed are rewritten.
    """
    for index, cid in enumerate(ordered_ids):
        card = markdown_store.read_card(cid)
        if not card:
            continue
        new_pos = float(index)
        lane_changed = card.lane != target_lane
        pos_changed = card.position != new_pos
        if not lane_changed and not pos_changed:
            continue
        if lane_changed:
            old_lane = card.lane
            card.lane = target_lane
            card.position = new_pos
            card.updated_at = _now()
            markdown_store.move_card_file(card, old_lane)
            sqlite_cache.upsert_card(card)
            event_log.record_transition(card.id, old_lane, target_lane, at=card.updated_at)
        else:
            card.position = new_pos
            markdown_store.write_card(card)
            sqlite_cache.upsert_card(card)


def archive_card(card_id: str) -> Card | None:
    card = markdown_store.read_card(card_id)
    if not card:
        return None
    card.archived = True
    card.updated_at = _now()
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    return card


def unarchive_card(card_id: str) -> Card | None:
    card = markdown_store.read_card(card_id)
    if not card:
        return None
    if card.epic and _epics.is_epic_archived(card.epic):
        raise EpicArchivedError(
            f"This card's epic '{card.epic}' is archived — unarchive the epic first."
        )
    _bucket = card.epic or "Unassigned"
    if sqlite_cache.count_cards_in_epic(card.epic or None) >= _validation.MAX_CARDS_PER_EPIC:
        raise ValueError(
            f"'{_bucket}' already has the maximum of "
            f"{_validation.MAX_CARDS_PER_EPIC} active cards — cannot restore this card."
        )
    card.archived = False
    card.updated_at = _now()
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    return card


def archive_epic_op(epic_name: str) -> int:
    """Archive an epic and all its active cards. Returns count of archived cards."""
    rows = sqlite_cache.query_cards(epic=epic_name, archived=False)
    archived_ids: list[str] = []
    for row in rows:
        card = markdown_store.read_card(row["id"])
        if card:
            card.archived = True
            card.updated_at = _now()
            markdown_store.write_card(card)
            sqlite_cache.upsert_card(card)
            archived_ids.append(card.id)
    _epics.archive_epic(epic_name, archived_ids)
    return len(archived_ids)


def unarchive_epic_op(epic_name: str) -> int:
    """Unarchive an epic and its snapshotted cards. Returns count of restored cards."""
    card_ids = _epics.unarchive_epic(epic_name)
    count = 0
    for card_id in card_ids:
        card = markdown_store.read_card(card_id)
        if card:
            card.archived = False
            card.updated_at = _now()
            markdown_store.write_card(card)
            sqlite_cache.upsert_card(card)
            count += 1
    return count


def delete_card(card_id: str) -> bool:
    """Permanently delete a card. Returns False if not found."""
    card = markdown_store.read_card(card_id)
    if not card:
        return False
    # Remove inverse links from all linked cards
    for lk in card.links:
        target_row = sqlite_cache.get_card_by_number(lk["number"])
        if target_row:
            target = markdown_store.read_card(target_row["id"])
            if target:
                inverse = LINK_INVERSES.get(lk["type"], lk["type"])
                before = len(target.links)
                target.links = [
                    l for l in target.links
                    if not (l["type"] == inverse and l["number"] == card.number)
                ]
                if len(target.links) != before:
                    target.updated_at = _now()
                    markdown_store.write_card(target)
                    sqlite_cache.upsert_card(target)
    # Delete markdown file and cache row.
    card_path = markdown_store.BOARD_DIR / card.lane / f"{card.id}.md"
    if card_path.exists():
        card_path.unlink()
    sqlite_cache.delete_card(card_id)
    return True


def link_cards(from_number: int, to_number: int, link_type: str) -> str | None:
    """Add a typed link between two cards (auto-inverse). Returns error string or None on success."""
    if link_type not in VALID_LINK_TYPES:
        return f"Invalid link type '{link_type}'. Valid: {sorted(VALID_LINK_TYPES)}"
    if from_number == to_number:
        return "Cannot link a card to itself."
    from_row = sqlite_cache.get_card_by_number(from_number)
    to_row = sqlite_cache.get_card_by_number(to_number)
    if not from_row:
        return f"Card #{from_number} not found."
    if not to_row:
        return f"Card #{to_number} not found."
    from_card = markdown_store.read_card(from_row["id"])
    to_card = markdown_store.read_card(to_row["id"])
    if not from_card or not to_card:
        return "Card file not found."
    if any(l["type"] == link_type and l["number"] == to_number for l in from_card.links):
        return f"Link already exists: #{from_number} {link_type} #{to_number}."
    # Check link count limit on both cards before writing
    if len(from_card.links) >= _validation.MAX_LINKS:
        return f"Card #{from_number} already has the maximum {_validation.MAX_LINKS} relationships."
    if len(to_card.links) >= _validation.MAX_LINKS:
        return f"Card #{to_number} already has the maximum {_validation.MAX_LINKS} relationships."
    inverse = LINK_INVERSES[link_type]
    from_card.links.append({"type": link_type, "number": to_number})
    from_card.updated_at = _now()
    to_card.links.append({"type": inverse, "number": from_number})
    to_card.updated_at = _now()
    markdown_store.write_card(from_card)
    sqlite_cache.upsert_card(from_card)
    markdown_store.write_card(to_card)
    sqlite_cache.upsert_card(to_card)
    return None


def unlink_cards(from_number: int, to_number: int, link_type: str) -> str | None:
    """Remove a typed link between two cards (auto-removes inverse). Returns error string or None on success."""
    if link_type not in VALID_LINK_TYPES:
        return f"Invalid link type '{link_type}'."
    from_row = sqlite_cache.get_card_by_number(from_number)
    to_row = sqlite_cache.get_card_by_number(to_number)
    if not from_row:
        return f"Card #{from_number} not found."
    if not to_row:
        return f"Card #{to_number} not found."
    from_card = markdown_store.read_card(from_row["id"])
    to_card = markdown_store.read_card(to_row["id"])
    if not from_card or not to_card:
        return "Card file not found."
    before = len(from_card.links)
    inverse = LINK_INVERSES[link_type]
    from_card.links = [
        l for l in from_card.links
        if not (l["type"] == link_type and l["number"] == to_number)
    ]
    if len(from_card.links) == before:
        return f"Link #{from_number} {link_type} #{to_number} not found."
    to_card.links = [
        l for l in to_card.links
        if not (l["type"] == inverse and l["number"] == from_number)
    ]
    from_card.updated_at = _now()
    to_card.updated_at = _now()
    markdown_store.write_card(from_card)
    sqlite_cache.upsert_card(from_card)
    markdown_store.write_card(to_card)
    sqlite_cache.upsert_card(to_card)
    return None


# ── Attachments ──────────────────────────────────────────────────────────────

def _sanitize_filename(name: str) -> str:
    """Strip unsafe characters from a filename, preserving the extension."""
    # Replace path separators, commas (used as delimiter in SQLite), and null bytes
    name = re.sub(r'[/\\,\x00]', '_', name)
    return name[:200]


def get_attachment_dir(card_id: str) -> Path:
    """Return the directory where a card's attachments are stored."""
    return ATTACHMENTS_DIR / card_id


def get_attachment_path(card_id: str, filename: str) -> Path:
    """Return the full path of a specific attachment file."""
    return get_attachment_dir(card_id) / filename


def get_attachments_total_size_bytes() -> int:
    """Return the total size in bytes of every file under the attachments directory."""
    total = 0
    if ATTACHMENTS_DIR.exists():
        for p in ATTACHMENTS_DIR.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    return total


def add_attachment(card_id: str, src_path: str) -> str | None:
    """Copy *src_path* into the card's attachment directory.

    Returns an error string on failure, or ``None`` on success.
    """
    card = markdown_store.read_card(card_id)
    if not card:
        return "Card not found."
    try:
        _validation.validate_attachment(src_path, len(card.attachments))
    except ValueError as ex:
        return str(ex)

    src = Path(src_path)
    new_bytes = src.stat().st_size
    used_bytes = get_attachments_total_size_bytes()
    if (used_bytes + new_bytes) / (1024 * 1024) > _validation.MAX_TOTAL_ATTACHMENTS_MB:
        used_mb = used_bytes / (1024 * 1024)
        return (
            f"Board-wide attachment storage limit reached "
            f"({used_mb:.1f} MB / {_validation.MAX_TOTAL_ATTACHMENTS_MB} MB used). "
            f"Run Cleanup in Settings to free space from done cards."
        )

    filename = _sanitize_filename(src.name)

    att_dir = get_attachment_dir(card_id)
    att_dir.mkdir(parents=True, exist_ok=True)
    dest = att_dir / filename

    # Avoid overwriting an existing attachment by appending a counter.
    if dest.exists():
        stem, suffix = src.stem, src.suffix
        for i in range(1, 1000):
            candidate = att_dir / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                dest = candidate
                filename = dest.name
                break
        else:
            return "Could not find a unique filename for the attachment."

    shutil.copy2(src_path, str(dest))
    card.attachments.append(filename)
    card.updated_at = _now()
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    return None


def add_attachment_bytes(card_id: str, filename: str, data: bytes) -> str | None:
    """Write *data* as an attachment without requiring a local file path.

    Used in web mode (``page.web = True``) where ``FilePicker`` returns file
    bytes instead of a server-side path.

    Returns an error string on failure, or ``None`` on success.
    """
    card = markdown_store.read_card(card_id)
    if not card:
        return "Card not found."

    # Reuse the extension-block and count limits from validate_attachment,
    # skipping the existence / size checks (we already have the bytes).
    from pathlib import Path as _Path
    p = _Path(filename)
    ext = p.suffix.lower()
    if ext in _validation.BLOCKED_EXTENSIONS:
        return f"File type '{ext}' is not allowed for security reasons."
    if len(card.attachments) >= _validation.MAX_ATTACHMENTS:
        return f"attachments: maximum {_validation.MAX_ATTACHMENTS} attachments per card."
    size_mb = len(data) / (1024 * 1024)
    if size_mb > _validation.MAX_ATTACHMENT_SIZE_MB:
        return (
            f"File is too large ({size_mb:.1f} MB). "
            f"Maximum is {_validation.MAX_ATTACHMENT_SIZE_MB} MB."
        )

    used_bytes = get_attachments_total_size_bytes()
    if (used_bytes + len(data)) / (1024 * 1024) > _validation.MAX_TOTAL_ATTACHMENTS_MB:
        used_mb = used_bytes / (1024 * 1024)
        return (
            f"Board-wide attachment storage limit reached "
            f"({used_mb:.1f} MB / {_validation.MAX_TOTAL_ATTACHMENTS_MB} MB used). "
            f"Run Cleanup in Settings to free space from done cards."
        )

    safe_name = _sanitize_filename(filename)
    att_dir = get_attachment_dir(card_id)
    att_dir.mkdir(parents=True, exist_ok=True)
    dest = att_dir / safe_name

    if dest.exists():
        stem, suffix = _Path(safe_name).stem, _Path(safe_name).suffix
        for i in range(1, 1000):
            candidate = att_dir / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                dest = candidate
                safe_name = dest.name
                break
        else:
            return "Could not find a unique filename for the attachment."

    dest.write_bytes(data)
    card.attachments.append(safe_name)
    card.updated_at = _now()
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    return None


def remove_attachment(card_id: str, filename: str) -> str | None:
    """Delete an attachment file and remove it from the card's metadata.

    Returns an error string on failure, or ``None`` on success.
    """
    card = markdown_store.read_card(card_id)
    if not card:
        return "Card not found."
    if filename not in card.attachments:
        return f"Attachment '{filename}' not found on card."

    att_path = get_attachment_path(card_id, filename)
    if att_path.exists():
        att_path.unlink()

    card.attachments = [a for a in card.attachments if a != filename]
    card.updated_at = _now()
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    return None


# ── File references ───────────────────────────────────────────────────────────

def add_file_ref(card_id: str, path: str) -> str | None:
    """Append a file reference to a card.

    *path* is a relative file path with an optional line range suffix:
    ``"src/foo.py"`` or ``"src/foo.py:10-30"``.  Returns an error string on
    failure, or ``None`` on success.
    """
    card = markdown_store.read_card(card_id)
    if not card:
        return "Card not found."
    path = (path or "").strip()
    try:
        _validation.validate_file_ref(path, len(card.file_refs))
    except ValueError as ex:
        return str(ex)
    # Avoid exact duplicates.
    if path in card.file_refs:
        return None  # idempotent — not an error
    card.file_refs.append(path)
    card.updated_at = _now()
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    return None


def remove_file_ref(card_id: str, path: str) -> str | None:
    """Remove a file reference from a card by its exact path string.

    Returns an error string on failure, or ``None`` on success.
    """
    card = markdown_store.read_card(card_id)
    if not card:
        return "Card not found."
    if path not in card.file_refs:
        return f"File reference '{path}' not found on card."
    card.file_refs = [r for r in card.file_refs if r != path]
    card.updated_at = _now()
    markdown_store.write_card(card)
    sqlite_cache.upsert_card(card)
    return None


def cleanup_attachments(dry_run: bool = True, days: int = 90) -> dict:
    """Find (and optionally remove) attachments from done cards idle for *days* days.

    A card is eligible when it is currently in the ``done`` lane (non-archived)
    and its ``updated_at`` timestamp predates the cutoff (now − *days*).

    Parameters
    ----------
    dry_run:
        When ``True`` (default) nothing is deleted; the return value describes
        what *would* be removed.  Pass ``False`` to actually delete the files
        and update card metadata.
    days:
        Number of days of inactivity after which a done card's attachments are
        eligible.  Defaults to 90.

    Returns a dict with keys:
      dry_run         – mirrors the argument
      eligible_cards  – number of done cards that have eligible attachments
      files           – total number of attachment files eligible
      size_mb         – combined size in MB (rounded to 2 d.p.)
      cards           – per-card detail list (id, number, title, updated_at, files)
    """
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = sqlite_cache.query_cards(lane="done", archived=False)
    eligible: list[dict] = []
    total_files = 0
    total_bytes = 0
    for row in rows:
        updated = row.get("updated_at") or ""
        if not updated or updated >= cutoff:
            continue
        card = markdown_store.read_card(row["id"])
        if not card or not card.attachments:
            continue
        card_files = []
        for fname in list(card.attachments):
            att_path = get_attachment_path(card.id, fname)
            if att_path.exists():
                try:
                    size = att_path.stat().st_size
                except OSError:
                    size = 0
                card_files.append({"filename": fname, "size_bytes": size})
                total_files += 1
                total_bytes += size
        if card_files:
            eligible.append({
                "card_id": card.id,
                "card_number": card.number,
                "title": card.title,
                "updated_at": card.updated_at,
                "files": card_files,
            })
    if not dry_run:
        for card_info in eligible:
            for file_info in card_info["files"]:
                remove_attachment(card_info["card_id"], file_info["filename"])
    return {
        "dry_run": dry_run,
        "eligible_cards": len(eligible),
        "files": total_files,
        "size_mb": round(total_bytes / (1024 * 1024), 2),
        "cards": eligible,
    }


# ── Relationship graph ────────────────────────────────────────────────────────

def epic_graph(epic_name: str) -> dict:
    """Return nodes+edges for the graph view of an epic."""
    rows = sqlite_cache.query_cards(epic=epic_name, archived=False)
    nodes = [
        {
            "id": r["number"],
            "title": r["title"],
            "lane": r["lane"],
            "priority": r["priority"],
        }
        for r in rows
        if r["number"] is not None
    ]
    card_numbers = {n["id"] for n in nodes}
    seen_edges: set[frozenset] = set()
    edges = []
    for r in rows:
        for lk in (r.get("links") or []):
            a, b, t = r["number"], lk["number"], lk["type"]
            if a is None or b not in card_numbers:
                continue
            key = frozenset([a, b, t, LINK_INVERSES.get(t, t)])
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append({"from": a, "to": b, "type": t})
    return {"nodes": nodes, "edges": edges}
