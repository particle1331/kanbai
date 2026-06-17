"""Graph view — per-epic card relationship diagram.

Each epic's cards are laid out in rows by kanban status (Backlog → Done),
with relationship edges drawn as labelled arrows between nodes.

Public API
----------
GraphPage          -- page widget (build / refresh / refresh_epics)
open_graph_dialog  -- neighbourhood dialog opened from a card's edit dialog
"""
from __future__ import annotations

import math

import flet as ft
import flet.canvas as cv
from flet.controls.painting import Paint, PaintingStyle

from board import epics as _epics
from board import operations
from board.models import LANE_LABELS, LANES
from board.storage import sqlite_cache as _sc
from ui import avatar_color, theme

# ── Layout constants ───────────────────────────────────────────────────────────
_CARD_W: float = 200.0
_CARD_H: float = 90.0
_LANE_GAP: float = 36.0    # horizontal gap between status lanes
_V_GAP: float = 40.0       # vertical gap between stacked cards in a lane
_PAD_TOP: float = 12.0
_PAD_LEFT: float = 16.0
_LANE_LABEL_H: float = 34.0   # height reserved for the lane header at the top

_LINK_COLORS: dict[str, str] = {
    "blocks":      "#F85149",
    "blocked_by":  "#E3B341",
    "parent_of":   "#58A6FF",
    "child_of":    "#7ABFFF",
    "duplicate":   "#D29922",
    "relates_to":  "#6E7681",
}

# Reading an edge from each endpoint's own perspective: the card holding the
# stored link (the "from" card) reads ``link_type``; the partner card reads the
# inverse relationship. e.g. if #1 is ``blocked_by`` #81, then at #81 the same
# edge reads ``blocks`` #1. Self-inverse types map to themselves.
_INVERSE_LINK: dict[str, str] = {
    "blocks":      "blocked_by",
    "blocked_by":  "blocks",
    "parent_of":   "child_of",
    "child_of":    "parent_of",
    "duplicate":   "duplicate",
    "relates_to":  "relates_to",
}

_SENTINEL = "__unassigned__"   # dropdown key for "Unassigned" epic

# Sentinel for "never refreshed yet". Distinct from any real epic value
# (including None, which is the valid "Unassigned" epic) so the first
# select_epic() always renders instead of being skipped as a no-op.
_NEVER_REFRESHED = object()

# Map each "inverse half" of an asymmetric relationship to its primary form.
# Edges are normalised to the primary orientation so each relationship is drawn
# exactly once AND so an edge still appears when only one of its two cards is in
# scope — e.g. a cross-epic parent_of/child_of pair where the `parent_of` half lives in
# another epic and only the `child_of` half is iterated here.
_PRIMARY_LINK: dict[str, str] = {
    "blocks": "blocked_by",
    "child_of":  "parent_of",
}

# Self-inverse relationships — de-duplicated by unordered card pair.
_SYMMETRIC_LINKS: frozenset[str] = frozenset({"duplicate", "relates_to"})


def _canonical_edge(fn: int, tn: int, lt: str) -> tuple[str, int, int]:
    """Return (link_type, from_num, to_num) in canonical orientation.

    Inverse halves (`blocks`, `child_of`) are flipped to their primary form
    (`blocked_by`, `parent_of`) with the endpoints swapped, so both stored halves
    of a relationship collapse onto the same canonical edge.
    """
    primary = _PRIMARY_LINK.get(lt)
    if primary is not None:
        return primary, tn, fn
    return lt, fn, tn


def _hierarchy_order(
    internal_nums: "set[int]",
    edges: "list[tuple[int, int, str]]",
    pos_of: "dict[int, float]",
) -> dict[int, int]:
    """Rank internal cards so a parent is immediately followed by its children.

    Cards are normally stacked down a lane by raw position. To make the
    parent/child hierarchy legible — and keep a parent vertically near its
    children across status lanes — we walk the parent forest depth-first and
    assign each card a global order index. The same ordering is applied in every
    lane, so members of one task tree land in adjacent vertical bands and their
    edges read as a clean hierarchy/sequence instead of crossing diagonals.

    ``edges`` are canonical, so a ``parent`` edge means ``from`` is the parent of
    ``to``. Multiple parents / cycles are tolerated via a ``visited`` guard.
    """
    children: dict[int, list[int]] = {}
    has_parent: set[int] = set()
    for fn, tn, lt in edges:
        if lt != "parent" or fn not in internal_nums or tn not in internal_nums:
            continue
        children.setdefault(fn, []).append(tn)
        has_parent.add(tn)

    def _pos(n: int) -> float:
        return pos_of.get(n, 0.0)

    for kids in children.values():
        kids.sort(key=_pos)

    order: dict[int, int] = {}
    visited: set[int] = set()

    def _visit(n: int) -> None:
        if n in visited:
            return
        visited.add(n)
        order[n] = len(order)
        for child in children.get(n, ()):
            _visit(child)

    # Roots first (no parent in scope), then mop up anything left in a cycle.
    for root in sorted((n for n in internal_nums if n not in has_parent), key=_pos):
        _visit(root)
    for n in sorted(internal_nums, key=_pos):
        _visit(n)
    return order


