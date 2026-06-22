from __future__ import annotations

import subprocess
import sys
import threading
import flet as ft
from collections.abc import Callable
from datetime import datetime, date
from pathlib import Path

# ── Dialog layout constants ───────────────────────────────────────────────────
_DIALOG_WIDTH = 810
_DIALOG_HEIGHT = 660
_PANEL_HEIGHT = 660
_SIDEBAR_WIDTH = 280

from kanbai.board import epics as _epics
from kanbai.board import operations
from kanbai.board import settings
from kanbai.board.models import Card, LANE_LABELS, LANES
from kanbai.board.settings import DEFAULT_TAGS
from kanbai.board.storage import sqlite_cache as _sc
from kanbai.ui import avatar_color
from kanbai.ui import theme


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


def _get_tag_suggestions() -> list[str]:
    """Return tag suggestions: existing DB tags (by count) + DEFAULT_TAGS."""
    db_tags = [t for t, _ in _sc.count_cards_by_tag(limit=50)]
    seen = set(db_tags)
    extra = [t for t in DEFAULT_TAGS if t not in seen]
    return db_tags + extra


def _chip_input(
    page: ft.Page,
    label: str,
    initial: list[str],
    all_suggestions: list[str],
    max_items: int = 10,
    accent: str = "#58A6FF",
) -> tuple[ft.Column, "Callable[[], list[str]]"]:
    """
    Multi-value chip input with live autocomplete.
    Returns (widget_column, get_values_fn).
    """
    values: list[str] = list(initial)
    chips_row = ft.Row(wrap=True, spacing=4, run_spacing=4)
    input_field = ft.TextField(
        label=label,
        hint_text="Type and press Enter",
        text_size=12,
        height=42,
        content_padding=ft.Padding(10, 0, 10, 0),
    )
    suggestions_row = ft.Row(wrap=True, spacing=4, run_spacing=4)

    def _render_chips() -> None:
        chips_row.controls.clear()
        chips_row.visible = bool(values)
        for v in list(values):
            def _remove(e, val=v):
                if val in values:
                    values.remove(val)
                _render_chips()
                _render_suggestions()
                page.update()
            chips_row.controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Text(v, size=11, color=accent),
                            ft.Text("×", size=12, color=accent),
                        ],
                        spacing=4,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor="#21262D",
                    border_radius=10,
                    padding=ft.Padding(8, 3, 8, 3),
                    on_click=_remove,
                    ink=True,
                )
            )

    def _filtered() -> list[str]:
        q = (input_field.value or "").strip().lower()
        result = []
        for s in all_suggestions:
            if s in values:
                continue
            if not q or q in s.lower():
                result.append(s)
        return result[:5]

    def _render_suggestions() -> None:
        suggestions_row.controls.clear()
        filtered = _filtered()
        suggestions_row.visible = bool(filtered)
        for s in filtered:
            def _pick(e, val=s):
                if val not in values and len(values) < max_items:
                    values.append(val)
                input_field.value = ""
                _render_chips()
                _render_suggestions()
                page.update()
            suggestions_row.controls.append(
                ft.Container(
                    content=ft.Text(s, size=10, color="#8B949E"),
                    on_click=_pick,
                    ink=True,
                    border_radius=8,
                    padding=ft.Padding(6, 2, 6, 2),
                )
            )

    def _on_change(e):
        _render_suggestions()
        page.update()

    def _on_submit(e):
        val = (input_field.value or "").strip()
        if val and val not in values and len(values) < max_items:
            values.append(val)
        input_field.value = ""
        _render_chips()
        _render_suggestions()
        page.update()

    input_field.on_change = _on_change
    input_field.on_submit = _on_submit

    _render_chips()
    _render_suggestions()

    widget = ft.Column(
        [
            chips_row,
            input_field,
            suggestions_row,
        ],
        spacing=5,
    )
    return widget, lambda: list(values)


