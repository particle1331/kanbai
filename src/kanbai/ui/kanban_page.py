from __future__ import annotations

import flet as ft

from kanbai.board import epics as _epics
from kanbai.board import operations
from kanbai.board.models import LANES
from kanbai.ui import theme
from kanbai.ui.board_view import (
    _archived_card_numbers,
    _compute_visible_numbers,
    apply_filter,
    build_board,
    refresh_lane,
)


class KanbanPage:
    def __init__(self, page: ft.Page, on_open_card, open_create_dialog_fn, on_show_graph=None, on_epic_change=None, on_open_search=None):
        self._page = page
        self._on_open_card = on_open_card
        self._open_create_dialog = open_create_dialog_fn
        self._on_show_graph = on_show_graph
        self._on_epic_change_cb = on_epic_change
        self._on_open_search = on_open_search

        self._current_epic: str | None = None  # None = Unassigned
        self._last_refreshed_epic: str | None = None  # Track last refresh to skip redundant refreshes
        self._show_archived = False
        self._priority_filter: str | None = None  # None = show all priorities
        self._deadline_filter: str | None = None  # None | "due_3d" | "due_1d"
        self._col_lv_ref: dict = {}

        self._epic_dropdown: ft.Dropdown | None = None
        self._archive_btn: ft.IconButton | None = None
        self._board_container: ft.Container | None = None
        self._epic_detail_container: ft.Container | None = None
        self._epic_detail_expanded: bool = False
        self._view: ft.Column | None = None
        self._priority_pills: dict[str, ft.Container] = {}
        self._deadline_pills: dict[str, ft.Container] = {}

    # ── Public API ─────────────────────────────────────────────────────────

    def build(self) -> ft.Column:
        self._epic_dropdown = self._build_dropdown()
        self._archive_btn = self._build_archive_btn()

        new_btn = ft.TextButton(
            "+ New epic",
            style=ft.ButtonStyle(color="#58A6FF"),
            on_click=self._on_new_epic,
        )

        new_card_btn = ft.Container(
            content=ft.Text("+ New card", size=12, color="#FFFFFF", weight=ft.FontWeight.W_500),
            bgcolor="#1F6FEB",
            border_radius=6,
            height=36,
            padding=ft.Padding(14, 0, 14, 0),
            alignment=ft.Alignment(0, 0),
            ink=True,
            on_click=lambda e: self._on_new_card("backlog"),
        )

        graph_btn = ft.IconButton(
            icon=ft.Icons.ACCOUNT_TREE_OUTLINED,
            icon_size=18,
            icon_color="#8B949E",
            tooltip="View this epic in the Graph",
            on_click=self._on_view_graph,
        )

        self._notes_btn = ft.IconButton(
            icon=ft.Icons.DESCRIPTION_OUTLINED,
            icon_size=18,
            icon_color="#8B949E",
            tooltip="Toggle epic description & comments",
            on_click=self._on_toggle_epic_detail,
        )

        show_archived_sw = theme.eye_toggle(
            "Archived",
            False,
            self._on_toggle_archived,
            tooltip="Reveal archived epics in the dropdown and show archived cards at the bottom of each column",
        )

        epic_row = ft.Container(
            content=ft.Row(
                [
                    ft.Row(
                        [
                            ft.Text("Epic:", size=12, color="#8B949E"),
                            self._epic_dropdown,
                            graph_btn,
                            self._notes_btn,
                            new_btn,
                            new_card_btn,
                            ft.VerticalDivider(width=1, color="#3E3E42"),
                            ft.Text("Filter:", size=11, color="#6E7681"),
                            self._build_priority_pills(),
                            ft.Container(width=1, height=16, bgcolor="#3E3E42"),
                            self._build_deadline_pills(),
                        ],
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [
                            ft.IconButton(
                                icon=ft.Icons.SEARCH,
                                icon_size=18,
                                icon_color="#8B949E",
                                tooltip="Search cards  (Ctrl+K / ⌘K)",
                                on_click=lambda e: self._on_open_search() if self._on_open_search else None,
                            ),
                            ft.VerticalDivider(width=1, color="#3E3E42"),
                            show_archived_sw,
                            ft.VerticalDivider(width=1, color="#3E3E42"),
                            self._archive_btn,
                        ],
                        spacing=8,
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

        board_row, col_lv = build_board(
            self._page,
            self._on_open_card,
            self._on_new_card,
            epic=self._current_epic,
            on_move_card=self._on_move_card,
            show_archived=self._show_archived,
            on_reorder=self._on_reorder_card,
            priority_filter=self._priority_filter,
            deadline_filter=self._deadline_filter,
        )
        self._col_lv_ref.update(col_lv)
        self._last_refreshed_epic = self._current_epic

        self._board_container = ft.Container(
            content=board_row,
            padding=ft.Padding(16, 16, 16, 16),
            expand=True,
        )

        self._epic_detail_container = ft.Container(visible=False)
        self._refresh_epic_detail()

        self._view = ft.Column(
            [epic_row, self._epic_detail_container, self._board_container],
            spacing=0,
            expand=True,
        )
        return self._view

    def refresh_board(self) -> None:
        """Refresh all lanes with the current epic filter.

        The visible-number set and hidden-blocker set are computed once here
        instead of being recomputed redundantly inside each refresh_lane call.
        """
        visible_numbers = _compute_visible_numbers(
            self._current_epic,
            self._priority_filter,
            self._show_archived,
            self._deadline_filter,
        )
        hidden_block_numbers = _archived_card_numbers(self._show_archived)
        for col in LANES:
            refresh_lane(
                self._page,
                self._col_lv_ref,
                col,
                self._on_open_card,
                epic=self._current_epic,
                show_archived=self._show_archived,
                on_reorder=self._on_reorder_card,
                priority_filter=self._priority_filter,
                deadline_filter=self._deadline_filter,
                update=False,
                _visible_numbers=visible_numbers,
                _hidden_block_numbers=hidden_block_numbers,
            )
        self._page.update()

    def refresh_epics(self) -> None:
        """Refresh the epic dropdown options (e.g. after agent creates a new epic)."""
        if self._epic_dropdown is None:
            return
        new_dd = self._build_dropdown()
        self._epic_dropdown.options = new_dd.options
        self._epic_dropdown.value = new_dd.value
        new_btn = self._build_archive_btn()
        self._archive_btn.icon = new_btn.icon  # type: ignore[union-attr]
        self._archive_btn.icon_color = new_btn.icon_color  # type: ignore[union-attr]
        self._archive_btn.tooltip = new_btn.tooltip  # type: ignore[union-attr]
        self._archive_btn.disabled = new_btn.disabled  # type: ignore[union-attr]
        self._refresh_epic_detail()
        self._page.update()

    def select_epic(self, epic: str | None) -> None:
        """Programmatically select an epic (None = Unassigned) and refresh the board."""
        # Skip refresh if epic hasn't changed (optimization for page navigation)
        if epic == self._last_refreshed_epic:
            return
        
        self._last_refreshed_epic = epic
        self._current_epic = epic
        self._rebuild_epic_controls()
        self._refresh_epic_detail()
        self.refresh_board()

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _epic_options(self) -> list[ft.dropdown.Option]:
        opts = [
            ft.dropdown.Option(
                key="__unassigned__",
                text="(Unassigned)",
                content=ft.Text(
                    "(Unassigned)",
                    size=13,
                    italic=True,
                    color="#6E7681",
                ),
            )
        ]
        names = _epics.list_epics(include_archived=self._show_archived)
        # Keep active epics in their original order, push archived ones to the bottom
        # so the active-only ordering is unchanged when "Show archived" is off.
        for name in sorted(names, key=lambda n: _epics.is_epic_archived(n)):
            if _epics.is_epic_archived(name):
                content = ft.Text(
                    spans=[
                        ft.TextSpan(name),
                        ft.TextSpan(
                            " (archived)",
                            style=ft.TextStyle(italic=True, color="#6E7681", size=11),
                        ),
                    ],
                    size=13,
                )
                label = f"{name} (archived)"
            else:
                content = ft.Text(name, size=13)
                label = name
            opts.append(ft.dropdown.Option(key=name, content=content, text=label))
        return opts

    def _build_dropdown(self) -> ft.Dropdown:
        options = self._epic_options()
        # Find current selection key
        current_key = "__unassigned__" if self._current_epic is None else self._current_epic
        # Ensure current key is actually in options; fall back to Unassigned
        keys = {o.key for o in options}
        if current_key not in keys:
            current_key = "__unassigned__"
            self._current_epic = None
        return theme.epic_dropdown(
            options=options,
            value=current_key,
            on_select=self._on_epic_change,
        )

    def _build_archive_btn(self) -> ft.IconButton:
        is_archived = bool(self._current_epic and _epics.is_epic_archived(self._current_epic))
        return theme.archive_icon_button(
            archived=is_archived,
            subject="epic",
            on_click=self._on_archive_epic,
            disabled=self._current_epic is None,
        )

    def _rebuild_epic_controls(self) -> None:
        if self._epic_dropdown is None:
            return
        new_dd = self._build_dropdown()
        new_btn = self._build_archive_btn()
        # Replace in-place
        self._epic_dropdown.options = new_dd.options
        self._epic_dropdown.value = new_dd.value
        self._archive_btn.icon = new_btn.icon  # type: ignore[union-attr]
        self._archive_btn.icon_color = new_btn.icon_color  # type: ignore[union-attr]
        self._archive_btn.tooltip = new_btn.tooltip  # type: ignore[union-attr]
        self._archive_btn.disabled = new_btn.disabled  # type: ignore[union-attr]

    # ── Priority filter pills ───────────────────────────────────────────────

    def _build_priority_pills(self) -> ft.Row:
        self._priority_pills = {}
        for key in ("high", "medium", "low"):
            accent = theme.priority_color(key)
            self._priority_pills[key] = ft.Container(
                content=ft.Row(
                    [
                        ft.Container(width=8, height=8, bgcolor=accent, border_radius=4),
                        ft.Text(key.title(), size=11, weight=ft.FontWeight.W_500),
                    ],
                    spacing=5,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding(10, 4, 10, 4),
                border_radius=12,
                on_click=lambda e, k=key: self._set_priority(k),
                ink=True,
                tooltip=f"Show only {key} priority cards (blockers stay visible)",
            )
        self._render_priority_pills()
        return ft.Row(list(self._priority_pills.values()), spacing=6, tight=True)

    def _render_priority_pills(self) -> None:
        for key, pill in self._priority_pills.items():
            accent = theme.priority_color(key)
            active = self._priority_filter == key
            edge = accent if active else "#3E3E42"
            pill.bgcolor = ft.Colors.with_opacity(0.18, accent) if active else "#21262D"
            pill.border = ft.Border(
                ft.BorderSide(1, edge),
                ft.BorderSide(1, edge),
                ft.BorderSide(1, edge),
                ft.BorderSide(1, edge),
            )
            pill.content.controls[1].color = accent if active else "#8B949E"  # type: ignore[union-attr]

    def _set_priority(self, key: str) -> None:
        # Clicking the active pill again clears the filter.
        self._priority_filter = None if self._priority_filter == key else key
        self._render_priority_pills()
        self._apply_filter_only()

    def _apply_filter_only(self) -> None:
        """Fast path for priority/deadline filter changes.

        Reuses the existing tile controls — only toggles their ``visible``
        property. Flet pushes ~1 small update per card instead of
        re-serialising the full control tree, cutting response time from
        several seconds down to < 200 ms.
        """
        visible_numbers = _compute_visible_numbers(
            self._current_epic,
            self._priority_filter,
            self._show_archived,
            self._deadline_filter,
        )
        apply_filter(self._page, self._col_lv_ref, visible_numbers)

    # ── Deadline filter pills ───────────────────────────────────────────────

    # key → (label, tooltip). Colours track the same urgency palette as the
    # due-date chips on the cards: amber for the 3-day window, red for 1-day.
    _DEADLINE_PILLS: dict[str, tuple[str, str, str]] = {
        "due_3d": ("Due ≤3d", "#F0A742", "Show only cards due within 3 days (blockers stay visible)"),
        "due_1d": ("Due ≤1d", "#F85149", "Show only cards due within 1 day (blockers stay visible)"),
    }

    def _build_deadline_pills(self) -> ft.Row:
        self._deadline_pills = {}
        for key, (label, accent, tip) in self._DEADLINE_PILLS.items():
            self._deadline_pills[key] = ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.SCHEDULE, size=12, color=accent),
                        ft.Text(label, size=11, weight=ft.FontWeight.W_500),
                    ],
                    spacing=5,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding(10, 4, 10, 4),
                border_radius=12,
                on_click=lambda e, k=key: self._set_deadline(k),
                ink=True,
                tooltip=tip,
            )
        self._render_deadline_pills()
        return ft.Row(list(self._deadline_pills.values()), spacing=6, tight=True)

    def _render_deadline_pills(self) -> None:
        for key, pill in self._deadline_pills.items():
            accent = self._DEADLINE_PILLS[key][1]
            active = self._deadline_filter == key
            edge = accent if active else "#3E3E42"
            pill.bgcolor = ft.Colors.with_opacity(0.18, accent) if active else "#21262D"
            pill.border = ft.Border(
                ft.BorderSide(1, edge),
                ft.BorderSide(1, edge),
                ft.BorderSide(1, edge),
                ft.BorderSide(1, edge),
            )
            pill.content.controls[1].color = accent if active else "#8B949E"  # type: ignore[union-attr]

    def _set_deadline(self, key: str) -> None:
        # Clicking the active pill again clears the filter.
        self._deadline_filter = None if self._deadline_filter == key else key
        self._render_deadline_pills()
        self._apply_filter_only()

    def _rebuild_board(self) -> None:
        """Rebuild the board from scratch with current epic filter."""
        if self._board_container is None:
            return
        self._col_lv_ref.clear()
        board_row, col_lv = build_board(
            self._page,
            self._on_open_card,
            self._on_new_card,
            epic=self._current_epic,
            on_move_card=self._on_move_card,
            show_archived=self._show_archived,
            on_reorder=self._on_reorder_card,
            priority_filter=self._priority_filter,
            deadline_filter=self._deadline_filter,
        )
        self._col_lv_ref.update(col_lv)
        self._board_container.content = board_row

    # ── Epic detail panel ────────────────────────────────────────────────────

    def _on_toggle_epic_detail(self, e) -> None:
        self._epic_detail_expanded = not self._epic_detail_expanded
        # Update button highlight
        self._notes_btn.icon_color = "#58A6FF" if self._epic_detail_expanded else "#8B949E"
        self._refresh_epic_detail()
        self._page.update()

    def _refresh_epic_detail(self) -> None:
        """Rebuild the epic detail panel content based on current state."""
        if self._epic_detail_container is None:
            return

        # Hide if collapsed or no epic selected
        if not self._epic_detail_expanded or self._current_epic is None:
            self._epic_detail_container.visible = False
            self._epic_detail_container.content = None
            return

        detail = _epics.get_epic_detail(self._current_epic)
        if detail is None:
            self._epic_detail_container.visible = False
            self._epic_detail_container.content = None
            return

        description = detail.get("description", "")
        comments = detail.get("comments", [])

        # ── Description section
        desc_controls: list[ft.Control] = []
        desc_controls.append(
            ft.Row(
                [
                    ft.Icon(ft.Icons.ARTICLE_OUTLINED, size=14, color="#8B949E"),
                    ft.Text("Description", size=12, weight=ft.FontWeight.W_600, color="#C9D1D9"),
                    ft.Container(expand=True),
                    ft.TextButton(
                        "Edit" if description else "Add",
                        style=ft.ButtonStyle(color="#58A6FF"),
                        on_click=self._on_edit_description,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )
        if description:
            desc_controls.append(
                ft.Container(
                    content=ft.Markdown(
                        description,
                        selectable=True,
                        extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
                        code_theme=ft.MarkdownCodeTheme.MONOKAI,
                    ),
                    padding=ft.Padding(8, 4, 8, 8),
                )
            )
        else:
            desc_controls.append(
                ft.Container(
                    content=ft.Text(
                        "No description yet. Click 'Add' to write about this epic.",
                        size=12, italic=True, color="#6E7681",
                    ),
                    padding=ft.Padding(8, 4, 8, 8),
                )
            )

        # ── Comments section
        comment_controls: list[ft.Control] = []
        comment_controls.append(
            ft.Row(
                [
                    ft.Icon(ft.Icons.CHAT_BUBBLE_OUTLINE, size=14, color="#8B949E"),
                    ft.Text(
                        f"Comments ({len(comments)})",
                        size=12, weight=ft.FontWeight.W_600, color="#C9D1D9",
                    ),
                    ft.Container(expand=True),
                    ft.TextButton(
                        "+ Add",
                        style=ft.ButtonStyle(color="#58A6FF"),
                        on_click=self._on_add_comment,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )
        if comments:
            for c in reversed(comments):  # newest first
                ts = c.get("created_at", "")[:16].replace("T", " ")
                comment_controls.append(
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(ts, size=10, color="#6E7681"),
                                        ft.Container(expand=True),
                                        ft.IconButton(
                                            icon=ft.Icons.DELETE_OUTLINE,
                                            icon_size=14,
                                            icon_color="#6E7681",
                                            tooltip="Delete comment",
                                            on_click=lambda e, cid=c["id"]: self._on_delete_comment(cid),
                                        ),
                                    ],
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                ft.Markdown(
                                    c.get("text", ""),
                                    selectable=True,
                                    extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED,
                                    code_theme=ft.MarkdownCodeTheme.MONOKAI,
                                ),
                            ],
                            spacing=2,
                        ),
                        bgcolor="#1C1C1C",
                        border_radius=6,
                        padding=ft.Padding(10, 6, 10, 8),
                        margin=ft.Margin(0, 2, 0, 2),
                    )
                )
        else:
            comment_controls.append(
                ft.Container(
                    content=ft.Text(
                        "No comments yet.",
                        size=12, italic=True, color="#6E7681",
                    ),
                    padding=ft.Padding(8, 4, 8, 8),
                )
            )

        # Left 2/3: description | Right 1/3: comments
        left_col = ft.Container(
            content=ft.Column(
                desc_controls,
                spacing=4,
                scroll=ft.ScrollMode.AUTO,
            ),
            expand=2,
            padding=ft.Padding(0, 0, 12, 0),
        )
        right_col = ft.Container(
            content=ft.Column(
                comment_controls,
                spacing=4,
                scroll=ft.ScrollMode.AUTO,
            ),
            expand=1,
            border=ft.Border(left=ft.BorderSide(1, "#3E3E42")),
            padding=ft.Padding(12, 0, 0, 0),
        )

        panel = ft.Container(
            content=ft.Row(
                [left_col, right_col],
                spacing=0,
                expand=True,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor="#252526",
            border=ft.Border(bottom=ft.BorderSide(1, "#3E3E42")),
            padding=ft.Padding(16, 12, 16, 12),
            height=220,
        )

        self._epic_detail_container.content = panel
        self._epic_detail_container.visible = True

    def _on_edit_description(self, e) -> None:
        """Open a dialog to edit the epic description."""
        current_desc = _epics.get_epic_description(self._current_epic or "")
        desc_field = ft.TextField(
            value=current_desc,
            multiline=True,
            min_lines=30,
            hint_text="Write a markdown description for this epic...",
            bgcolor="#1C1C1C",
            border_color="#3E3E42",
            text_size=13,
            expand=True,
        )

        def save(_):
            text = desc_field.value or ""
            try:
                _epics.set_epic_description(self._current_epic or "", text)
            except ValueError as ex:
                desc_field.error_text = str(ex)
                self._page.update()
                return
            dlg.open = False
            self._refresh_epic_detail()
            self._page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("Edit Epic Description"),
            content=ft.Container(content=desc_field, width=900, height=600),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: setattr(dlg, "open", False) or self._page.update()),
                ft.TextButton("Save", on_click=save),
            ],
        )
        self._page.overlay.append(dlg)
        dlg.open = True
        self._page.update()

    def _on_add_comment(self, e) -> None:
        """Open a dialog to add a comment to the epic."""
        comment_field = ft.TextField(
            multiline=True,
            min_lines=3,
            max_lines=8,
            hint_text="Write a comment (markdown supported)...",
            bgcolor="#1C1C1C",
            border_color="#3E3E42",
            text_size=13,
            autofocus=True,
        )

        def save(_):
            text = (comment_field.value or "").strip()
            if not text:
                comment_field.error_text = "Comment cannot be empty"
                self._page.update()
                return
            try:
                _epics.add_epic_comment(self._current_epic or "", text)
            except ValueError as ex:
                comment_field.error_text = str(ex)
                self._page.update()
                return
            dlg.open = False
            self._refresh_epic_detail()
            self._page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("Add Epic Comment"),
            content=ft.Container(content=comment_field, width=500),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: setattr(dlg, "open", False) or self._page.update()),
                ft.TextButton("Add", on_click=save),
            ],
        )
        self._page.overlay.append(dlg)
        dlg.open = True
        self._page.update()

    def _on_delete_comment(self, comment_id: str) -> None:
        """Delete a comment from the current epic."""
        _epics.delete_epic_comment(self._current_epic or "", comment_id)
        self._refresh_epic_detail()
        self._page.update()

    # ── Event handlers ───────────────────────────────────────────────────────

    def _on_epic_change(self, e) -> None:
        key = self._epic_dropdown.value
        self._current_epic = None if key == "__unassigned__" else key
        self._last_refreshed_epic = self._current_epic
        self._rebuild_epic_controls()
        self._refresh_epic_detail()
        self.refresh_board()
        if self._on_epic_change_cb is not None:
            self._on_epic_change_cb(self._current_epic)

    def _on_view_graph(self, e) -> None:
        if self._on_show_graph is not None:
            self._on_show_graph(self._current_epic)

    def _on_toggle_archived(self, value: bool) -> None:
        self._show_archived = bool(value)
        # Rebuild dropdown options in place; _build_dropdown may reset
        # self._current_epic if the selected epic is no longer visible.
        if self._epic_dropdown:
            new_dd = self._build_dropdown()
            self._epic_dropdown.options = new_dd.options
            self._epic_dropdown.value = new_dd.value
            new_btn = self._build_archive_btn()
            self._archive_btn.icon = new_btn.icon  # type: ignore[union-attr]
            self._archive_btn.icon_color = new_btn.icon_color  # type: ignore[union-attr]
            self._archive_btn.tooltip = new_btn.tooltip  # type: ignore[union-attr]
            self._archive_btn.disabled = new_btn.disabled  # type: ignore[union-attr]
        # In-place column refresh (swap tile children only) is far cheaper than
        # rebuilding the whole board (columns, DragTargets, headers) from scratch.
        self.refresh_board()

    def _on_new_epic(self, e) -> None:
        name_field = ft.TextField(
            hint_text="Epic name",
            autofocus=True,
            bgcolor="#2D2D2D",
            border_color="#3E3E42",
        )

        def confirm(_):
            name = (name_field.value or "").strip()
            if not name:
                return
            existing = {e.strip().lower() for e in _epics.list_epics(include_archived=True)}
            if name.lower() in existing:
                name_field.error_text = "An epic with that name already exists"
                self._page.update()
                return
            _epics.create_epic(name)
            dlg.open = False
            self._rebuild_epic_controls()
            self._page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("New Epic"),
            content=name_field,
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: setattr(dlg, "open", False) or self._page.update()),
                ft.TextButton("Create", on_click=confirm),
            ],
        )
        self._page.overlay.append(dlg)
        dlg.open = True
        self._page.update()

    def _on_archive_epic(self, e) -> None:
        if self._current_epic is None:
            return
        epic = self._current_epic
        is_archived = _epics.is_epic_archived(epic)

        if is_archived:
            def confirm(_):
                dlg.open = False
                operations.unarchive_epic_op(epic)
                self._rebuild_epic_controls()
                self.refresh_board()
        else:
            def confirm(_):
                dlg.open = False
                operations.archive_epic_op(epic)
                self._current_epic = None
                self._last_refreshed_epic = None
                self._rebuild_epic_controls()
                self.refresh_board()

        action = "Unarchive" if is_archived else "Archive"
        msg = (
            f"Unarchive epic '{epic}'? Its archived cards will be restored."
            if is_archived
            else f"Archive epic '{epic}'? All its active cards will be archived."
        )
        dlg = ft.AlertDialog(
            title=ft.Text(f"{action} Epic"),
            content=ft.Text(msg),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: setattr(dlg, "open", False) or self._page.update()),
                ft.TextButton(action, on_click=confirm),
            ],
        )
        self._page.overlay.append(dlg)
        dlg.open = True
        self._page.update()

    def _on_new_card(self, lane: str) -> None:
        if self._current_epic and _epics.is_epic_archived(self._current_epic):
            info = ft.AlertDialog(
                title=ft.Text("Epic archived"),
                content=ft.Text(
                    f"Epic '{self._current_epic}' is archived — unarchive it before adding cards."
                ),
                actions=[
                    ft.TextButton(
                        "OK",
                        on_click=lambda _: setattr(info, "open", False) or self._page.update(),
                    ),
                ],
            )
            self._page.overlay.append(info)
            info.open = True
            self._page.update()
            return

        def on_created(card):
            refresh_lane(
                self._page,
                self._col_lv_ref,
                card.lane,
                self._on_open_card,
                epic=self._current_epic,
                show_archived=self._show_archived,
                priority_filter=self._priority_filter,
                deadline_filter=self._deadline_filter,
            )
        self._open_create_dialog(self._page, lane, on_created, self._current_epic)

    def _on_move_card(self, card_id: str, from_column: str, to_column: str) -> None:
        operations.move_card(card_id, to_column)
        refresh_lane(
            self._page,
            self._col_lv_ref,
            from_column,
            self._on_open_card,
            epic=self._current_epic,
            show_archived=self._show_archived,
            priority_filter=self._priority_filter,
            deadline_filter=self._deadline_filter,
        )
        refresh_lane(
            self._page,
            self._col_lv_ref,
            to_column,
            self._on_open_card,
            epic=self._current_epic,
            show_archived=self._show_archived,
            priority_filter=self._priority_filter,
            deadline_filter=self._deadline_filter,
        )

    def _on_reorder_card(
        self,
        card_id: str,
        from_column: str,
        to_column: str,
        before_id: str | None,
    ) -> None:
        """Place ``card_id`` into ``to_column``, before ``before_id`` (or at the
        bottom when ``before_id`` is None), then persist the new order."""
        if card_id == before_id:
            return
        active = operations.list_cards(
            lane=to_column, archived=False, epic=self._current_epic
        )
        ids = [c["id"] for c in active if c["id"] != card_id]
        if before_id is not None and before_id in ids:
            ids.insert(ids.index(before_id), card_id)
        else:
            ids.append(card_id)
        operations.reorder_cards(to_column, ids)
        if from_column != to_column:
            refresh_lane(
                self._page,
                self._col_lv_ref,
                from_column,
                self._on_open_card,
                epic=self._current_epic,
                show_archived=self._show_archived,
                on_reorder=self._on_reorder_card,
                priority_filter=self._priority_filter,
                deadline_filter=self._deadline_filter,
            )
        refresh_lane(
            self._page,
            self._col_lv_ref,
            to_column,
            self._on_open_card,
            epic=self._current_epic,
            show_archived=self._show_archived,
            on_reorder=self._on_reorder_card,
            priority_filter=self._priority_filter,
            deadline_filter=self._deadline_filter,
        )
