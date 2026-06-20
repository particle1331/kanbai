"""Validation limits for card fields.

All hard limits are enforced here and called from operations.py before any
write, so they apply uniformly to the UI, the embedded chat agent, and
external MCP callers.

Raising ``ValueError`` with a human-readable message lets callers (including
MCP tool handlers) surface the reason without catching separate exception types.
"""
from __future__ import annotations

# ── Hard limits ────────────────────────────────────────────────────────────────

MAX_TITLE       = 200
MAX_DESCRIPTION = 10_000
MAX_TAGS        = 6
MAX_TAG_LEN     = 50
MAX_ASSIGNEES   = 10
MAX_ASSIGNEE_LEN = 100
MAX_LINKS       = 20
MAX_COMMENTS    = 200
MAX_COMMENT_LEN = 4_000
MAX_EPIC_LEN    = 100
MAX_CARDS_PER_EPIC = 500  # active cards per epic (Unassigned counts as one bucket)
MAX_ATTACHMENTS = 20
MAX_ATTACHMENT_SIZE_MB = 100
BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".wsf",
    ".sh", ".bash", ".zsh",
    ".msi", ".app", ".dmg", ".pkg", ".deb", ".rpm",
    ".bin", ".com", ".scr", ".pif",
}

# ── Soft-warning thresholds (checked separately by the UI) ────────────────────

WARN_DESCRIPTION = 5_000
WARN_TAGS        = 4
WARN_LINKS       = 10


# ── Validators ────────────────────────────────────────────────────────────────

def validate_card_fields(
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    assigned_to: list[str] | None = None,
    links: list[dict] | None = None,
    epic: str | None = None,
) -> None:
    """Raise ``ValueError`` with a human-readable message on any hard-limit violation."""
    if title is not None:
        t = title.strip()
        if not t:
            raise ValueError("title: must not be empty.")
        if len(t) > MAX_TITLE:
            raise ValueError(
                f"title: maximum {MAX_TITLE} characters allowed (got {len(t)})."
            )

    if description is not None and len(description) > MAX_DESCRIPTION:
        raise ValueError(
            f"description: maximum {MAX_DESCRIPTION} characters allowed "
            f"(got {len(description)})."
        )

    if tags is not None:
        if len(tags) > MAX_TAGS:
            raise ValueError(
                f"tags: maximum {MAX_TAGS} tags allowed (got {len(tags)})."
            )
        for tag in tags:
            t = tag.strip()
            if not t:
                raise ValueError("tags: tag must not be empty.")
            if len(t) > MAX_TAG_LEN:
                raise ValueError(
                    f"tags: each tag must be ≤ {MAX_TAG_LEN} characters "
                    f"('{t[:20]}…' has {len(t)})."
                )

    if assigned_to is not None:
        if len(assigned_to) > MAX_ASSIGNEES:
            raise ValueError(
                f"assigned_to: maximum {MAX_ASSIGNEES} assignees allowed "
                f"(got {len(assigned_to)})."
            )
        for name in assigned_to:
            n = name.strip()
            if not n:
                raise ValueError("assigned_to: assignee name must not be empty.")
            if len(n) > MAX_ASSIGNEE_LEN:
                raise ValueError(
                    f"assigned_to: each name must be ≤ {MAX_ASSIGNEE_LEN} characters "
                    f"('{n[:20]}…' has {len(n)})."
                )

    if links is not None and len(links) > MAX_LINKS:
        raise ValueError(
            f"links: maximum {MAX_LINKS} relationships per card allowed "
            f"(got {len(links)})."
        )

    if epic is not None and len(epic) > MAX_EPIC_LEN:
        raise ValueError(
            f"epic: maximum {MAX_EPIC_LEN} characters allowed (got {len(epic)})."
        )


def validate_due_date(due_date: str | None) -> None:
    """Raise ``ValueError`` unless *due_date* is None/empty or a valid YYYY-MM-DD date."""
    if not due_date:
        return
    import datetime as _dt
    try:
        _dt.date.fromisoformat(due_date)
    except ValueError:
        raise ValueError(
            f"due_date: must be a valid date in YYYY-MM-DD format (got '{due_date}')."
        )


def validate_comment(text: str, current_count: int) -> None:
    """Raise ``ValueError`` if the comment text or card comment count exceeds hard limits."""
    t = (text or "").strip()
    if not t:
        raise ValueError("comment: text must not be empty.")
    if len(t) > MAX_COMMENT_LEN:
        raise ValueError(
            f"comment: maximum {MAX_COMMENT_LEN} characters allowed (got {len(t)})."
        )
    if current_count >= MAX_COMMENTS:
        raise ValueError(
            f"comments: maximum {MAX_COMMENTS} comments per card reached."
        )


def validate_attachment(src_path: str, current_count: int) -> None:
    """Raise ``ValueError`` if the attachment violates hard limits."""
    import os
    from pathlib import Path
    p = Path(src_path)
    if not p.exists():
        raise ValueError(f"File not found: {src_path}")
    if current_count >= MAX_ATTACHMENTS:
        raise ValueError(
            f"attachments: maximum {MAX_ATTACHMENTS} attachments per card."
        )
    size_mb = p.stat().st_size / (1024 * 1024)
    if size_mb > MAX_ATTACHMENT_SIZE_MB:
        raise ValueError(
            f"File is too large ({size_mb:.1f} MB). Maximum is {MAX_ATTACHMENT_SIZE_MB} MB."
        )
    ext = p.suffix.lower()
    if ext in BLOCKED_EXTENSIONS:
        raise ValueError(
            f"File type '{ext}' is not allowed for security reasons."
        )


def soft_warnings(
    description: str | None = None,
    tags: list[str] | None = None,
    links: list[dict] | None = None,
) -> list[str]:
    """Return a list of soft-warning strings (non-blocking, shown in the UI only).

    Returns an empty list when everything is within comfortable bounds.
    """
    warnings: list[str] = []
    if description is not None and len(description) > WARN_DESCRIPTION:
        warnings.append(
            "Description is long — consider splitting into sub-tasks."
        )
    if tags is not None and len(tags) > WARN_TAGS:
        warnings.append(
            "Lots of tags — consider using an epic instead."
        )
    if links is not None and len(links) > WARN_LINKS:
        warnings.append(
            "Many relationships — check for duplicate or redundant links."
        )
    return warnings
