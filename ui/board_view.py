from __future__ import annotations

import flet as ft
from datetime import date, datetime, timezone

from board import operations
from board import settings
from board.models import LANE_LABELS, LANES
from ui import avatar_color
from ui import theme

_LANE_COLORS = theme.STATUS_COLORS


def _matches_deadline(card: dict, deadline_filter: str, today: date) -> bool:
    """Whether a card falls inside the given deadline window (and isn't done)."""
    if (card.get("lane") or "") == "done":
        return False
    due_raw = card.get("due_date")
    if not due_raw:
        return False
    try:
        due = date.fromisoformat(str(due_raw)[:10])
    except ValueError:
        return False
    delta = (due - today).days
    if deadline_filter == "due_1d":
        return delta <= settings.DEADLINE_URGENT_DAYS
    if deadline_filter == "due_3d":
        return delta <= settings.DEADLINE_SOON_DAYS
    return False


def _compute_visible_numbers(
    epic,
    priority_filter: str | None,
    show_archived: bool,
    deadline_filter: str | None = None,
) -> set[int] | None:
    """Compute the set of card numbers that should be visible under the active
    priority and/or deadline filters.

    Returns ``None`` when no filter is active (i.e. show everything). Otherwise
    each active filter contributes the numbers of its matching cards PLUS the
    numbers of any card a match is *blocked by* (so blockers stay visible), and
    the filters are intersected when more than one is active.

    Priority and deadline are independent axes: ``priority`` is the card's
    intrinsic importance (never altered by a due date), while the deadline
    filters select by urgency. Combining e.g. "High" + "Due ≤1d" yields the
    important-and-urgent cards.
    """
    if not priority_filter and not deadline_filter:
        return None
    cards = operations.list_cards(archived=False, epic=epic)
    if show_archived:
        cards += operations.list_cards(archived=True, epic=epic)
    today = date.today()

    def _with_blockers(matched: list[dict]) -> set[int]:
        nums: set[int] = set()
        for c in matched:
            num = c.get("number")
            if num is not None:
                nums.add(num)
            for link in (c.get("links") or []):
                if link.get("type") == "blocked_by":
                    blocker = link.get("number")
                    if blocker is not None:
                        nums.add(blocker)
        return nums

    visible: set[int] | None = None
    if priority_filter:
        matched = [c for c in cards if (c.get("priority") or "medium") == priority_filter]
        pset = _with_blockers(matched)
        visible = pset if visible is None else (visible & pset)
    if deadline_filter:
        matched = [c for c in cards if _matches_deadline(c, deadline_filter, today)]
        dset = _with_blockers(matched)
        visible = dset if visible is None else (visible & dset)
    return visible


def _archived_card_numbers(show_archived: bool) -> frozenset[int]:
    """Card numbers that should be suppressed from 'Blocks #N' badges.

    When ``show_archived`` is on, nothing is hidden. Otherwise returns the
    numbers of all archived cards (across every epic, since a ``blocks`` link
    may point at a card in another epic) so badges referencing them are dropped.
    """
    if show_archived:
        return frozenset()
    return frozenset(
        c["number"]
        for c in operations.list_cards(archived=True, epic=None)
        if c.get("number") is not None
    )


def _age_label(created_at: str) -> ft.Container:
    """Return a small mm/dd + age-in-days label, or empty container if no date."""
    if not created_at:
        return ft.Container(width=0)
    try:
        dt_utc = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if dt_utc.tzinfo is None:
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        # Honour the app-wide timezone setting (local vs UTC) so the date and
        # age match what the user sees on the analytics board.
        if settings.use_local_tz():
            dt = dt_utc.astimezone().date()
            today = date.today()
        else:
            dt = dt_utc.astimezone(timezone.utc).date()
            today = datetime.now(timezone.utc).date()
    except (ValueError, AttributeError):
        return ft.Container(width=0)
    age = (today - dt).days
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(dt.strftime("%m/%d"), size=9, color="#6E7681", text_align=ft.TextAlign.RIGHT),
                ft.Text(f"{age}d", size=9, color="#6E7681", text_align=ft.TextAlign.RIGHT),
            ],
            spacing=0,
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.END,
        ),
    )