# ── Paint helpers ──────────────────────────────────────────────────────────────

def _mk_paint(color: str, *, fill: bool = False, width: float = 2.0) -> Paint:
    return Paint(
        color=color,
        stroke_width=width,
        style=PaintingStyle.FILL if fill else PaintingStyle.STROKE,
    )


# ── Shared node tile ───────────────────────────────────────────────────────────

def _build_node_tile(
    card_data: dict,
    on_open: "Callable[[str], None]",   # noqa: F821 — forward ref ok at runtime
    *,
    highlight: bool = False,
    ghost: bool = False,
) -> ft.Control:
    """Return a compact card tile for use as a graph node.

    ``on_open`` receives a single argument: the card id string.

    When ``ghost`` is True the card belongs to a *different* epic (a cross-epic
    relationship partner) and is rendered faded with its epic name shown.
    """
    priority    = card_data.get("priority", "medium")
    number      = card_data.get("number")
    title       = card_data.get("title", "")
    is_archived = bool(card_data.get("archived", False))
    epic_name   = (card_data.get("epic") or "").strip()

    raw_tags = card_data.get("tags", "")
    tag_list = (
        [t.strip() for t in raw_tags.split(",") if t.strip()]
        if isinstance(raw_tags, str)
        else list(raw_tags or [])
    )

    raw_assigned = card_data.get("assigned_to", "")
    assigned_list = (
        [a.strip() for a in raw_assigned.split(",") if a.strip()]
        if isinstance(raw_assigned, str)
        else list(raw_assigned or [])
    )

    if ghost:
        border_color = "#6E7681"
        border_w     = 1
        bg_color     = "#1A1D21"
    else:
        border_color = "#DB8132" if highlight else theme.priority_border(priority)
        border_w     = 2 if highlight else 1
        bg_color     = theme.priority_bg(priority)

    title_row = ft.Row(
        [
            ft.Text(
                f"#{number}", size=11, color="#6E7681",
                weight=ft.FontWeight.W_600,
            ) if number is not None else ft.Container(width=0),
            ft.Text(
                title, size=11, weight=ft.FontWeight.W_600,
                color="#C9D1D9", max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS, expand=True,
            ),
        ],
        spacing=4,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )

    bottom: list[ft.Control] = []
    if ghost:
        # Show the partner's epic so it's clear why it's outside the lanes.
        bottom.append(
            ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.ACCOUNT_TREE_OUTLINED, size=10, color="#8B949E"),
                        ft.Text(
                            epic_name or "Unassigned",
                            size=9, color="#8B949E",
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                    spacing=3,
                    tight=True,
                ),
                bgcolor="#21262D",
                border_radius=3,
                padding=ft.Padding(4, 2, 4, 2),
            )
        )
    else:
        for t in tag_list[:2]:
            bottom.append(
                ft.Container(
                    content=ft.Text(t, size=9, color="#58A6FF"),
                    bgcolor="#1C2A4A",
                    border_radius=3,
                    padding=ft.Padding(3, 2, 3, 2),
                )
            )
        for name in assigned_list[:2]:
            bottom.append(
                ft.Container(
                    content=ft.Text(
                        name[:2].upper(), size=8,
                        color="#0d1117", weight=ft.FontWeight.W_600,
                    ),
                    width=18, height=18,
                    bgcolor=avatar_color(name),
                    border_radius=9,
                    alignment=ft.Alignment.CENTER,
                    tooltip=name,
                )
            )

    if ghost:
        opacity = 0.7
    elif is_archived:
        opacity = 0.45
    else:
        opacity = 1.0

    return ft.Container(
        content=ft.Column(
            [
                title_row,
                ft.Row(bottom, spacing=4, tight=True) if bottom else ft.Container(height=0),
            ],
            spacing=4,
            tight=True,
        ),
        padding=8,
        bgcolor=bg_color,
        border_radius=6,
        border=ft.Border(
            ft.BorderSide(border_w, border_color),
            ft.BorderSide(border_w, border_color),
            ft.BorderSide(border_w, border_color),
            ft.BorderSide(border_w, border_color),
        ),
        opacity=opacity,
        on_click=lambda e, cid=card_data["id"]: on_open(cid),
        ink=True,
        tooltip="In epic '%s' — click to open" % (epic_name or "Unassigned") if ghost else None,
    )


# ── Edge drawing helpers ───────────────────────────────────────────────────────

