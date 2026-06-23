"""Validation limits for card fields.

All hard limits are enforced here and called from operations.py before any
write, so they apply uniformly to the UI, the embedded chat agent, and
external MCP callers.

Raising ``ValueError`` with a human-readable message lets callers (including
MCP tool handlers) surface the reason without catching separate exception types.
"""
from __future__ import annotations

from pathlib import Path

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
MAX_ATTACHMENT_SIZE_MB = 25
MAX_TOTAL_ATTACHMENTS_MB = 500
MAX_CHECKLIST_ITEMS = 100
MAX_CHECKLIST_TEXT = 500
MAX_FILE_REFS = 50
MAX_FILE_REF_LEN = 300
VALID_COMMENT_ROLES = frozenset(["note", "instruction", "update", "decision", "blocker", "question", "system"])
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
WARN_ATTACHMENT_SIZE_MB   = 5    # per-file soft hint: suggest file ref instead
WARN_TOTAL_ATTACHMENTS_MB = 400  # board-wide: health signal goes watch


# ── Validators ────────────────────────────────────────────────────────────────

def resolve_target_repo_path(ref: str) -> tuple[str, Path]:
    """Resolve a project-root-relative ref to a concrete file path in the target repo.

    Returns (file_part, resolved_path). The ref may include an optional line
    range suffix like ':10-30'; only the file portion is used for filesystem
    validation.
    """
    import kanbai.paths as _paths

    p = (ref or "").strip()
    file_part = p.split(":")[0]
    target = (_paths.PROJECT_ROOT / file_part).resolve()
    try:
        target.relative_to(_paths.PROJECT_ROOT.resolve())
    except ValueError:
        raise ValueError("file_ref: must stay within the target repository.")
    return file_part, target


def normalize_file_ref(ref: str) -> str:
    """Convert a project-root-relative file ref to the absolute stored form.

    The line-range suffix, when present, is preserved verbatim.
    """
    p = (ref or "").strip()
    file_part, target = resolve_target_repo_path(p)
    suffix = p[len(file_part):]
    return str(target) + suffix


def coerce_stored_file_ref(ref: str) -> str:
    """Return the stored form for a file ref, preserving already-absolute refs.

    Relative refs are resolved against the current project root; absolute refs
    are returned unchanged.
    """
    p = (ref or "").strip()
    if not p:
        return p
    file_part = p.split(":")[0]
    if Path(file_part).is_absolute():
        return p
    return normalize_file_ref(p)


def display_file_ref(ref: str) -> str:
    """Convert a stored file ref to the project-root-relative display form.

    If the ref is not inside the current project root, return it unchanged.
    The line-range suffix, when present, is preserved verbatim.
    """
    import kanbai.paths as _paths

    p = (ref or "").strip()
    if not p:
        return p
    file_part = p.split(":")[0]
    suffix = p[len(file_part):]
    try:
        target = Path(file_part)
        if not target.is_absolute():
            target = _paths.PROJECT_ROOT / target
        rel = target.resolve().relative_to(_paths.PROJECT_ROOT.resolve())
    except Exception:
        return p
    return f"{rel}{suffix}"


def validate_card_fields(
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    assigned_to: list[str] | None = None,
    links: list[dict] | None = None,
    file_refs: list[str] | None = None,
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

    if file_refs is not None:
        if len(file_refs) > MAX_FILE_REFS:
            raise ValueError(
                f"file_refs: maximum {MAX_FILE_REFS} references per card."
            )
        for i, ref in enumerate(file_refs):
            validate_file_ref(ref, i)

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


def validate_comment_role(role: str) -> None:
    """Raise ``ValueError`` if *role* is not a recognised comment role."""
    if role not in VALID_COMMENT_ROLES:
        valid = ", ".join(sorted(VALID_COMMENT_ROLES))
        raise ValueError(f"comment role: '{role}' is not valid. Choose from: {valid}.")


def validate_checklist_item(text: str, current_count: int) -> None:
    """Raise ``ValueError`` if the checklist item text or count exceeds hard limits."""
    t = (text or "").strip()
    if not t:
        raise ValueError("checklist item: text must not be empty.")
    if len(t) > MAX_CHECKLIST_TEXT:
        raise ValueError(
            f"checklist item: maximum {MAX_CHECKLIST_TEXT} characters allowed (got {len(t)})."
        )
    if current_count >= MAX_CHECKLIST_ITEMS:
        raise ValueError(
            f"checklist: maximum {MAX_CHECKLIST_ITEMS} items per card reached."
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


def validate_file_ref(path: str, current_count: int) -> None:
    """Raise ``ValueError`` if the project-root-relative file reference is invalid.

    The raw ref stored on the card remains relative; resolution against
    ``PROJECT_ROOT`` happens only for validation.
    """
    import re

    p = (path or "").strip()
    if not p:
        raise ValueError("file_ref: path must not be empty.")
    if len(p) > MAX_FILE_REF_LEN:
        raise ValueError(
            f"file_ref: path too long ({len(p)} chars, max {MAX_FILE_REF_LEN})."
        )
    if current_count >= MAX_FILE_REFS:
        raise ValueError(
            f"file_refs: maximum {MAX_FILE_REFS} references per card."
        )
    if p.startswith("/"):
        raise ValueError("file_ref: must be a relative path (no leading '/').")
    # Reject '..' in the file-path component (before any ':line' suffix).
    file_part = p.split(":")[0]
    if ".." in file_part.split("/"):
        raise ValueError("file_ref: '..' path components are not allowed.")
    # Valid formats: 'path/to/file', 'path/to/file:42', 'path/to/file:10-30'
    if not re.match(r'^[^:]+(?::\d+(?:-\d+)?)?$', p):
        raise ValueError(
            "file_ref: invalid format. Use 'path/to/file' or 'path/to/file:10-30'."
        )

    _, target = resolve_target_repo_path(p)
    if not target.exists():
        raise ValueError(f"file_ref: file not found in target repository: {file_part}")
    if not target.is_file():
        raise ValueError(f"file_ref: target is not a file: {file_part}")


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