def _due_chip(due_date: str | None, lane: str) -> ft.Control | None:
    """Small calendar chip for a card tile, enforcing the 3-day rule.

    Every card is meant to be completable within ~3 days, so a deadline should
    still be *actionable* when it surfaces. The chip therefore warns early:
      • red  — due today or overdue (commitment missed / about to miss)
      • amber — due within ``settings.DEADLINE_SOON_DAYS`` days (act now)
      • grey  — comfortably ahead, or the card is already done
    """
    if not due_date:
        return None
    try:
        due = date.fromisoformat(due_date)
        label = due.strftime("%b %d")
    except ValueError:
        return None

    delta = (due - date.today()).days
    done = lane == "done"
    if not done and delta < 0:
        color, icon = "#F85149", ft.Icons.WARNING_AMBER_ROUNDED
        tooltip = f"Due {due_date} — overdue by {abs(delta)}d"
    elif not done and delta == 0:
        color, icon = "#F85149", ft.Icons.WARNING_AMBER_ROUNDED
        tooltip = f"Due {due_date} — due today"
    elif not done and delta <= settings.DEADLINE_URGENT_DAYS:
        color, icon = "#F85149", ft.Icons.WARNING_AMBER_ROUNDED
        tooltip = f"Due {due_date} — in {delta}d (urgent)"
    elif not done and delta <= settings.DEADLINE_SOON_DAYS:
        color, icon = "#F0A742", ft.Icons.WARNING_AMBER_ROUNDED
        tooltip = f"Due {due_date} — in {delta}d (act now)"
    else:
        color, icon = "#8B949E", ft.Icons.EVENT_OUTLINED
        tooltip = f"Due {due_date}"
    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(icon, size=10, color=color),
                ft.Text(label, size=10, color=color, weight=ft.FontWeight.W_500),
            ],
            spacing=3,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=ft.Colors.with_opacity(0.15, color),
        border_radius=10,
        padding=ft.Padding(7, 2, 7, 2),
        tooltip=tooltip,
    )


