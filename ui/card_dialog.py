from __future__ import annotations

import flet as ft
from datetime import datetime, date

from board import epics as _epics
from board import operations
from board import settings
from board.models import LANE_LABELS, LANES
from board.settings import DEFAULT_TAGS
from board.storage import sqlite_cache as _sc
from ui import avatar_color
from ui import theme


def _make_due_field(page: ft.Page, initial: str = "") -> tuple[ft.Row, ft.TextField]:
    """Build a due-date input: an editable YYYY-MM-DD field plus a calendar
    button that opens a Material DatePicker. Returns (row, field)."""
    due_field = ft.TextField(
        label="Due date",
        hint_text="YYYY-MM-DD",
        value=initial,
        expand=True,
    )

    def _on_pick(e):
        sel = e.control.value
        if sel is not None:
            from datetime import timezone
            # DatePicker returns UTC midnight; honour the app timezone setting.
            if sel.tzinfo is None:
                sel = sel.replace(tzinfo=timezone.utc)
            if settings.use_local_tz():
                due_field.value = sel.astimezone().strftime("%Y-%m-%d")
            else:
                due_field.value = sel.strftime("%Y-%m-%d")
            page.update()

    picker = ft.DatePicker(
        on_change=_on_pick,
        first_date=datetime(2000, 1, 1),
        last_date=datetime(2100, 12, 31),
        help_text="Select due date",
    )

    def _open(e):
        raw = (due_field.value or "").strip()
        if raw:
            try:
                picker.value = datetime.fromisoformat(raw[:10])
            except ValueError:
                pass
        page.show_dialog(picker)

    pick_btn = ft.IconButton(
        icon=ft.Icons.CALENDAR_MONTH,
        tooltip="Pick date",
        on_click=_open,
    )
    row = ft.Row(
        [due_field, pick_btn],
        spacing=4,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    return row, due_field


def _tag_suggestions(tags_field: ft.TextField) -> ft.Row:
    """Quick-pick chips for the three default type tags.

    Clicking a chip appends the tag to the comma-separated tags field
    (no-op if it's already present).
    """
    def _add_tag(tag: str) -> None:
        existing = [t.strip() for t in (tags_field.value or "").split(",") if t.strip()]
        if tag not in existing:
            existing.append(tag)
            tags_field.value = ", ".join(existing)
            tags_field.update()

    chips = [
        ft.Container(
            content=ft.Text(tag, size=11, color="#8B949E"),
            border=ft.Border(
                ft.BorderSide(1, "#30363D"), ft.BorderSide(1, "#30363D"),
                ft.BorderSide(1, "#30363D"), ft.BorderSide(1, "#30363D"),
            ),
            border_radius=10,
            padding=ft.Padding(8, 3, 8, 3),
            on_click=lambda e, t=tag: _add_tag(t),
            ink=True,
        )
        for tag in DEFAULT_TAGS
    ]
    return ft.Row([ft.Text("quick:", size=10, color="#4A6278"), *chips], spacing=6)


def open_create_dialog(page: ft.Page, default_lane: str, on_created, default_epic: str | None = None) -> None:
    title_field = ft.TextField(label="Title", autofocus=True)
    desc_field = ft.TextField(
        label="Description",
        multiline=True,
        expand=True,
    )
    tags_field = ft.TextField(label="Tags (comma-separated)")
    assigned_field = ft.TextField(label="Assigned to (comma-separated)")
    due_row, due_field = _make_due_field(page)
    priority_dd = ft.Dropdown(
        label="Priority",
        value="medium",
        options=[
            ft.dropdown.Option("low", "Low"),
            ft.dropdown.Option("medium", "Medium"),
            ft.dropdown.Option("high", "High"),
        ],
        expand=True,
    )
    error_text = ft.Text("", color=ft.Colors.RED_400, size=12)
    _saving = [False]

    # ── Pending relationships (applied after the card is created) ─────────────
    pending_links: list[dict] = []
    link_error = ft.Text("", color=ft.Colors.RED_400, size=11)
    links_col = ft.Column(spacing=2)
    add_type_dd = ft.Dropdown(
        label="Type",
        value="relates_to",
        options=[
            ft.dropdown.Option(t, t)
            for t in ["blocks", "blocked_by", "parent_of", "child_of", "duplicate", "relates_to"]
        ],
        width=140,
    )
    search_field = ft.TextField(
        label="Search cards by # or title",
        expand=True,
        on_change=lambda e: _refresh_results(),
    )
    results_col = ft.Column(spacing=2, height=0)

    def _build_link_rows() -> None:
        links_col.controls.clear()
        if not pending_links:
            links_col.controls.append(
                ft.Container(
                    content=ft.Text("No relationships yet.", size=11, color="#6E7681", italic=True),
                    padding=ft.Padding(2, 4, 2, 4),
                )
            )
            return
        for lk in list(pending_links):
            target_row = _sc.get_card_by_number(lk["number"])
            target_title = target_row["title"] if target_row else f"Card #{lk['number']}"

            def _remove_link(e, n=lk["number"], t=lk["type"]) -> None:
                pending_links[:] = [
                    l for l in pending_links if not (l["type"] == t and l["number"] == n)
                ]
                _build_link_rows()
                page.update()

            links_col.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Container(
                                content=ft.Text(lk["type"], size=10, color="#8B949E", weight=ft.FontWeight.W_500),
                                bgcolor="#21262D",
                                border_radius=4,
                                padding=ft.Padding(8, 3, 8, 3),
                            ),
                            ft.Container(
                                content=ft.Text(f"#{lk['number']} {target_title}", size=12, color="#C9D1D9"),
                                expand=True,
                            ),
                            ft.IconButton(
                                ft.Icons.CANCEL, icon_size=14, icon_color="#6E7681",
                                on_click=_remove_link, tooltip="Remove link",
                                padding=ft.Padding(0, 0, 0, 0),
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding(0, 1, 0, 1),
                )
            )

    _build_link_rows()

    def _matching_cards() -> list[dict]:
        rawval = search_field.value or ""
        raw = rawval.strip()
        # A '#'-number query followed by a trailing space means "exact number"
        # so `#5 ` matches only #5, while `#5` (no space) still substring-matches
        # #5/#50/#58/#85/etc.
        exact_number = raw.startswith("#") and rawval != rawval.rstrip()
        q = raw.lstrip("#").lower()
        if not q:
            return []
        # Exclude only cards already linked with the *currently selected* type —
        # a card linked under one type (e.g. `blocks`) can still be found to add
        # a different relationship (matches _do_add_link's per-type dedup).
        cur_type = add_type_dd.value or "relates_to"
        queued_nums = {
            l["number"] for l in pending_links if l["type"] == cur_type
        }
        out: list[dict] = []
        for r in _sc.query_cards(archived=None):
            num = r.get("number")
            if num is None:
                continue
            if num in queued_nums:
                continue
            if exact_number:
                if str(num) != q:
                    continue
            elif q not in str(num) and q not in (r.get("title") or "").lower():
                continue
            out.append(r)
        # Surface an exact number match first so it is never buried.
        out.sort(key=lambda r: 0 if str(r.get("number")) == q else 1)
        return out

    def _refresh_results() -> None:
        results_col.controls.clear()
        matches = _matching_cards()
        if not matches:
            results_col.height = 0
        else:
            visible = matches[:5]
            extra = len(matches) - 5
            results_col.height = None
            for r in visible:
                num = r["number"]
                ttl = r.get("title") or ""

                def _pick(e, n=num) -> None:
                    _do_add_link(n)

                results_col.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Text(f"#{num}", size=11, color="#8B949E", width=44),
                                ft.Text(
                                    ttl, size=12, color="#C9D1D9", expand=True,
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                            ],
                            spacing=6,
                        ),
                        on_click=_pick,
                        ink=True,
                        border_radius=4,
                        bgcolor="#161B22",
                        padding=ft.Padding(8, 6, 8, 6),
                    )
                )
            if extra > 0:
                results_col.controls.append(
                    ft.Text(
                        f"…and {extra} more — refine your search",
                        size=11, color="#6E7681", italic=True,
                    )
                )
        page.update()

    # Re-filter results when the link type changes (the exclusion set is
    # per-type, so switching type can reveal/hide already-linked cards).
    add_type_dd.on_select = lambda e: _refresh_results()

    def _do_add_link(target_num: int) -> None:
        link_type = add_type_dd.value or "relates_to"
        if any(l["type"] == link_type and l["number"] == target_num for l in pending_links):
            link_error.value = "That relationship is already queued."
            page.update()
            return
        pending_links.append({"type": link_type, "number": target_num})
        search_field.value = ""
        link_error.value = ""
        _refresh_results()
        _build_link_rows()
        page.update()

    def _save(e):
        if _saving[0]:
            return
        if not title_field.value or not title_field.value.strip():
            error_text.value = "Title is required."
            page.update()
            return
        _saving[0] = True
        tags = [t.strip() for t in (tags_field.value or "").split(",") if t.strip()]
        assigned = [a.strip() for a in (assigned_field.value or "").split(",") if a.strip()]
        try:
            card = operations.create_card(
                title=title_field.value.strip(),
                lane="backlog",
                description=(desc_field.value or "").strip(),
                tags=tags,
                priority=priority_dd.value or "medium",
                epic=default_epic,
                assigned_to=assigned,
                due_date=(due_field.value or "").strip() or None,
            )
        except (operations.EpicArchivedError, ValueError) as ex:
            _saving[0] = False
            error_text.value = str(ex)
            page.update()
            return
        # Apply queued relationships now that the card has a number.
        if card.number is not None:
            for lk in pending_links:
                operations.link_cards(card.number, lk["number"], lk["type"])
        page.pop_dialog()
        on_created(card)

    def _cancel(e):
        page.pop_dialog()

    rel_section = ft.Column(
        [
            ft.Text("Relationships", size=12, weight=ft.FontWeight.W_600, color="#C9D1D9"),
            links_col,
            ft.Row(
                [
                    add_type_dd,
                    search_field,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            results_col,
            link_error,
        ],
        spacing=8,
    )

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("New Card", size=16, weight=ft.FontWeight.W_600),
        content=ft.Container(
            content=ft.Row(
                [
                    ft.Column(
                        [ft.Container(height=5), title_field, desc_field],
                        spacing=10,
                        height=560,
                        expand=True,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    ft.VerticalDivider(width=1, color="#3E3E42"),
                    ft.Column(
                        [
                            ft.Container(height=4),
                            priority_dd,
                            tags_field,
                            _tag_suggestions(tags_field),
                            assigned_field,
                            due_row,
                            error_text,
                            ft.Divider(height=1, color="#3E3E42"),
                            rel_section,
                        ],
                        spacing=12,
                        scroll=ft.ScrollMode.AUTO,
                        height=560,
                        width=300,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            width=960,
            padding=ft.Padding(0, 8, 0, 0),
        ),
        actions=[
            ft.TextButton("Cancel", on_click=_cancel),
            ft.FilledButton("Create", on_click=_save),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dlg)


def open_edit_dialog(
    page: ft.Page,
    card_id: str,
    on_updated,
    on_moved,
    on_archived,
    open_card_fn=None,
    on_close=None,
) -> None:
    card = operations.get_card(card_id)
    if not card:
        return

    original_lane = card.lane
    current_links: list[dict] = list(card.links)

    # ── Edit fields ───────────────────────────────────────────────────────────
    title_field = ft.TextField(label="Title", value=card.title)
    desc_field = ft.TextField(
        label="Description",
        value=card.description,
        multiline=True,
        expand=True,
    )
    tags_field = ft.TextField(
        label="Tags (comma-separated)",
        value=", ".join(card.tags),
    )
    assigned_field = ft.TextField(
        label="Assigned to (comma-separated)",
        value=", ".join(card.assigned_to),
    )
    due_row, due_field = _make_due_field(page, card.due_date or "")
    priority_dd = ft.Dropdown(
        label="Priority",
        value=card.priority,
        options=[
            ft.dropdown.Option("low", "Low"),
            ft.dropdown.Option("medium", "Medium"),
            ft.dropdown.Option("high", "High"),
        ],
        expand=True,
    )
    lane_dd = ft.Dropdown(
        label="Move to",
        value=card.lane,
        options=[ft.dropdown.Option(k, v) for k, v in LANE_LABELS.items()],
        expand=True,
    )
    _active_epics = _epics.list_epics()
    _epic_options = [ft.dropdown.Option("", "Unassigned")] + [
        ft.dropdown.Option(e, e) for e in _active_epics
    ]
    # Keep the card's current epic selectable even if it's archived (e.g. an
    # archived card living in an archived epic) so saving doesn't silently drop it.
    if card.epic and card.epic not in _active_epics:
        _epic_options.append(ft.dropdown.Option(card.epic, f"{card.epic} (archived)"))
    epic_dd = ft.Dropdown(
        label="Epic",
        value=card.epic or "",
        options=_epic_options,
        expand=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    link_error = ft.Text("", color=ft.Colors.RED_400, size=11)
    op_error = ft.Text("", color=ft.Colors.RED_400, size=12)
    links_col = ft.Column(spacing=2)

    # Persisted fold state so sections stay open/closed across re-renders.
    fold_state = {"rel": False, "comments": False}

    def _make_fold(state_key: str, title: str, body: ft.Control) -> ft.Column:
        is_open = fold_state.get(state_key, False)
        toggle_icon = ft.Text(
            "−" if is_open else "+",
            size=16, weight=ft.FontWeight.W_700, color="#8B949E",
        )
        body.visible = is_open

        def _toggle(e) -> None:
            new = not fold_state.get(state_key, False)
            fold_state[state_key] = new
            body.visible = new
            toggle_icon.value = "−" if new else "+"
            page.update()

        header = ft.Container(
            content=ft.Row(
                [toggle_icon, ft.Text(title, size=12, weight=ft.FontWeight.W_600, color="#C9D1D9")],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_click=_toggle,
            ink=True,
            border_radius=4,
            padding=ft.Padding(2, 2, 2, 2),
        )
        return ft.Column([header, body], spacing=4)

    # ── Comments ──────────────────────────────────────────────────────────────
    comments_col = ft.Column(spacing=8)
    comment_field = ft.TextField(
        hint_text="Add a comment… (press Enter to post)",
        expand=True,
        on_submit=lambda e: _add_comment(e),
    )

    def _fmt_ts_parts(iso: str) -> tuple[str, str]:
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
            return dt.strftime("%m/%d/%y"), dt.strftime("%H:%M")
        except (ValueError, AttributeError):
            return iso, ""

    def _fmt_date(iso: str) -> str:
        if not iso:
            return "—"
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
            return dt.strftime("%b %d, %Y \u00b7 %H:%M")
        except (ValueError, AttributeError):
            return iso

    def _build_comments() -> None:
        comments_col.controls.clear()
        if not card.comments:
            comments_col.controls.append(
                ft.Text("No comments yet.", size=11, color="#6E7681", italic=True)
            )
            return
        for cm in card.comments:
            def _del(e, cid=cm.get("id")) -> None:
                nonlocal card
                operations.delete_comment(card_id, cid)
                card = operations.get_card(card_id) or card
                _build_comments()
                page.update()

            date_str, time_str = _fmt_ts_parts(cm.get("created_at", ""))
            comments_col.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text(date_str, size=10, color="#6E7681"),
                                    ft.Text(time_str, size=10, color="#6E7681"),
                                ],
                                spacing=0,
                                width=52,
                            ),
                            ft.Text(
                                cm.get("text", ""), size=12, color="#C9D1D9",
                                selectable=True, expand=True,
                            ),
                            ft.IconButton(
                                ft.Icons.CANCEL, icon_size=12, icon_color="#6E7681",
                                on_click=_del, tooltip="Delete comment",
                                padding=ft.Padding(0, 0, 0, 0),
                            ),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    bgcolor="#161B22",
                    border_radius=6,
                    padding=ft.Padding(8, 6, 8, 6),
                )
            )

    def _add_comment(e) -> None:
        nonlocal card
        txt = (comment_field.value or "").strip()
        if not txt:
            return
        operations.add_comment(card_id, txt)
        comment_field.value = ""
        card = operations.get_card(card_id) or card
        _build_comments()
        page.update()

    def _comments_section(editable: bool) -> ft.Control:
        _build_comments()
        children: list[ft.Control] = [comments_col]
        if editable:
            children.append(comment_field)
        body = ft.Column(children, spacing=8)
        return _make_fold("comments", f"Comments ({len(card.comments)})", body)

    def _build_link_rows(edit_mode: bool = False) -> None:
        links_col.controls.clear()
        if not current_links:
            links_col.controls.append(
                ft.Container(
                    content=ft.Text("No relationships yet.", size=11, color="#6E7681", italic=True),
                    padding=ft.Padding(2, 4, 2, 4),
                )
            )
            return
        for lk in list(current_links):
            target_row = _sc.get_card_by_number(lk["number"])
            target_title = target_row["title"] if target_row else f"Card #{lk['number']}"
            target_id = target_row["id"] if target_row else None

            def _open_linked(e, tid=target_id) -> None:
                if tid and open_card_fn:
                    page.pop_dialog()
                    open_card_fn(tid)

            def _remove_link(e, n=lk["number"], t=lk["type"]) -> None:
                nonlocal current_links
                if card.number is None:
                    return
                err = operations.unlink_cards(card.number, n, t)
                if err:
                    link_error.value = err
                    page.update()
                    return
                current_links = [l for l in current_links if not (l["type"] == t and l["number"] == n)]
                link_error.value = ""
                _build_link_rows(edit_mode=True)
                page.update()

            link_text: ft.Control = (
                ft.TextButton(
                    f"#{lk['number']} {target_title}",
                    on_click=_open_linked,
                    style=ft.ButtonStyle(
                        color="#58A6FF",
                        padding=ft.Padding(0, 0, 0, 0),
                        alignment=ft.Alignment.CENTER_LEFT,
                    ),
                )
                if (target_id and open_card_fn)
                else ft.Text(f"#{lk['number']} {target_title}", size=12, color="#C9D1D9", expand=True)
            )
            row_controls: list[ft.Control] = [
                ft.Container(
                    content=ft.Text(lk["type"], size=10, color="#8B949E", weight=ft.FontWeight.W_500),
                    bgcolor="#21262D",
                    border_radius=4,
                    padding=ft.Padding(8, 3, 8, 3),
                ),
                ft.Container(content=link_text, expand=True),
            ]
            if edit_mode:
                row_controls.append(
                    ft.IconButton(
                        ft.Icons.CANCEL, icon_size=14, icon_color="#6E7681",
                        on_click=_remove_link, tooltip="Remove link",
                        padding=ft.Padding(0, 0, 0, 0),
                    )
                )
            links_col.controls.append(
                ft.Container(
                    content=ft.Row(
                        row_controls,
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=ft.Padding(0, 1, 0, 1),
                )
            )

    _build_link_rows()

    add_type_dd = ft.Dropdown(
        label="Type",
        value="relates_to",
        options=[
            ft.dropdown.Option(t, t)
            for t in ["blocks", "blocked_by", "parent_of", "child_of", "duplicate", "relates_to"]
        ],
        width=140,
    )
    search_field = ft.TextField(
        label="Search cards by # or title",
        expand=True,
        on_change=lambda e: _refresh_results(),
    )
    results_col = ft.Column(spacing=2, height=0)

    def _matching_cards() -> list[dict]:
        rawval = search_field.value or ""
        raw = rawval.strip()
        # A '#'-number query followed by a trailing space means "exact number"
        # so `#5 ` matches only #5, while `#5` (no space) still substring-matches
        # #5/#50/#58/#85/etc.
        exact_number = raw.startswith("#") and rawval != rawval.rstrip()
        q = raw.lstrip("#").lower()
        if not q:
            return []
        # Exclude only cards already linked with the *currently selected* type so
        # an already-linked card can still be found to add a different
        # relationship (matches link_cards' per-type dedup).
        cur_type = add_type_dd.value or "relates_to"
        linked_nums = {
            l["number"] for l in current_links if l["type"] == cur_type
        }
        out: list[dict] = []
        for r in _sc.query_cards(archived=None):
            num = r.get("number")
            if num is None:
                continue
            if card.number is not None and num == card.number:
                continue
            if num in linked_nums:
                continue
            if exact_number:
                if str(num) != q:
                    continue
            elif q not in str(num) and q not in (r.get("title") or "").lower():
                continue
            out.append(r)
        # Surface an exact number match first so it is never buried.
        out.sort(key=lambda r: 0 if str(r.get("number")) == q else 1)
        return out

    def _refresh_results() -> None:
        results_col.controls.clear()
        matches = _matching_cards()
        if not matches:
            results_col.height = 0
        else:
            visible = matches[:5]
            extra = len(matches) - 5
            results_col.height = None
            for r in visible:
                num = r["number"]
                ttl = r.get("title") or ""

                def _pick(e, n=num) -> None:
                    _do_add_link(n)

                results_col.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Text(f"#{num}", size=11, color="#8B949E", width=44),
                                ft.Text(
                                    ttl, size=12, color="#C9D1D9", expand=True,
                                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                            ],
                            spacing=6,
                        ),
                        on_click=_pick,
                        ink=True,
                        border_radius=4,
                        bgcolor="#161B22",
                        padding=ft.Padding(8, 6, 8, 6),
                    )
                )
            if extra > 0:
                results_col.controls.append(
                    ft.Text(
                        f"…and {extra} more — refine your search",
                        size=11, color="#6E7681", italic=True,
                    )
                )
        page.update()

    # Re-filter results when the link type changes (the exclusion set is
    # per-type, so switching type can reveal/hide already-linked cards).
    add_type_dd.on_select = lambda e: _refresh_results()

    def _do_add_link(target_num: int) -> None:
        nonlocal current_links
        if card.number is None:
            link_error.value = "Card has no number (run a sync first)."
            page.update()
            return
        err = operations.link_cards(card.number, target_num, add_type_dd.value or "relates_to")
        if err:
            link_error.value = err
            page.update()
            return
        current_links.append({"type": add_type_dd.value, "number": target_num})
        search_field.value = ""
        link_error.value = ""
        _refresh_results()
        _build_link_rows(edit_mode=True)
        page.update()

    # ── Save / archive / delete ───────────────────────────────────────────────
    def _save(e) -> None:
        nonlocal card, original_lane
        tags = [t.strip() for t in (tags_field.value or "").split(",") if t.strip()]
        assigned = [a.strip() for a in (assigned_field.value or "").split(",") if a.strip()]
        epic_val = epic_dd.value if epic_dd.value else None
        new_col = lane_dd.value or original_lane
        try:
            operations.update_card(
                card_id,
                title=(title_field.value or "").strip() or None,
                description=(desc_field.value or "").strip() or None,
                tags=tags,
                priority=priority_dd.value,
                epic=epic_val,
                assigned_to=assigned,
                due_date=(due_field.value or "").strip() or None,
            )
        except (operations.EpicArchivedError, ValueError) as ex:
            op_error.value = str(ex)
            page.update()
            return
        op_error.value = ""
        if new_col != original_lane:
            operations.move_card(card_id, new_col)
            on_moved(card_id, original_lane, new_col)
            original_lane = new_col
        else:
            on_updated(card_id, original_lane)
        # Re-read updated card and return to view mode
        card = operations.get_card(card_id) or card
        _render("view")

    def _archive(e) -> None:
        operations.archive_card(card_id)
        page.pop_dialog()
        on_archived(card_id, original_lane)

    def _unarchive(e) -> None:
        try:
            operations.unarchive_card(card_id)
        except operations.EpicArchivedError as ex:
            op_error.value = str(ex)
            page.update()
            return
        page.pop_dialog()
        on_archived(card_id, original_lane)

    def _delete(e) -> None:
        def confirm(_) -> None:
            page.pop_dialog()  # close confirm dialog
            ok = operations.delete_card(card_id)
            if ok:
                page.pop_dialog()  # close card dialog
                on_archived(card_id, original_lane)

        confirm_dlg = ft.AlertDialog(
            title=ft.Text("Delete Card"),
            content=ft.Text("Permanently delete this card? This cannot be undone."),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: page.pop_dialog()),
                ft.TextButton("Delete", on_click=confirm, style=ft.ButtonStyle(color="#F07878")),
            ],
        )
        page.show_dialog(confirm_dlg)

    def _close(e) -> None:
        page.pop_dialog()
        if on_close:
            on_close()

    def _view_relationships() -> None:
        from ui.graph_page import open_graph_dialog
        page.pop_dialog()

        def _restore_card_dialog() -> None:
            # Re-open a FRESH card dialog rather than reusing this (now stale)
            # dialog instance.
            if open_card_fn:
                open_card_fn(card_id)

        open_graph_dialog(page, card_id, open_card_fn, on_close=_restore_card_dialog)

    # ── Layout constants & helpers ────────────────────────────────────────────
    _PANEL_HEIGHT = 560
    _SIDEBAR_WIDTH = 300
    _PRIORITY_COLORS = {"low": "#56D364", "medium": "#D29922", "high": "#F07878"}
    _LANE_COLORS = {
        "backlog": "#6E7681",
        "in_progress": "#58A6FF",
        "review": "#D29922",
        "done": "#56D364",
    }

    def _badge(text: str, color: str) -> ft.Container:
        return ft.Container(
            content=ft.Text(text, size=11, color=color, weight=ft.FontWeight.W_500),
            bgcolor="#21262D",
            border_radius=12,
            padding=ft.Padding(10, 4, 10, 4),
            border=ft.Border(
                left=ft.BorderSide(1, color),
                top=ft.BorderSide(1, color),
                right=ft.BorderSide(1, color),
                bottom=ft.BorderSide(1, color),
            ),
        )

    def _sidebar_section(label: str, content: ft.Control) -> ft.Column:
        return ft.Column(
            [
                ft.Text(label, size=10, color="#6E7681", weight=ft.FontWeight.W_600),
                content,
            ],
            spacing=4,
        )

    def _due_widget() -> ft.Control:
        """Due-date chip for the view tab; red when overdue (past and not done)."""
        if not card.due_date:
            return ft.Text("No due date", size=12, color="#6E7681", italic=True)
        overdue = False
        try:
            due = date.fromisoformat(card.due_date)
            overdue = card.lane != "done" and due < date.today()
        except ValueError:
            pass
        color = "#F07878" if overdue else "#C9D1D9"
        label = f"{card.due_date} (overdue)" if overdue else card.due_date
        return ft.Row(
            [
                ft.Icon(ft.Icons.EVENT_OUTLINED, size=13, color=color),
                ft.Text(label, size=13, color=color, weight=ft.FontWeight.W_500 if overdue else None),
            ],
            spacing=4,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # ── View content builder ──────────────────────────────────────────────────
    def _build_view_content() -> ft.Row:
        _build_link_rows(edit_mode=False)

        desc_widget: ft.Control = (
            ft.Markdown(
                card.description,
                selectable=True,
                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK,
                md_style_sheet=ft.MarkdownStyleSheet(
                    p_text_style=ft.TextStyle(color="#C9D1D9", size=14),
                    h1_text_style=ft.TextStyle(color="#E6EDF3", size=22, weight=ft.FontWeight.BOLD),
                    h2_text_style=ft.TextStyle(color="#E6EDF3", size=18, weight=ft.FontWeight.BOLD),
                    h3_text_style=ft.TextStyle(color="#E6EDF3", size=16, weight=ft.FontWeight.W_600),
                    h4_text_style=ft.TextStyle(color="#E6EDF3", size=14, weight=ft.FontWeight.W_600),
                    strong_text_style=ft.TextStyle(color="#E6EDF3", weight=ft.FontWeight.BOLD),
                    em_text_style=ft.TextStyle(color="#C9D1D9", italic=True),
                    code_text_style=ft.TextStyle(color="#E6EDF3", font_family="monospace", size=13),
                    list_bullet_text_style=ft.TextStyle(color="#C9D1D9", size=14),
                    table_head_text_style=ft.TextStyle(color="#E6EDF3", weight=ft.FontWeight.W_600, size=13),
                    table_body_text_style=ft.TextStyle(color="#C9D1D9", size=13),
                    blockquote_decoration=ft.BoxDecoration(
                        border=ft.Border(left=ft.BorderSide(3, "#3E3E42")),
                        bgcolor="#161B22",
                    ),
                    codeblock_decoration=ft.BoxDecoration(
                        bgcolor="#161B22",
                        border_radius=ft.BorderRadius(4, 4, 4, 4),
                    ),
                ),
            )
            if card.description
            else ft.Text("No description.", size=13, color="#6E7681", italic=True)
        )

        left_col = ft.Column(
            [
                ft.Column(
                    [
                        ft.Container(
                            content=ft.Text(
                                f"#{card.number}",
                                size=12,
                                weight=ft.FontWeight.BOLD,
                                color="#1C2A4A",
                                font_family="monospace",
                            ),
                            bgcolor="#DB8132",
                            border_radius=6,
                            padding=ft.Padding(6, 4, 6, 4),
                        ) if card.number else ft.Container(height=0),
                        ft.Text(
                            card.title,
                            size=20,
                            weight=ft.FontWeight.W_700,
                            color="#E6EDF3",
                        ),
                    ],
                    spacing=2,
                ),
                ft.Divider(height=1, color="#3E3E42"),
                desc_widget,
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            height=_PANEL_HEIGHT,
            expand=True,
        )

        p_color = _PRIORITY_COLORS.get(card.priority, "#6E7681")
        c_color = _LANE_COLORS.get(card.lane, "#6E7681")
        tag_chips = (
            ft.Row(
                [
                    ft.Container(
                        content=ft.Text(t, size=11, color="#8B949E"),
                        bgcolor="#21262D",
                        border_radius=10,
                        padding=ft.Padding(8, 3, 8, 3),
                    )
                    for t in card.tags
                ],
                wrap=True,
                spacing=4,
            )
            if card.tags
            else ft.Text("None", size=12, color="#6E7681", italic=True)
        )

        rel_count = len(current_links)
        assigned_chips = (
            ft.Row(
                [
                    ft.Container(
                        content=ft.Text(a, size=11, color="#0d1117"),
                        bgcolor=avatar_color(a),
                        border_radius=10,
                        padding=ft.Padding(8, 3, 8, 3),
                    )
                    for a in card.assigned_to
                ],
                wrap=True,
                spacing=4,
            )
            if card.assigned_to
            else ft.Text("Nobody", size=12, color="#6E7681", italic=True)
        )
        right_col = ft.Column(
            [
                op_error,
                _sidebar_section("LANE", _badge(LANE_LABELS.get(card.lane, card.lane), c_color)),
                _sidebar_section("PRIORITY", _badge(card.priority.capitalize(), p_color)),
                _sidebar_section("EPIC", ft.Text(card.epic or "Unassigned", size=13, color="#C9D1D9")),
                _sidebar_section("DUE", _due_widget()),
                _sidebar_section("ASSIGNED TO", assigned_chips),
                _sidebar_section("TAGS", tag_chips),
                ft.Row(
                    [
                        _sidebar_section(
                            "CREATED",
                            ft.Text(_fmt_date(card.created_at), size=12, color="#C9D1D9"),
                        ),
                        _sidebar_section(
                            "UPDATED",
                            ft.Text(_fmt_date(card.updated_at), size=12, color="#C9D1D9"),
                        ),
                    ],
                    spacing=24,
                ),
                ft.Divider(height=1, color="#3E3E42"),
                _make_fold("rel", f"Relationships ({rel_count})", ft.Column([links_col], spacing=2)),
                _comments_section(editable=True),
            ],
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            height=_PANEL_HEIGHT,
            width=_SIDEBAR_WIDTH,
        )

        return ft.Row(
            [left_col, ft.VerticalDivider(width=1, color="#3E3E42"), right_col],
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    # ── Edit content builder ──────────────────────────────────────────────────
    def _build_edit_content() -> ft.Row:
        _build_link_rows(edit_mode=True)

        left_col = ft.Column(
            [ft.Container(height=5), title_field, desc_field],
            spacing=10,
            height=_PANEL_HEIGHT,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        rel_section = ft.Column(
            [
                links_col,
                ft.Row(
                    [
                        add_type_dd,
                        search_field,
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                results_col,
                link_error,
            ],
            spacing=8,
        )

        right_col = ft.Column(
            [
                ft.Container(height=4),
                ft.Row([priority_dd, lane_dd], spacing=8),
                epic_dd,
                tags_field,
                _tag_suggestions(tags_field),
                assigned_field,
                due_row,
                op_error,
                ft.Divider(height=1, color="#3E3E42"),
                _make_fold("rel", "Relationships", rel_section),
                _comments_section(editable=True),
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            height=_PANEL_HEIGHT,
            width=_SIDEBAR_WIDTH,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        return ft.Row(
            [left_col, ft.VerticalDivider(width=1, color="#3E3E42"), right_col],
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    # ── Render helper ─────────────────────────────────────────────────────────
    def _title_bar(label: str) -> ft.Control | None:
        if not label and not card.archived:
            return None
        children: list[ft.Control] = [
            ft.Text(label, size=16, weight=ft.FontWeight.W_600),
        ]
        if card.archived:
            children.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.ARCHIVE_OUTLINED, size=14, color="#F0883E"),
                            ft.Text("ARCHIVED", size=11, weight=ft.FontWeight.W_700, color="#F0883E"),
                        ],
                        spacing=5,
                        tight=True,
                    ),
                    bgcolor="#3D2611",
                    border_radius=0,
                    padding=ft.Padding(10, 5, 10, 5),
                    border=ft.Border(
                        left=ft.BorderSide(1, "#F0883E"),
                        top=ft.BorderSide(1, "#F0883E"),
                        right=ft.BorderSide(1, "#F0883E"),
                        bottom=ft.BorderSide(1, "#F0883E"),
                    ),
                )
            )
        return ft.Row(
            children,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _render(m: str) -> None:
        op_error.value = ""
        num_str = f"  #{card.number}" if card.number else ""
        if m == "view":
            dlg.title = _title_bar("")
            dlg.content = ft.Container(
                content=_build_view_content(),
                width=960,
                padding=ft.Padding(0, 8, 0, 0),
            )
            if card.archived:
                view_left: list[ft.Control] = [
                    theme.archive_icon_button(archived=True, subject="card", on_click=_unarchive),
                    ft.IconButton(
                        ft.Icons.DELETE_OUTLINE,
                        icon_color="#F07878",
                        tooltip="Delete card",
                        on_click=_delete,
                    ),
                ]
            else:
                view_left = [
                    theme.archive_icon_button(archived=False, subject="card", on_click=_archive),
                    ft.IconButton(
                        ft.Icons.DELETE_OUTLINE,
                        icon_color="#F07878",
                        tooltip="Delete card",
                        on_click=_delete,
                    ),
                ]
            has_links = bool(current_links)
            dlg.actions = [
                *view_left,
                ft.IconButton(
                    ft.Icons.ACCOUNT_TREE_OUTLINED,
                    icon_color="#58A6FF" if has_links else "#484F58",
                    tooltip="View relationships" if has_links else "No relationships to view",
                    disabled=not has_links,
                    on_click=lambda e: _view_relationships(),
                ),
                ft.IconButton(
                    ft.Icons.EDIT_NOTE,
                    icon_color="#ADBAC7",
                    tooltip="Edit card",
                    on_click=lambda e: _render("edit"),
                ),
                ft.IconButton(
                    ft.Icons.CANCEL,
                    icon_color="#ADBAC7",
                    tooltip="Close",
                    on_click=_close,
                ),
            ]
        else:
            dlg.title = _title_bar(f"Edit Card{num_str}")
            dlg.content = ft.Container(
                content=_build_edit_content(),
                width=960,
                padding=ft.Padding(0, 8, 0, 0),
            )
            dlg.actions = [
                ft.IconButton(
                    ft.Icons.SAVE,
                    icon_color="#58A6FF",
                    tooltip="Save",
                    on_click=_save,
                ),
                ft.IconButton(
                    ft.Icons.CANCEL,
                    icon_color="#ADBAC7",
                    tooltip="Cancel",
                    on_click=lambda e: _render("view"),
                ),
            ]
        dlg.actions_alignment = ft.MainAxisAlignment.END
        page.update()

    # ── Initial dialog ────────────────────────────────────────────────────────
    dlg = ft.AlertDialog(modal=True)
    _render("view")
    page.show_dialog(dlg)