def _clip_to_rect(
    cx: float, cy: float,        # centre of the rect to clip against
    tx: float, ty: float,        # the other endpoint (direction to clip toward)
    half_w: float, half_h: float,
) -> tuple[float, float]:
    """Return the point on the rect's border along the line (cx,cy)→(tx,ty)."""
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    # Scale factor to hit the nearest vertical/horizontal border.
    scale_x = half_w / abs(dx) if dx != 0 else math.inf
    scale_y = half_h / abs(dy) if dy != 0 else math.inf
    scale = min(scale_x, scale_y)
    return cx + dx * scale, cy + dy * scale


def _draw_edge(
    shapes: list,
    x1: float, y1: float,
    x2: float, y2: float,
    link_type: str,
    from_num: int | None = None,
    to_num: int | None = None,
) -> None:
    """Append a relationship-annotated edge to *shapes*.

    The edge is drawn as a plain line with a hollow dot at **each** end (no
    arrowhead). Each end is annotated from the perspective of the card it
    touches, so the relationship reads naturally at both cards:

    - At the **base** (near the ``from`` card): ``link_type #to_num``
      e.g. ``blocked_by #81``.
    - At the **head** (near the ``to`` card): the inverse relationship and the
      ``from`` card's number, e.g. ``blocks #1``.

    Symmetric types (``relates_to`` / ``duplicate``) read the same at both ends.
    """
    color = _LINK_COLORS.get(link_type, "#6E7681")
    shapes.append(cv.Line(x1, y1, x2, y2, paint=_mk_paint(color)))

    dx, dy = x2 - x1, y2 - y1
    length = math.sqrt(dx * dx + dy * dy)
    if length > 0:
        ux, uy = dx / length, dy / length
        nx, ny = -uy, ux   # perpendicular unit vector (for label offset)

        # Hollow dot at BOTH ends so each card's connection point reads clearly.
        for ex_, ey_ in ((x1, y1), (x2, y2)):
            shapes.append(
                cv.Circle(ex_, ey_, 5.0, paint=_mk_paint("#0D1117", fill=True))
            )
            shapes.append(
                cv.Circle(ex_, ey_, 5.0, paint=_mk_paint(color, width=2.0))
            )

        # Relationship-aware endpoint annotations.
        anno_style = ft.TextStyle(size=9, color=color, weight=ft.FontWeight.W_700)
        inverse = _INVERSE_LINK.get(link_type, link_type)

        # Annotations are placed *outward* from each endpoint — away from the
        # edge centre — so they never overlap the line or each other.
        # cv.Text anchors at top-left, so horizontal alignment depends on
        # which direction the label extends:
        #   • edge goes right  → from-label goes left  → right-align (subtract width)
        #   • edge goes left   → to-label   goes left  → right-align
        #   • edge is vertical → label is centered horizontally (subtract half width)
        _OUT   = 14    # px outward along edge direction
        _PERP  = 6     # px perpendicular (keeps label off the line)
        _CW    = 5.5   # approx px per char at font-size 9
        _TH    = 6     # half text height for vertical centering

        if to_num is not None:
            label = f"{link_type} #{to_num}"
            bx = x1 - ux * _OUT + nx * _PERP
            by = y1 - uy * _OUT + ny * _PERP - _TH
            if ux > 0.1:          # edge goes right → label goes left → right-align
                bx -= len(label) * _CW
            elif abs(ux) <= 0.1:  # vertical edge → center horizontally
                bx -= len(label) * _CW / 2
            shapes.append(cv.Text(bx, by, value=label, style=anno_style))

        if from_num is not None:
            label = f"{inverse} #{from_num}"
            hx = x2 + ux * _OUT + nx * _PERP
            hy = y2 + uy * _OUT + ny * _PERP - _TH
            if ux < -0.1:         # edge goes left → label goes left → right-align
                hx -= len(label) * _CW
            elif abs(ux) <= 0.1:  # vertical edge → center horizontally
                hx -= len(label) * _CW / 2
            shapes.append(cv.Text(hx, hy, value=label, style=anno_style))


# ── GraphPage ──────────────────────────────────────────────────────────────────