def _make_card_tile(
    card_data: dict,
    on_open_card,
    is_archived: bool = False,
    on_reorder=None,
    page: ft.Page | None = None,
    hidden_block_numbers: frozenset[int] | None = None,
) -> ft.Control:
    priority = card_data.get("priority", "medium")
    raw_tags = card_data.get("tags", "")
    tag_list = (
        [t.strip() for t in raw_tags.split(",") if t.strip()]
        if isinstance(raw_tags, str)
        else list(raw_tags or [])
    )
    number = card_data.get("number")
    created_at = card_data.get("created_at", "")

    raw_assigned = card_data.get("assigned_to", "")
    assigned_list = (
        [a.strip() for a in raw_assigned.split(",") if a.strip()]
        if isinstance(raw_assigned, str)
        else list(raw_assigned or [])
    )
    assignee_avatars = ft.Row(
        [
            ft.Container(
                content=ft.Text(
                    name[:2].upper(), size=9, color="#0d1117", weight=ft.FontWeight.W_600,
                ),
                width=22,
                height=22,
                bgcolor=avatar_color(name),
                border_radius=11,
                alignment=ft.Alignment.CENTER,
                tooltip=name,
            )
            for name in assigned_list
        ],
        spacing=3,
        tight=True,
        alignment=ft.MainAxisAlignment.END,
    )

    blocked_numbers = [
        l.get("number")
        for l in (card_data.get("links") or [])
        if l.get("type") == "blocks"
        and l.get("number") is not None
        and not (hidden_block_numbers and l.get("number") in hidden_block_numbers)
    ]
    blocks_badges = [
        ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.BLOCK, size=10, color="#F85149"),
                    ft.Text(
                        f"Blocks #{num}",
                        size=10,
                        color="#F85149",
                        weight=ft.FontWeight.W_500,
                    ),
                ],
                spacing=3,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=ft.Colors.with_opacity(0.15, "#F85149"),
            border_radius=10,
            padding=ft.Padding(7, 2, 7, 2),
            tooltip=f"Blocks card #{num}",
        )
        for num in blocked_numbers
    ]

    tag_chips = [
        ft.Container(
            content=ft.Text(t, size=10, color="#58A6FF"),
            bgcolor="#1C2A4A",
            border_radius=4,
            padding=ft.Padding(2, 6, 2, 6),
        )
        for t in tag_list
    ]
    due_chip = _due_chip(card_data.get("due_date"), card_data.get("lane", ""))
    meta_badges = blocks_badges + ([due_chip] if due_chip else []) + tag_chips
    tag_row = ft.Row(
        meta_badges,
        wrap=True,
        spacing=4,
        expand=True,
    )
    has_meta = bool(meta_badges)

    title_row = ft.Row(
        [
            ft.Text(
                f"#{number}",
                size=13,
                color="#6E7681",
                weight=ft.FontWeight.W_600,
            ) if number is not None else ft.Container(width=0),
            ft.Text(
                card_data["title"],
                weight=ft.FontWeight.W_600,
                size=13,
                color="#C9D1D9",
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
                expand=True,
            ),
        ],
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )

    bottom_row = ft.Row(
        [
            tag_row if has_meta else ft.Container(expand=True),
            ft.Column(
                [_age_label(created_at)]
                + ([assignee_avatars] if assigned_list else []),
                spacing=6,
                horizontal_alignment=ft.CrossAxisAlignment.END,
                tight=True,
            ),
        ],
        spacing=4,
        vertical_alignment=ft.CrossAxisAlignment.START,
    )

    _border_color = theme.priority_border(priority)
    _bg_color = theme.priority_bg(priority)
    tile = ft.Container(
        content=ft.Column(
            [
                title_row,
                bottom_row,
            ],
            spacing=6,
            tight=True,
        ),
        padding=10,
        margin=ft.Margin(bottom=6),
        bgcolor=_bg_color,
        border_radius=6,
        border=ft.Border(
            ft.BorderSide(1, _border_color),
            ft.BorderSide(1, _border_color),
            ft.BorderSide(1, _border_color),
            ft.BorderSide(1, _border_color),
        ),
        opacity=0.45 if is_archived else 1.0,
        on_click=lambda e, cid=card_data["id"]: on_open_card(cid),
        ink=True,
    )

    feedback = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            f"#{number}",
                            size=13,
                            color="#6E7681",
                            weight=ft.FontWeight.W_600,
                        ) if number is not None else ft.Container(width=0),
                        ft.Text(
                            card_data["title"],
                            weight=ft.FontWeight.W_600,
                            size=13,
                            color="#C9D1D9",
                            max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            expand=True,
                        ),
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Row(
                    [
                        ft.Row(
                            [
                                ft.Container(
                                    content=ft.Text(t, size=10, color="#58A6FF"),
                                    bgcolor="#1C2A4A",
                                    border_radius=4,
                                    padding=ft.Padding(2, 6, 2, 6),
                                )
                                for t in tag_list
                            ],
                            wrap=True,
                            spacing=4,
                            expand=True,
                        ) if tag_list else ft.Container(expand=True),
                        _age_label(created_at),
                    ],
                    spacing=4,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                ),
            ],
            spacing=6,
            tight=True,
        ),
        width=240,
        padding=10,
        bgcolor=theme.priority_bg(priority),
        border_radius=6,
        opacity=0.85,
        border=ft.Border(
            ft.BorderSide(2, "#58A6FF"),
            ft.BorderSide(2, "#58A6FF"),
            ft.BorderSide(2, "#58A6FF"),
            ft.BorderSide(2, "#58A6FF"),
        ),
    )

    draggable = ft.Draggable(
        group="cards",
        content=tile,
        content_feedback=feedback,
        data=(card_data["id"], card_data.get("lane")),
    )

    # Tag the outermost control with the card number (string key) so the
    # fast filter path can toggle visibility without rebuilding the tile.
    draggable.key = str(number) if number is not None else ""

    # Archived cards are not reorderable; plain draggable.
    if is_archived or on_reorder is None or page is None:
        return draggable

    # Insertion indicator shown above the card while a drop is hovering over it.
    indicator = ft.Container(
        height=2,
        bgcolor="#00000000",
        border_radius=2,
        margin=ft.Margin(0, 0, 0, 4),
    )
    wrapper = ft.Column([indicator, draggable], spacing=0, tight=True)

    def _on_will_accept(e) -> None:
        indicator.bgcolor = "#58A6FF"
        page.update()

    def _on_leave(e) -> None:
        indicator.bgcolor = "#00000000"
        page.update()

    def _on_accept(e) -> None:
        indicator.bgcolor = "#00000000"
        src = e.src
        data = src.data if src is not None else None
        if not data:
            page.update()
            return
        dragged_id, from_col = data
        target_col = card_data.get("lane")
        before_id = card_data["id"]
        if dragged_id == before_id:
            page.update()
            return
        on_reorder(dragged_id, from_col, target_col, before_id)

    drag_target = ft.DragTarget(
        group="cards",
        content=wrapper,
        on_will_accept=_on_will_accept,
        on_leave=_on_leave,
        on_accept=_on_accept,
    )
    # Mirror the card number on the outer DragTarget for filter visibility.
    drag_target.key = str(number) if number is not None else ""
    return drag_target