def open_create_dialog(page: ft.Page, default_lane: str, on_created, default_epic: str | None = None) -> None:
    title_field = ft.TextField(label="Title", autofocus=True)
    desc_field = ft.TextField(
        label="Description",
        multiline=True,
        expand=True,
    )
    _tag_sugg = _get_tag_suggestions()
    _assignee_sugg = _sc.get_all_assignees()
    tags_widget, get_tags = _chip_input(page, "Tags", [], _tag_sugg)
    assigned_widget, get_assigned = _chip_input(page, "Assigned to", [], _assignee_sugg, accent="#DB8132")
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

    _prev_kb_handler_create = page.on_keyboard_event

    def _restore_kb_create() -> None:
        page.on_keyboard_event = _prev_kb_handler_create

    def _on_key_create(e: ft.KeyboardEvent) -> None:
        if e.key == "Escape":
            _restore_kb_create()
            page.pop_dialog()
            return
        if _prev_kb_handler_create:
            _prev_kb_handler_create(e)  # type: ignore[call-arg]

    page.on_keyboard_event = _on_key_create

    def _save(e):
        if _saving[0]:
            return
        if not title_field.value or not title_field.value.strip():
            error_text.value = "Title is required."
            page.update()
            return
        _saving[0] = True
        tags = get_tags()
        assigned = get_assigned()
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
        _restore_kb_create()
        page.pop_dialog()
        on_created(card)

    def _cancel(e):
        _restore_kb_create()
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
        actions_alignment=ft.MainAxisAlignment.END,
        content=ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        content=ft.Column(
                            [ft.Container(height=5), title_field, desc_field],
                            spacing=10,
                            expand=True,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                        height=_PANEL_HEIGHT,
                        expand=True,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    ),
                    ft.VerticalDivider(width=1, color="#3E3E42"),
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Container(height=4),
                                priority_dd,
                                tags_widget,
                                ft.Container(height=4),
                                assigned_widget,
                                ft.Container(height=4),
                                due_row,
                                error_text,
                                ft.Divider(height=1, color="#3E3E42"),
                                rel_section,
                            ],
                            spacing=12,
                            scroll=ft.ScrollMode.AUTO,
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                        height=_PANEL_HEIGHT,
                        width=_SIDEBAR_WIDTH,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                    ),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            width=_DIALOG_WIDTH,
            height=_DIALOG_HEIGHT,
            padding=ft.Padding(0, 8, 0, 0),
        ),
        actions=[
            ft.FilledButton(
                "Create", 
                color="#FFFFFF", 
                bgcolor="#1F6FEB", 
                on_click=_save,
                style=ft.ButtonStyle(
                    shape=ft.RoundedRectangleBorder(radius=5),
                    padding=ft.Padding(90, 0, 90, 0),
                )
            ),
            ft.IconButton(
                icon=ft.Icons.CANCEL,
                icon_color="#ADBAC7",
                tooltip="Cancel",
                on_click=_cancel,
            ),
        ],
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
    _raw_card = operations.get_card(card_id)
    if not _raw_card:
        return
    card: Card = _raw_card

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
    _tag_sugg = _get_tag_suggestions()
    _assignee_sugg = _sc.get_all_assignees()
    tags_widget, get_tags = _chip_input(page, "Tags", card.tags, _tag_sugg)
    assigned_widget, get_assigned = _chip_input(page, "Assigned to", card.assigned_to, _assignee_sugg, accent="#DB8132")
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
    fold_state = {"rel": False, "comments": False, "att": False}

    def _make_fold(state_key: str, title: "str | ft.Text", body: ft.Control) -> ft.Column:
        is_open = fold_state.get(state_key, False)
        toggle_icon = ft.Text(
            "−" if is_open else "+",
            size=16, weight=ft.FontWeight.W_700, color="#8B949E",
        )
        body.visible = is_open
        title_widget = title if isinstance(title, ft.Text) else ft.Text(
            title, size=12, weight=ft.FontWeight.W_600, color="#C9D1D9"
        )

        def _toggle(e) -> None:
            new = not fold_state.get(state_key, False)
            fold_state[state_key] = new
            body.visible = new
            toggle_icon.value = "−" if new else "+"
            page.update()

        header = ft.Container(
            content=ft.Row(
                [toggle_icon, title_widget],
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
                def _do() -> None:
                    nonlocal card
                    if cid is not None:
                        operations.delete_comment(card_id, cid)
                    _refreshed = operations.get_card(card_id)
                    if _refreshed is not None:
                        card = _refreshed
                    _build_comments()
                    page.update()
                threading.Thread(target=_do, daemon=True).start()

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
        comment_field.value = ""
        comment_field.disabled = True
        page.update()

        def _do() -> None:
            nonlocal card
            operations.add_comment(card_id, txt)
            _refreshed = operations.get_card(card_id)
            if _refreshed is not None:
                card = _refreshed
            comment_field.disabled = False
            _build_comments()
            page.update()

        threading.Thread(target=_do, daemon=True).start()

    def _comments_section(editable: bool) -> ft.Control:
        _build_comments()
        children: list[ft.Control] = [comments_col]
        if editable:
            children.append(comment_field)
        body = ft.Column(children, spacing=8)
        return _make_fold("comments", f"Comments ({len(card.comments)})", body)

    # ── Attachments ───────────────────────────────────────────────────────────
    att_error = ft.Text("", color=ft.Colors.RED_400, size=11)
    att_col = ft.Column(spacing=4)

    # FilePicker is a Service — it auto-registers with page._services on init.
    # Do NOT add it to page.overlay (that causes "Unknown control: FilePicker").
    file_picker = ft.FilePicker()

    def _fmt_size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"

    def _ext_icon(filename: str) -> ft.IconData:
        ext = Path(filename).suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".ico"}:
            return ft.Icons.IMAGE_OUTLINED
        if ext == ".pdf":
            return ft.Icons.PICTURE_AS_PDF_OUTLINED
        if ext in {".zip", ".gz", ".tar", ".bz2", ".7z", ".rar", ".xz"}:
            return ft.Icons.FOLDER_ZIP_OUTLINED
        if ext in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
            return ft.Icons.VIDEO_FILE_OUTLINED
        if ext in {".mp3", ".wav", ".flac", ".m4a", ".ogg"}:
            return ft.Icons.AUDIO_FILE_OUTLINED
        if ext in {".py", ".js", ".ts", ".html", ".css", ".json",
                   ".md", ".txt", ".csv", ".xml", ".yaml", ".yml",
                   ".toml", ".ini", ".cfg", ".rst"}:
            return ft.Icons.CODE
        return ft.Icons.ATTACH_FILE

    def _open_attachment(filename: str) -> None:
        att_path = operations.get_attachment_path(card_id, filename)
        if not att_path.exists():
            att_error.value = f"File not found: {filename}"
            page.update()
            return
        path_str = str(att_path)
        if sys.platform == "darwin":
            subprocess.run(["open", path_str])
        elif sys.platform == "win32":
            import os
            os.startfile(path_str)
        else:
            subprocess.run(["xdg-open", path_str])

    _att_fold_title = ft.Text(
        f"Attachments ({len(card.attachments)})",
        size=12, weight=ft.FontWeight.W_600, color="#C9D1D9",
    )

    def _build_attachments() -> None:
        _att_fold_title.value = f"Attachments ({len(card.attachments)})"
        att_col.controls.clear()
        if not card.attachments:
            att_col.controls.append(
                ft.Text("No attachments.", size=11, color="#6E7681", italic=True)
            )
        else:
            for fname in list(card.attachments):
                att_path = operations.get_attachment_path(card_id, fname)
                size_str = ""
                try:
                    if att_path.exists():
                        size_str = f" · {_fmt_size(att_path.stat().st_size)}"
                except OSError:
                    pass

                def _open(e, fn=fname) -> None:
                    _open_attachment(fn)

                def _del_att(e, fn=fname) -> None:
                    nonlocal card
                    err = operations.remove_attachment(card_id, fn)
                    if err:
                        att_error.value = err
                        page.update()
                        return
                    att_error.value = ""
                    _refreshed = operations.get_card(card_id)
                    if _refreshed is not None:
                        card = _refreshed
                    _build_attachments()
                    page.update()

                att_col.controls.append(
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(_ext_icon(fname), size=14, color="#8B949E"),
                                ft.Text(
                                    fname + size_str,
                                    size=12,
                                    color="#C9D1D9",
                                    expand=True,
                                    max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                                ft.IconButton(
                                    ft.Icons.OPEN_IN_NEW,
                                    icon_size=13,
                                    icon_color="#58A6FF",
                                    on_click=_open,
                                    tooltip="Open file",
                                    padding=ft.Padding(0, 0, 0, 0),
                                ),
                                ft.IconButton(
                                    ft.Icons.CANCEL,
                                    icon_size=13,
                                    icon_color="#6E7681",
                                    on_click=_del_att,
                                    tooltip="Remove attachment",
                                    padding=ft.Padding(0, 0, 0, 0),
                                ),
                            ],
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        bgcolor="#161B22",
                        border_radius=6,
                        padding=ft.Padding(8, 5, 4, 5),
                    )
                )
        # "Add files" button — always present
        att_col.controls.append(
            ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.ATTACH_FILE, size=13, color="#8B949E"),
                        ft.Text("Add files…", size=11, color="#8B949E"),
                    ],
                    spacing=4,
                    tight=True,
                ),
                on_click=_pick_files,
                ink=True,
                border_radius=6,
                bgcolor="#21262D",
                padding=ft.Padding(8, 5, 8, 5),
            )
        )
        att_col.controls.append(att_error)

    def _pick_files(e) -> None:
        async def _do() -> None:
            nonlocal card
            # In web mode (WEB_BROWSER view) the browser JS client cannot
            # expose server-side file paths, so f.path is always None.
            # Request raw bytes instead so we can write the file ourselves.
            use_bytes = page.web
            files = await file_picker.pick_files(
                dialog_title="Attach files to card",
                allow_multiple=True,
                with_data=use_bytes,
            )
            if not files:
                return
            errors: list[str] = []
            for f in files:
                if f.path:
                    err = operations.add_attachment(card_id, f.path)
                elif f.bytes is not None:
                    err = operations.add_attachment_bytes(card_id, f.name, f.bytes)
                else:
                    errors.append(f"{f.name}: no file data available")
                    continue
                if err:
                    errors.append(err)
            att_error.value = "; ".join(errors) if errors else ""
            _refreshed = operations.get_card(card_id)
            if _refreshed is not None:
                card = _refreshed
            _build_attachments()
            page.update()

        page.run_task(_do)

    _build_attachments()

    def _attachments_section() -> ft.Control:
        return _make_fold(
            "att",
            _att_fold_title,
            ft.Column([att_col], spacing=2),
        )

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
                    _cleanup_picker()
                    _restore_kb()
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
        tags = get_tags()
        assigned = get_assigned()
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
        _refreshed = operations.get_card(card_id)
        if _refreshed is not None:
            card = _refreshed
        _render("view")

    def _cleanup_picker() -> None:
        """No-op: FilePicker is a Service and auto-unregisters when unreferenced."""
        pass

    # ── ESC / keyboard handler ─────────────────────────────────────────
    _prev_kb_handler = page.on_keyboard_event
    _current_mode: list[str] = ["view"]

    def _restore_kb() -> None:
        page.on_keyboard_event = _prev_kb_handler

    def _on_key(e: ft.KeyboardEvent) -> None:
        if e.key == "Escape":
            if _current_mode[0] == "edit":
                # In edit mode: ESC cancels edits and returns to view
                _render("view")
            else:
                # In view mode: ESC closes the dialog
                _cleanup_picker()
                _restore_kb()
                page.pop_dialog()
                if on_close:
                    on_close()
            return
        if _prev_kb_handler:
            _prev_kb_handler(e)  # type: ignore[call-arg]

    page.on_keyboard_event = _on_key

    def _archive(e) -> None:
        operations.archive_card(card_id)
        _cleanup_picker()
        _restore_kb()
        page.pop_dialog()
        on_archived(card_id, original_lane)

    def _unarchive(e) -> None:
        try:
            operations.unarchive_card(card_id)
        except operations.EpicArchivedError as ex:
            op_error.value = str(ex)
            page.update()
            return
        _cleanup_picker()
        _restore_kb()
        page.pop_dialog()
        on_archived(card_id, original_lane)

    def _delete(e) -> None:
        """Show a separate confirmation popup for permanent deletion."""
        prev_kb = page.on_keyboard_event

        def _finish_delete() -> bool:
            ok = operations.delete_card(card_id)
            if not ok:
                # Some installs still enforce archive-first permanent deletion.
                archived = operations.archive_card(card_id)
                ok = bool(archived) and operations.delete_card(card_id)
            return ok

        def _cancel_dlg(_) -> None:
            page.on_keyboard_event = prev_kb
            page.pop_dialog()

        def _confirm_delete(_) -> None:
            page.on_keyboard_event = prev_kb
            page.pop_dialog()  # close confirm dialog
            ok = _finish_delete()
            if ok:
                _cleanup_picker()
                _restore_kb()
                page.pop_dialog()  # close card dialog
                on_archived(card_id, original_lane)
            else:
                op_error.value = "Couldn't delete card."
                page.update()

        def _on_delete_key(ev: ft.KeyboardEvent) -> None:
            if ev.key == "Escape":
                _cancel_dlg(None)
                return
            if ev.key == "Enter":
                _confirm_delete(None)
                return
            if prev_kb:
                prev_kb(ev)  # type: ignore[call-arg]

        confirm_dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Delete this card permanently?"),
            content=ft.Text("This cannot be undone."),
            actions=[
                ft.TextButton("Cancel", icon=ft.Icons.CANCEL, on_click=_cancel_dlg),
                ft.TextButton(
                    "Delete",
                    icon=ft.Icons.DELETE_FOREVER,
                    on_click=_confirm_delete,
                    style=ft.ButtonStyle(color="#F07878"),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.on_keyboard_event = _on_delete_key
        page.show_dialog(confirm_dlg)

    def _close(e) -> None:
        _cleanup_picker()
        _restore_kb()
        page.pop_dialog()
        if on_close:
            on_close()

    def _view_relationships() -> None:
        from kanbai.ui.graph_page import open_graph_dialog
        _cleanup_picker()
        _restore_kb()
        page.pop_dialog()

        def _restore_card_dialog() -> None:
            # Re-open a FRESH card dialog rather than reusing this (now stale)
            # dialog instance.
            if open_card_fn:
                open_card_fn(card_id)

        open_graph_dialog(page, card_id, open_card_fn, on_close=_restore_card_dialog)

    # ── Layout constants & helpers ────────────────────────────────────────────
    # layout constants inherited from module level: _PANEL_HEIGHT, _SIDEBAR_WIDTH
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

        left_col = ft.Container(
            content=ft.Column(
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
            ),
            height=_PANEL_HEIGHT,
            expand=True,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
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
        right_col_inner = ft.Column(
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
                _attachments_section(),
            ],
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
        )
        right_col = ft.Container(
            content=right_col_inner,
            height=_PANEL_HEIGHT,
            width=_SIDEBAR_WIDTH,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )

        return ft.Row(
            [left_col, ft.VerticalDivider(width=1, color="#3E3E42"), right_col],
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    # ── Edit content builder ──────────────────────────────────────────────────
    def _build_edit_content() -> ft.Row:
        _build_link_rows(edit_mode=True)

        left_col = ft.Container(
            content=ft.Column(
                [ft.Container(height=5), title_field, desc_field],
                spacing=10,
                expand=True,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            height=_PANEL_HEIGHT,
            expand=True,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
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

        right_col = ft.Container(
            content=ft.Column(
                [
                    ft.Container(height=4),
                    ft.Row([priority_dd, lane_dd], spacing=8),
                    epic_dd,
                    tags_widget,
                    ft.Container(height=4),
                    assigned_widget,
                    ft.Container(height=4),
                    due_row,
                    op_error,
                    ft.Divider(height=1, color="#3E3E42"),
                    _make_fold("rel", "Relationships", rel_section),
                    _comments_section(editable=True),
                    _attachments_section(),
                ],
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            height=_PANEL_HEIGHT,
            width=_SIDEBAR_WIDTH,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
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
        _current_mode[0] = m
        op_error.value = ""
        num_str = f"  #{card.number}" if card.number else ""
        if m == "view":
            dlg.title = _title_bar("")
            dlg.content = ft.Container(
                content=_build_view_content(),
                width=_DIALOG_WIDTH,
                height=_DIALOG_HEIGHT,
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
                width=_DIALOG_WIDTH,
                height=_DIALOG_HEIGHT,
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