class GraphPage:
    def __init__(self, page: ft.Page, on_open_card, on_show_board=None, on_epic_change=None) -> None:
        self._page = page
        self._on_open_card = on_open_card
        self._on_show_board = on_show_board
        self._on_epic_change_cb = on_epic_change
        self._selected_epic: str | None = None   # None = Unassigned
        self._last_refreshed_epic: object = _NEVER_REFRESHED  # Track last refresh to skip redundant refreshes
        self._show_completed: bool = True
        self._show_backlog: bool = True
        self._show_archived: bool = False
        self._epic_dropdown: ft.Dropdown | None = None
        self._graph_area: ft.Container | None = None
        self._view: ft.Column | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def build(self) -> ft.Column:
        self._epic_dropdown = self._build_epic_dropdown()
        self._graph_area = ft.Container(expand=True)

        board_btn = ft.IconButton(
            icon=ft.Icons.VIEW_KANBAN_OUTLINED,
            icon_size=18,
            icon_color="#8B949E",
            tooltip="View this epic on the Board",
            on_click=self._on_view_board,
        )

        show_backlog_sw = theme.eye_toggle(
            "Backlog",
            self._show_backlog,
            self._on_toggle_backlog,
            tooltip="Include the Backlog lane",
        )
        show_completed_sw = theme.eye_toggle(
            "Completed",
            self._show_completed,
            self._on_toggle_completed,
            tooltip="Include the Done lane",
        )
        show_archived_sw = theme.eye_toggle(
            "Archived",
            self._show_archived,
            self._on_toggle_archived,
            tooltip="Include archived cards",
        )

        toolbar = ft.Container(
            content=ft.Row(
                [
                    ft.Row(
                        [
                            ft.Text("Epic:", size=12, color="#8B949E"),
                            self._epic_dropdown,
                            board_btn,
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [
                            show_backlog_sw,
                            show_completed_sw,
                            show_archived_sw,
                        ],
                        spacing=16,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(12, 8, 12, 8),
            bgcolor="#252526",
            border=ft.Border(bottom=ft.BorderSide(1, "#3E3E42")),
        )

        self._view = ft.Column(
            [toolbar, self._graph_area],
            spacing=0,
            expand=True,
        )
        self._refresh_graph()
        # build() already rendered for the current epic; record it so the first
        # navigation to this page is a no-op instead of a redundant rebuild.
        self._last_refreshed_epic = self._selected_epic
        return self._view

    def refresh(self) -> None:
        self._refresh_graph()

    def refresh_epics(self) -> None:
        if self._epic_dropdown is None:
            return
        epics = _epics.list_epics()
        all_vals = [_SENTINEL] + epics
        self._epic_dropdown.options = (
            [ft.dropdown.Option(_SENTINEL, "Unassigned")]
            + [ft.dropdown.Option(e, e) for e in epics]
        )
        if self._epic_dropdown.value not in all_vals:
            self._epic_dropdown.value = _SENTINEL
            self._selected_epic = None
        self._refresh_graph()

    def select_epic(self, epic: str | None) -> None:
        """Programmatically select an epic (None = Unassigned) and refresh."""
        # Skip refresh if epic hasn't changed (optimization for page navigation)
        if epic == self._last_refreshed_epic:
            return
        
        self._last_refreshed_epic = epic
        self._selected_epic = epic
        if self._epic_dropdown is not None:
            self._epic_dropdown.value = _SENTINEL if epic is None else epic
        self._refresh_graph()

    # ── Internals ──────────────────────────────────────────────────────────────

    def _build_epic_dropdown(self) -> ft.Dropdown:
        epics = _epics.list_epics()
        return theme.epic_dropdown(
            value=_SENTINEL,
            options=(
                [ft.dropdown.Option(_SENTINEL, "Unassigned")]
                + [ft.dropdown.Option(e, e) for e in epics]
            ),
            on_select=self._on_epic_select,
        )

    def _on_epic_select(self, e) -> None:
        val = e.control.value
        self._selected_epic = None if val == _SENTINEL else val
        self._last_refreshed_epic = self._selected_epic
        self._refresh_graph()
        if self._on_epic_change_cb is not None:
            self._on_epic_change_cb(self._selected_epic)

    def _on_view_board(self, e) -> None:
        if self._on_show_board is not None:
            self._on_show_board(self._selected_epic)

    def _on_toggle_backlog(self, value: bool) -> None:
        self._show_backlog = bool(value)
        self._refresh_graph()

    def _on_toggle_completed(self, value: bool) -> None:
        self._show_completed = bool(value)
        self._refresh_graph()

    def _on_toggle_archived(self, value: bool) -> None:
        self._show_archived = bool(value)
        self._refresh_graph()

    def _refresh_graph(self) -> None:
        if self._graph_area is None:
            return
        self._graph_area.content = self._build_graph_content()
        if self._view is not None:
            self._page.update()

    def _build_graph_content(self) -> ft.Control:
        all_rows = operations.list_cards(epic=self._selected_epic, archived=None)
        label = self._selected_epic or "Unassigned"

        epic_numbers = {
            r["number"] for r in all_rows if r.get("number") is not None
        }
        epic_row_by_num: dict[int, dict] = {
            r["number"]: r for r in all_rows if r.get("number") is not None
        }

        # Determine which status lanes are visible (switches hide Backlog / Done)
        hidden: set[str] = set()
        if not self._show_backlog:
            hidden.add("backlog")
        if not self._show_completed:
            hidden.add("done")
        visible_columns = [c for c in LANES if c not in hidden]

        # ── Resolve edges (drawing-direction de-duplicated) ──────────────────────
        # An edge originates from a card in THIS epic. The partner may live in
        # the same epic (internal) or a different epic (a faded "ghost" node).
        #
        # Visibility rule (applied in a second pass below): an edge is shown for
        # ANY relationship type, and is hidden only when it touches an archived
        # card while "Show archived" is off — EXCEPT a blocker relationship
        # (`blocks`/`blocked_by`) forces its archived card to stay visible. Once
        # a card is visible (forced by a blocker), ALL of its edges are drawn,
        # so we can't decide per-edge in one pass: first gather candidates, then
        # work out which archived cards are forced visible, then filter.
        candidates: list[tuple[int, int, str]] = []   # (from_num, to_num, type)
        seen_edges: set = set()
        ghost_rows: dict[int, dict] = {}
        row_by_num: dict[int, dict] = {}

        for r in all_rows:
            fn = r.get("number")
            if fn is None:
                continue
            # Skip cards in hidden lanes — their relationships drop out too.
            if r.get("lane", "backlog") not in visible_columns:
                continue
            for lk in r.get("links") or []:
                tn = lk.get("number")
                lt = lk.get("type", "relates_to")
                if tn is None:
                    continue
                # Normalise to a canonical orientation so the edge is drawn once
                # regardless of which endpoint stores it, and so it survives when
                # only ONE of its two cards is in scope (e.g. a cross-epic
                # parent/child or blocks/blocked_by pair).
                ctype, cfrom, cto = _canonical_edge(fn, tn, lt)

                if tn in epic_numbers:
                    partner = epic_row_by_num.get(tn)
                    # Skip a partner that sits in a hidden lane.
                    if not partner or partner.get("lane", "backlog") not in visible_columns:
                        continue
                else:
                    # Cross-epic partner → ghost node.
                    partner = ghost_rows.get(tn)
                    if partner is None:
                        partner = _sc.get_card_by_number(tn)
                        if not partner:
                            continue          # dangling link to a deleted card
                    ghost_rows[tn] = partner

                row_by_num[fn] = r
                row_by_num[tn] = partner

                if ctype in _SYMMETRIC_LINKS:
                    key = (frozenset((cfrom, cto)), ctype)
                else:
                    key = (cfrom, cto, ctype)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                candidates.append((cfrom, cto, ctype))

        # An archived card is "forced visible" when it takes part in a blocker
        # relationship, so active work never silently loses a blocker.
        forced_visible: set[int] = set()
        for cfrom, cto, ctype in candidates:
            if ctype == "blocked_by":
                for n in (cfrom, cto):
                    if bool(row_by_num[n].get("archived")):
                        forced_visible.add(n)

        def _node_visible(n: int) -> bool:
            if self._show_archived:
                return True
            if not bool(row_by_num[n].get("archived")):
                return True
            return n in forced_visible

        # Keep every edge between two visible cards — regardless of link type.
        edges: list[tuple[int, int, str]] = [
            (cfrom, cto, ctype)
            for cfrom, cto, ctype in candidates
            if _node_visible(cfrom) and _node_visible(cto)
        ]

        if not edges:
            empty_msg = (
                f"No related cards in '{label}'"
                if all_rows
                else f"No cards in '{label}'"
            )
            return ft.Container(
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.ACCOUNT_TREE_OUTLINED, size=48, color="#3E3E42"),
                        ft.Text(
                            empty_msg,
                            size=14, color="#6E7681", italic=True,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=12,
                ),
                expand=True,
                alignment=ft.Alignment.CENTER,
            )

        # Which cards are actually involved in at least one edge?
        involved: set[int] = set()
        for fn, tn, _ in edges:
            involved.add(fn)
            involved.add(tn)

        internal_nums = involved & epic_numbers
        ghost_nums = sorted(n for n in involved if n not in epic_numbers)

        # number → row dict for O(1) lookup (internal + ghost)
        num_to_row: dict[int, dict] = {}
        for n in internal_nums:
            r = epic_row_by_num.get(n)
            if r:
                num_to_row[n] = r
        for n in ghost_nums:
            num_to_row[n] = ghost_rows[n]

        # Group internal cards by kanban column. Within a lane, order by the
        # parent/child hierarchy (parent immediately followed by its children)
        # rather than raw position, so task trees read clearly across lanes.
        hier_order = _hierarchy_order(
            internal_nums,
            edges,
            {n: (epic_row_by_num[n].get("position") or 0.0) for n in internal_nums},
        )
        groups: dict[str, list[dict]] = {c: [] for c in visible_columns}
        for n in internal_nums:
            r = epic_row_by_num.get(n)
            if not r:
                continue
            col = r.get("lane", "backlog")
            if col in groups:
                groups[col].append(r)
        for col in groups:
            groups[col].sort(
                key=lambda r: (hier_order.get(r["number"], 0), r.get("position") or 0.0)
            )

        # Build the ordered list of lanes (status lanes + a trailing ghost lane
        # for cross-epic partners). Each lane carries its header label/colour and
        # the ordered card numbers it contains.
        lanes: list[tuple[str, str, list[int]]] = []
        for col in visible_columns:
            nums = [r["number"] for r in groups[col] if r.get("number") is not None]
            lanes.append((LANE_LABELS[col], theme.STATUS_COLORS.get(col, "#8B949E"), nums))
        if ghost_nums:
            lanes.append(("Other epics", "#8B949E", list(ghost_nums)))

        # Compute (x, y) = top-left of each card node. Lanes are vertical
        # (side-by-side); cards stack straight down in a clean column.
        positions: dict[int, tuple[float, float]] = {}
        lane_headers: list[tuple[float, str, str]] = []   # (x, label, colour)
        for lane_idx, (label, color, nums) in enumerate(lanes):
            lane_x = _PAD_LEFT + lane_idx * (_CARD_W + _LANE_GAP)
            lane_headers.append((lane_x, label, color))
            for c_idx, n in enumerate(nums):
                card_y = _PAD_TOP + _LANE_LABEL_H + c_idx * (_CARD_H + _V_GAP)
                positions[n] = (lane_x, card_y)

        if not positions:
            return ft.Container(expand=True)

        n_lanes = len(lanes)
        canvas_w = _PAD_LEFT + n_lanes * (_CARD_W + _LANE_GAP) + _PAD_LEFT
        canvas_h = max(y + _CARD_H for _, y in positions.values()) + _PAD_TOP + 20.0

        # ── Edges ──────────────────────────────────────────────────────────────
        # Edges between two cards in the SAME lane (vertical) would all run
        # down the lane centre and overlap — fan them horizontally.
        # Edges between cards in the SAME ROW (horizontal) share the same y
        # and overlap — fan them vertically within the card's height.
        same_lane_edges: dict[float, list[int]] = {}   # lane_x → edge indices
        same_row_edges: dict[float, list[int]] = {}    # row_y  → edge indices
        for i, (fn, tn, _lt) in enumerate(edges):
            if fn not in positions or tn not in positions:
                continue
            fx, fy = positions[fn]
            tx, ty = positions[tn]
            if fx == tx:   # same lane (identical x)
                same_lane_edges.setdefault(fx, []).append(i)
            elif fy == ty:  # same row (identical y, different lane)
                same_row_edges.setdefault(fy, []).append(i)

        edge_x_offset: dict[int, float] = {}   # applied to x anchor (same-lane)
        for _lane_x, idxs in same_lane_edges.items():
            k = len(idxs)
            if k == 1:
                edge_x_offset[idxs[0]] = 0.0
                continue
            # Spread offsets evenly across ~80% of the card width, centred on 0.
            span = _CARD_W * 0.8
            step = span / (k - 1)
            start = -span / 2
            for j, idx in enumerate(idxs):
                edge_x_offset[idx] = start + j * step

        edge_y_offset: dict[int, float] = {}   # applied to y anchor (same-row)
        for _row_y, idxs in same_row_edges.items():
            k = len(idxs)
            if k == 1:
                edge_y_offset[idxs[0]] = 0.0
                continue
            # Spread offsets evenly across ~80% of the card height, centred on 0.
            span = _CARD_H * 0.8
            step = span / (k - 1)
            start = -span / 2
            for j, idx in enumerate(idxs):
                edge_y_offset[idx] = start + j * step

        shapes: list = []
        for i, (fn, tn, lt) in enumerate(edges):
            if fn not in positions or tn not in positions:
                continue
            x_off = edge_x_offset.get(i, 0.0)
            y_off = edge_y_offset.get(i, 0.0)
            fx, fy = positions[fn]
            tx, ty = positions[tn]
            fcx, fcy = fx + _CARD_W / 2 + x_off, fy + _CARD_H / 2 + y_off
            tcx, tcy = tx + _CARD_W / 2 + x_off, ty + _CARD_H / 2 + y_off
            # Clip both ends to the card borders so the arrowhead lands in
            # the gap between cards (otherwise it hides under the tile).
            sx, sy = _clip_to_rect(fcx, fcy, tcx, tcy, _CARD_W / 2, _CARD_H / 2)
            ex, ey = _clip_to_rect(tcx, tcy, fcx, fcy, _CARD_W / 2, _CARD_H / 2)
            _draw_edge(shapes, sx, sy, ex, ey, lt, from_num=fn, to_num=tn)

        # ── Stack controls ──────────────────────────────────────────────────────
        stack_controls: list[ft.Control] = []

        # Lane (status) headers across the top
        for lane_x, label, color in lane_headers:
            stack_controls.append(
                ft.Container(
                    content=ft.Text(
                        label,
                        size=12, weight=ft.FontWeight.W_700,
                        color=color,
                    ),
                    left=lane_x,
                    top=_PAD_TOP,
                    width=_CARD_W,
                    height=_LANE_LABEL_H,
                    alignment=ft.Alignment.CENTER_LEFT,
                )
            )

        # Card nodes
        for num, (x, y) in positions.items():
            r = num_to_row.get(num)
            if not r:
                continue
            is_ghost = num not in epic_numbers
            stack_controls.append(
                ft.Container(
                    content=_build_node_tile(r, self._on_open_card, ghost=is_ghost),
                    left=x, top=y,
                    width=_CARD_W, height=_CARD_H,
                )
            )

        # Edges canvas LAST so arrows (and their endpoint annotations) draw on
        # top of the card tiles — long diagonal edges stay fully visible instead
        # of disappearing behind the cards they cross. ``ignore_interactions``
        # lets pointer events fall through to the card tiles underneath so they
        # remain clickable.
        stack_controls.append(
            ft.Container(
                content=cv.Canvas(
                    shapes=shapes, width=canvas_w, height=canvas_h,
                ),
                left=0, top=0,
                width=canvas_w, height=canvas_h,
                ignore_interactions=True,
            )
        )

        graph_stack = ft.Stack(stack_controls, width=canvas_w, height=canvas_h)

        return ft.Column(
            [
                ft.Row(
                    [graph_stack],
                    scroll=ft.ScrollMode.AUTO,
                    height=canvas_h,
                ),
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )


# ── Neighbourhood dialog ───────────────────────────────────────────────────────

def open_graph_dialog(
    page: ft.Page,
    card_id: str,
    open_card_fn,
    on_close=None,
) -> None:
    """Open a modal showing the focal card and its 1-hop neighbours.

    Cards are laid out in board lanes (Backlog → Done), matching the main graph
    view.  Cross-epic neighbours appear faded in their actual lane.
    ``open_card_fn(card_id: str)`` is called when the user clicks any node.
    """
    focal = operations.get_card(card_id)
    if not focal or not focal.links:
        return

    focal_num  = focal.number
    focal_epic = focal.epic

    # ── Collect all involved card rows ────────────────────────────────────────
    num_to_row: dict[int, dict] = {}
    if focal_num is not None:
        num_to_row[focal_num] = {
            "id":          focal.id,
            "number":      focal.number,
            "title":       focal.title,
            "priority":    focal.priority,
            "tags":        focal.tags,
            "assigned_to": focal.assigned_to,
            "archived":    focal.archived,
            "lane":        focal.lane,
            "epic":        focal.epic,
        }

    for lk in focal.links:
        n = lk.get("number")
        if n is None:
            continue
        row = _sc.get_card_by_number(n)
        if row:
            num_to_row[n] = dict(row)

    if not num_to_row:
        return

    N = len(focal.links)

    # ── Build canonical edges ─────────────────────────────────────────────────
    edges: list[tuple[int, int, str]] = []
    seen_edges: set = set()
    for lk in focal.links:
        tn = lk.get("number")
        lt = lk.get("type", "relates_to")
        if tn is None or tn not in num_to_row or focal_num is None:
            continue
        ctype, cfrom, cto = _canonical_edge(focal_num, tn, lt)
        key = (
            (frozenset((cfrom, cto)), ctype)
            if ctype in _SYMMETRIC_LINKS
            else (cfrom, cto, ctype)
        )
        if key not in seen_edges:
            seen_edges.add(key)
            edges.append((cfrom, cto, ctype))

    # ── Group cards into lane columns ─────────────────────────────────────────
    groups: dict[str, list[dict]] = {c: [] for c in LANES}
    for row in num_to_row.values():
        col = row.get("lane", "backlog")
        if col in groups:
            groups[col].append(row)
    for col in groups:
        groups[col].sort(key=lambda r: r.get("position") or 0.0)

    lanes: list[tuple[str, str, list[int]]] = [
        (
            LANE_LABELS[col],
            theme.STATUS_COLORS.get(col, "#8B949E"),
            [r["number"] for r in groups[col] if r.get("number") is not None],
        )
        for col in LANES
    ]

    # ── Compute positions ─────────────────────────────────────────────────────
    positions: dict[int, tuple[float, float]] = {}
    lane_headers: list[tuple[float, str, str]] = []
    for lane_idx, (label, color, nums) in enumerate(lanes):
        lane_x = _PAD_LEFT + lane_idx * (_CARD_W + _LANE_GAP)
        lane_headers.append((lane_x, label, color))
        for c_idx, n in enumerate(nums):
            card_y = _PAD_TOP + _LANE_LABEL_H + c_idx * (_CARD_H + _V_GAP)
            positions[n] = (lane_x, card_y)

    n_lanes = len(lanes)
    canvas_w = _PAD_LEFT + n_lanes * (_CARD_W + _LANE_GAP) + _PAD_LEFT
    canvas_h = (
        max(y + _CARD_H for _, y in positions.values()) + _PAD_TOP + 20.0
        if positions else 200.0
    )

    # ── Edge fanning (same-lane: horizontal spread; same-row: vertical spread) ─
    same_lane_edges: dict[float, list[int]] = {}
    same_row_edges: dict[float, list[int]] = {}
    for i, (fn, tn, _lt) in enumerate(edges):
        if fn not in positions or tn not in positions:
            continue
        fx, fy = positions[fn]
        tx, ty = positions[tn]
        if fx == tx:
            same_lane_edges.setdefault(fx, []).append(i)
        elif fy == ty:
            same_row_edges.setdefault(fy, []).append(i)

    edge_x_offset: dict[int, float] = {}
    for _lane_x, idxs in same_lane_edges.items():
        k = len(idxs)
        if k == 1:
            edge_x_offset[idxs[0]] = 0.0
            continue
        span = _CARD_W * 0.8
        step = span / (k - 1)
        start = -span / 2
        for j, idx in enumerate(idxs):
            edge_x_offset[idx] = start + j * step

    edge_y_offset: dict[int, float] = {}
    for _row_y, idxs in same_row_edges.items():
        k = len(idxs)
        if k == 1:
            edge_y_offset[idxs[0]] = 0.0
            continue
        span = _CARD_H * 0.8
        step = span / (k - 1)
        start = -span / 2
        for j, idx in enumerate(idxs):
            edge_y_offset[idx] = start + j * step

    shapes: list = []
    for i, (fn, tn, lt) in enumerate(edges):
        if fn not in positions or tn not in positions:
            continue
        x_off = edge_x_offset.get(i, 0.0)
        y_off = edge_y_offset.get(i, 0.0)
        fx, fy = positions[fn]
        tx, ty = positions[tn]
        fcx, fcy = fx + _CARD_W / 2 + x_off, fy + _CARD_H / 2 + y_off
        tcx, tcy = tx + _CARD_W / 2 + x_off, ty + _CARD_H / 2 + y_off
        sx, sy = _clip_to_rect(fcx, fcy, tcx, tcy, _CARD_W / 2, _CARD_H / 2)
        ex, ey = _clip_to_rect(tcx, tcy, fcx, fcy, _CARD_W / 2, _CARD_H / 2)
        _draw_edge(shapes, sx, sy, ex, ey, lt, from_num=fn, to_num=tn)

    # ── Stack controls ────────────────────────────────────────────────────────
    dlg: ft.AlertDialog   # forward ref — assigned before any click

    def _opener(cid: str) -> None:
        page.pop_dialog()
        if open_card_fn:
            open_card_fn(cid)

    stack_controls: list[ft.Control] = []

    for lane_x, label, color in lane_headers:
        stack_controls.append(
            ft.Container(
                content=ft.Text(label, size=12, weight=ft.FontWeight.W_700, color=color),
                left=lane_x, top=_PAD_TOP,
                width=_CARD_W, height=_LANE_LABEL_H,
                alignment=ft.Alignment.CENTER_LEFT,
            )
        )

    for num, (x, y) in positions.items():
        row = num_to_row.get(num)
        if not row:
            continue
        is_focal = num == focal_num
        is_ghost = (row.get("epic") or None) != (focal_epic or None) and not is_focal
        stack_controls.append(
            ft.Container(
                content=_build_node_tile(row, _opener, highlight=is_focal, ghost=is_ghost),
                left=x, top=y,
                width=_CARD_W, height=_CARD_H,
            )
        )

    # Edges canvas LAST so arrows draw on top of the cards; pointer events fall
    # through so the cards stay clickable.
    stack_controls.append(
        ft.Container(
            content=cv.Canvas(shapes=shapes, width=canvas_w, height=canvas_h),
            left=0, top=0,
            width=canvas_w, height=canvas_h,
            ignore_interactions=True,
        )
    )

    graph_stack = ft.Stack(stack_controls, width=canvas_w, height=canvas_h)

    DLG_H = 560.0

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row(
            [
                ft.Text(
                    f"#{focal.number}  {focal.title}",
                    size=14, weight=ft.FontWeight.W_600,
                    expand=True, max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
                ft.Text(
                    f"{N} relationship{'s' if N != 1 else ''}",
                    size=11, color="#8B949E",
                ),
            ],
            spacing=12,
        ),
        content=ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [graph_stack],
                        scroll=ft.ScrollMode.AUTO,
                        height=canvas_h,
                    ),
                ],
                scroll=ft.ScrollMode.AUTO,
            ),
            width=min(canvas_w + 32, 1100.0),
            height=DLG_H,
            padding=0,
        ),
        actions=[
            ft.IconButton(
                ft.Icons.CANCEL,
                icon_color="#ADBAC7",
                tooltip="Close",
                on_click=lambda e: _close_graph(),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    def _close_graph() -> None:
        page.pop_dialog()
        if on_close:
            on_close()

    page.show_dialog(dlg)