def build_board(
    page: ft.Page,
    on_open_card,
    on_new_card,
    epic=None,
    on_move_card=None,
    show_archived: bool = False,
    on_reorder=None,
    priority_filter: str | None = None,
    deadline_filter: str | None = None,
) -> tuple[ft.Row, dict[str, ft.ListView]]:
    lane_listviews: dict[str, ft.ListView] = {}

    visible_numbers = _compute_visible_numbers(epic, priority_filter, show_archived, deadline_filter)
    hidden_block_numbers = _archived_card_numbers(show_archived)

    _HOVER_BORDER = ft.Border(
        ft.BorderSide(2, "#58A6FF"),
        ft.BorderSide(2, "#58A6FF"),
        ft.BorderSide(2, "#58A6FF"),
        ft.BorderSide(2, "#58A6FF"),
    )
    _NORMAL_BORDER = ft.Border(
        ft.BorderSide(1, "#3E3E42"),
        ft.BorderSide(1, "#3E3E42"),
        ft.BorderSide(1, "#3E3E42"),
        ft.BorderSide(1, "#3E3E42"),
    )

    def _build_lane(lane_key: str) -> ft.DragTarget:
        active = operations.list_cards(lane=lane_key, archived=False, epic=epic)
        archived_cards = (
            operations.list_cards(lane=lane_key, archived=True, epic=epic)
            if show_archived else []
        )
        if visible_numbers is not None:
            active = [c for c in active if c.get("number") in visible_numbers]
            archived_cards = [c for c in archived_cards if c.get("number") in visible_numbers]
        lv = ft.ListView(spacing=0, expand=True, padding=ft.Padding(top=4))
        for c in active:
            lv.controls.append(
                _make_card_tile(
                    c,
                    on_open_card,
                    on_reorder=on_reorder,
                    page=page,
                    hidden_block_numbers=hidden_block_numbers,
                )
            )
        for c in archived_cards:
            lv.controls.append(
                _make_card_tile(
                    c,
                    on_open_card,
                    is_archived=True,
                    hidden_block_numbers=hidden_block_numbers,
                )
            )
        lane_listviews[lane_key] = lv
        # Badge shows the count of cards currently VISIBLE in the lane, so it
        # tracks the "Show archived" toggle. The Text is stashed on lv.data so
        # the in-place refresh_lane path can update it without rebuilding.
        count_text = ft.Text(
            str(len(active) + len(archived_cards)),
            size=11,
            color="#8B949E",
        )
        lv.data = count_text

        lane_container = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                LANE_LABELS[lane_key],
                                weight=ft.FontWeight.BOLD,
                                size=13,
                                color="#E6EDF3",
                            ),
                            ft.Row(
                                [
                                    ft.IconButton(
                                        ft.Icons.ADD_CIRCLE_OUTLINE,
                                        icon_size=18,
                                        icon_color=_LANE_COLORS.get(lane_key, "#58A6FF"),
                                        tooltip="New card" if lane_key == "backlog" else "",
                                        on_click=lambda e: on_new_card("backlog") if lane_key == "backlog" else None,
                                        disabled=lane_key != "backlog",
                                        opacity=1.0 if lane_key == "backlog" else 0.0,
                                    ),
                                    count_text,
                                ],
                                spacing=2,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=1, color="#3E3E42"),
                    lv,
                ],
                spacing=0,
                expand=True,
            ),
            width=260,
            padding=ft.Padding(10, 10, 10, 10),
            bgcolor="#252526",
            border_radius=8,
            border=_NORMAL_BORDER,
            expand=False,
        )

        def _on_will_accept(e, container=lane_container) -> None:
            container.border = _HOVER_BORDER
            page.update()

        def _on_leave(e, container=lane_container) -> None:
            container.border = _NORMAL_BORDER
            page.update()

        def _on_accept(e, target_lane=lane_key, container=lane_container) -> None:
            container.border = _NORMAL_BORDER
            src = e.src
            data = src.data if src is not None else None
            if not data:
                page.update()
                return
            card_id, from_col = data
            # Dropped on empty lane space -> place at the bottom of this lane.
            if on_reorder is not None:
                on_reorder(card_id, from_col, target_lane, None)
                return
            if from_col == target_lane or on_move_card is None:
                page.update()
                return
            on_move_card(card_id, from_col, target_lane)

        return ft.DragTarget(
            group="cards",
            content=lane_container,
            on_will_accept=_on_will_accept,
            on_leave=_on_leave,
            on_accept=_on_accept,
        )

    board_row = ft.Row(
        controls=[_build_lane(c) for c in LANES],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
        vertical_alignment=ft.CrossAxisAlignment.START,
        expand=True,
    )
    return board_row, lane_listviews


