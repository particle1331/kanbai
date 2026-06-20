"""Single source of truth for status (column) and priority colors.

Every UI surface — Kanban board, table view, analytics charts and the priority
filter pills — imports its colors from here so they stay consistent. Use the
``Status`` / ``Priority`` enums for column / priority identifiers and the helper
functions to resolve colors.
"""

from __future__ import annotations

from enum import Enum

import flet as ft


class Status(str, Enum):
    BACKLOG = "backlog"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"


class Priority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ── Canonical status (column) accent colors ────────────────────────────────
# Workflow progression palette kept distinct from PRIORITY_COLORS
# (priority owns red/amber/blue): gray → teal → purple → green.
STATUS_COLORS: dict[str, str] = {
    Status.BACKLOG: "#6E7681",      # gray   (idle / not started)
    Status.IN_PROGRESS: "#2EA6A0",  # teal   (active work)
    Status.REVIEW: "#A371F7",       # purple (awaiting approval)
    Status.DONE: "#56D364",         # green  (complete)
}

# ── Canonical priority accent colors (used by pills, table, analytics) ──────
PRIORITY_COLORS: dict[str, str] = {
    Priority.HIGH: "#F85149",    # red
    Priority.MEDIUM: "#F0A742",  # amber
    Priority.LOW: "#58A6FF",     # blue
}

# ── Priority card background / border tints (Kanban tiles) ──────────────────
PRIORITY_BG: dict[str, str] = {
    Priority.HIGH: "#5C2828",
    Priority.MEDIUM: "#4D3A00",
    Priority.LOW: "#1E3350",
}
PRIORITY_BORDER: dict[str, str] = {
    Priority.HIGH: "#8C3A3A",
    Priority.MEDIUM: "#7A5E00",
    Priority.LOW: "#2E5C8A",
}

# Neutral fallbacks
DEFAULT_STATUS_COLOR = "#8B949E"
DEFAULT_PRIORITY_COLOR = "#58A6FF"
DEFAULT_PRIORITY_BG = "#2D2D2D"
DEFAULT_PRIORITY_BORDER = "#3E3E42"


def status_color(status: str) -> str:
    return STATUS_COLORS.get(status, DEFAULT_STATUS_COLOR)


def priority_color(priority: str) -> str:
    return PRIORITY_COLORS.get(priority, DEFAULT_PRIORITY_COLOR)


def priority_bg(priority: str) -> str:
    return PRIORITY_BG.get(priority, DEFAULT_PRIORITY_BG)


def priority_border(priority: str) -> str:
    return PRIORITY_BORDER.get(priority, DEFAULT_PRIORITY_BORDER)


# ── Epic selector dropdown (shared across Board / Analytics / Graph) ─────────
# Canonical visual styling so the epic <select> looks identical on every page.
# Per-page *options* and *behaviour* (e.g. "All" vs "(Unassigned)", archived
# handling) stay with each page — only the look is centralised here.
EPIC_DROPDOWN_WIDTH = 200
_EPIC_DD_BG = "#21262D"
_EPIC_DD_BORDER = "#30363D"
_EPIC_DD_TEXT = "#E6EDF3"


def epic_dropdown(
    *,
    options: list[ft.dropdown.Option],
    value: str,
    on_select,
) -> ft.Dropdown:
    """Build an epic-filter dropdown with the shared visual style.

    Callers supply their own ``options`` / ``value`` / ``on_select`` so that
    page-specific semantics are preserved; the colours, sizing and padding are
    identical everywhere.
    """
    return ft.Dropdown(
        options=options,
        value=value,
        on_select=on_select,
        bgcolor=_EPIC_DD_BG,
        border_color=_EPIC_DD_BORDER,
        color=_EPIC_DD_TEXT,
        text_size=12,
        content_padding=ft.Padding(10, 4, 10, 4),
        width=EPIC_DROPDOWN_WIDTH,
    )


# ── Eye-toggle visibility chip ──────────────────────────────────────────────
_EYE_ON = "#58A6FF"
_EYE_OFF = "#6E7681"
_EYE_TEXT_ON = "#C9D1D9"
_EYE_BORDER_OFF = "#3E3E42"
def eye_toggle(subject: str, value: bool, on_change, *, tooltip: str | None = None) -> ft.Container:
    """Build a compact eye-icon visibility toggle chip.

    Replaces verbose "Show X" switches: shows an open-eye icon + ``subject``
    text (highlighted) when visible, a crossed-out dimmed eye when hidden.
    Clicking toggles and calls ``on_change(new_value: bool)``. The chip updates
    its own appearance; the callback is responsible for any page refresh.
    """
    state = {"on": value}
    icon = ft.Icon(
        ft.Icons.VISIBILITY if value else ft.Icons.VISIBILITY_OFF,
        size=14,
        color=_EYE_ON if value else _EYE_OFF,
    )
    label = ft.Text(subject, size=11, color=_EYE_TEXT_ON if value else _EYE_OFF)

    def _side(on: bool) -> ft.Border:
        s = ft.BorderSide(1, _EYE_ON if on else _EYE_BORDER_OFF)
        return ft.Border(s, s, s, s)

    chip = ft.Container(
        content=ft.Row(
            [icon, label],
            spacing=5,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor="#21262D" if value else "transparent",
        border=_side(value),
        border_radius=6,
        padding=ft.Padding(8, 4, 8, 4),
        ink=True,
        tooltip=tooltip or f"Toggle {subject.lower()} visibility",
    )

    def _toggle(_e) -> None:
        on = not state["on"]
        state["on"] = on
        icon.icon = ft.Icons.VISIBILITY if on else ft.Icons.VISIBILITY_OFF
        icon.color = _EYE_ON if on else _EYE_OFF
        label.color = _EYE_TEXT_ON if on else _EYE_OFF
        chip.bgcolor = "#21262D" if on else "transparent"
        chip.border = _side(on)
        on_change(on)

    chip.on_click = _toggle
    return chip


# ── Archive / unarchive icon button ─────────────────────────────────────────
_ARCHIVE_COLOR = "#F0883E"   # orange — matches the "ARCHIVED" card marker
_UNARCHIVE_COLOR = "#58A6FF"  # blue
_ARCHIVE_DISABLED = "#484F58"  # muted gray for the disabled state


def archive_icon_button(
    *,
    archived: bool,
    subject: str,
    on_click,
    disabled: bool = False,
    icon_size: int = 18,
) -> ft.IconButton:
    """Icon button for archiving / unarchiving an item.

    Uses the box-with-down-arrow archive glyph (and the box-with-up-arrow
    unarchive glyph when ``archived`` is True). ``subject`` is woven into the
    hover tooltip, e.g. ``subject="card"`` -> "Archive card" / "Unarchive card".
    """
    if archived:
        return ft.IconButton(
            ft.Icons.UNARCHIVE_OUTLINED,
            icon_size=icon_size,
            icon_color=_ARCHIVE_DISABLED if disabled else _UNARCHIVE_COLOR,
            tooltip=f"Unarchive {subject}",
            on_click=on_click,
            disabled=disabled,
        )
    return ft.IconButton(
        ft.Icons.ARCHIVE_OUTLINED,
        icon_size=icon_size,
        icon_color=_ARCHIVE_DISABLED if disabled else _ARCHIVE_COLOR,
        tooltip=f"Archive {subject}",
        on_click=on_click,
        disabled=disabled,
    )