def apply_filter(
    page: ft.Page,
    lane_listviews: dict[str, ft.ListView],
    visible_numbers: set[int] | None,
) -> None:
    """Apply a filter by toggling tile visibility — no tile rebuild.

    Much faster than refresh_lane: Flet only needs to push ``visible``
    property changes (~1 update per card) instead of replacing the entire
    control tree (dozens of nested objects per card).

    ``visible_numbers=None`` means no filter active → show every tile.
    """
    for lv in lane_listviews.values():
        visible_count = 0
        for ctrl in lv.controls:
            # Card number is stored in ctrl.key at tile-creation time.
            try:
                num = int(ctrl.key) if ctrl.key else None
            except (ValueError, TypeError):
                num = None
            should_show = visible_numbers is None or (
                num is not None and num in visible_numbers
            )
            ctrl.visible = should_show
            if should_show:
                visible_count += 1
        # Refresh the lane's count badge (stashed on lv.data).
        count_text = getattr(lv, "data", None)
        if isinstance(count_text, ft.Text):
            count_text.value = str(visible_count)
    page.update()


def refresh_lane(
    page: ft.Page,
    lane_listviews: dict[str, ft.ListView],
    lane_key: str,
    on_open_card,
    epic=None,
    show_archived: bool = False,
    on_reorder=None,
    priority_filter: str | None = None,
    deadline_filter: str | None = None,
    update: bool = True,
    # Pre-computed filter sets: when provided, skip the redundant all-lane DB
    # queries so batch refresh_board() callers only pay the cost once.
    _visible_numbers: set[int] | None | bool = False,  # False = "not provided"
    _hidden_block_numbers: frozenset[int] | None = None,
) -> None:
    lv = lane_listviews.get(lane_key)
    if not lv:
        return
    active = operations.list_cards(lane=lane_key, archived=False, epic=epic)
    archived_cards = (
        operations.list_cards(lane=lane_key, archived=True, epic=epic)
        if show_archived else []
    )
    # Use pre-computed values when supplied by the caller (batch refresh path),
    # otherwise compute them here for the single-lane refresh path.
    if _visible_numbers is False:
        visible_numbers = _compute_visible_numbers(epic, priority_filter, show_archived, deadline_filter)
    else:
        visible_numbers = _visible_numbers  # type: ignore[assignment]
    if _hidden_block_numbers is None and _visible_numbers is False:
        hidden_block_numbers = _archived_card_numbers(show_archived)
    else:
        hidden_block_numbers = _hidden_block_numbers or frozenset()
    if visible_numbers is not None:
        active = [c for c in active if c.get("number") in visible_numbers]
        archived_cards = [c for c in archived_cards if c.get("number") in visible_numbers]
    lv.controls.clear()
    for c in active:
        lv.controls.append(
            _make_card_tile(
                c,
                on_open_card,
                on_reorder=on_reorder,
                page=page,
                hidden_block_numbers=hidden_block_numbers,
            )
        )
    for c in archived_cards:
        lv.controls.append(
            _make_card_tile(
                c,
                on_open_card,
                is_archived=True,
                hidden_block_numbers=hidden_block_numbers,
            )
        )
    # Update the column's count badge (stashed on lv.data) so it tracks the
    # current visible set, including archived cards when they're shown.
    count_text = getattr(lv, "data", None)
    if isinstance(count_text, ft.Text):
        count_text.value = str(len(active) + len(archived_cards))
    if update:
        page.update()
