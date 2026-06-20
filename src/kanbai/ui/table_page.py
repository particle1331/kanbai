from __future__ import annotations

import flet as ft

from kanbai.board import epics as _epics
from kanbai.board import operations
from kanbai.board.models import LANE_LABELS, LANES
from kanbai.ui import theme

_ALL = "__all__"
_UNASSIGNED = "__none__"
_PAGE_SIZE = 50

_PRIORITY_COLORS = theme.PRIORITY_COLORS
_PRIORITY_RANK = {"low": 0, "medium": 1, "high": 2}
_LANE_COLORS = theme.STATUS_COLORS


class TablePage:
    def __init__(self, page: ft.Page, on_open_card):
        self._page = page
        self._on_open_card = on_open_card
        self._show_archived = False
        self._sort_col: int = 8   # Updated column (index)
        self._sort_asc: bool = False
        self._table: ft.DataTable | None = None
        self._view: ft.Column | None = None
        self._count_text: ft.Text | None = None
        self._page_info: ft.Text | None = None
        self._prev_btn: ft.IconButton | None = None
        self._next_btn: ft.IconButton | None = None
        self._current_page: int = 0
        self._total_pages: int = 1
        self._last_refreshed_epic: str | None = "__none__"  # Track last refresh to skip redundant refreshes

        # Filter state
        self._search: str = ""
        self._filter_epic: str = _ALL
        self._filter_status: str = _ALL
        self._filter_priority: str = _ALL
        self._filter_tag: str = _ALL
        self._filter_due: str = _ALL

        # Filter controls (built in build())
        self._search_field: ft.TextField | None = None
        self._epic_dd: ft.Dropdown | None = None
        self._status_dd: ft.Dropdown | None = None
        self._priority_dd: ft.Dropdown | None = None
        self._tag_dd: ft.Dropdown | None = None
        self._due_dd: ft.Dropdown | None = None
        self._row_count: int = 0  # set by _build_table; reused by _count_label

    # ── Public API ─────────────────────────────────────────────────────────

    def build(self) -> ft.Column:
        self._table = self._build_table()

        show_archived_sw = theme.eye_toggle(
            "Archived",
            False,
            self._on_toggle_archived,
            tooltip="Show archived cards in the table",
        )

        self._search_field = ft.TextField(
            hint_text="Search title or description…",
            value="",
            width=240,
            height=36,
            text_size=12,
            bgcolor="#1E1E1E",
            border_color="#3E3E42",
            color="#C9D1D9",
            cursor_color="#58A6FF",
            content_padding=ft.Padding(10, 6, 10, 6),
            prefix_icon=ft.Icons.SEARCH,
            on_change=self._on_search_change,
        )

        self._epic_dd = self._make_filter_dropdown(
            "Epic", self._epic_options(), self._on_epic_change
        )
        self._status_dd = self._make_filter_dropdown(
            "Status", self._status_options(), self._on_status_change
        )
        self._priority_dd = self._make_filter_dropdown(
            "Priority", self._priority_options(), self._on_priority_change
        )
        self._tag_dd = self._make_filter_dropdown(
            "Tag", self._tag_options(), self._on_tag_change
        )

        self._due_dd = self._make_filter_dropdown(
            "Due", self._due_options(), self._on_due_change
        )

        clear_btn = ft.TextButton(
            "Clear filters",
            icon=ft.Icons.FILTER_ALT_OFF,
            style=ft.ButtonStyle(color="#8B949E"),
            on_click=self._on_clear_filters,
        )

        self._count_text = ft.Text(self._count_label(), size=11, color="#6E7681")

        self._prev_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_LEFT,
            icon_size=18,
            icon_color="#8B949E",
            tooltip="Previous page",
            on_click=self._on_prev_page,
            disabled=True,
        )
        self._next_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_RIGHT,
            icon_size=18,
            icon_color="#8B949E",
            tooltip="Next page",
            on_click=self._on_next_page,
            disabled=True,
        )
        self._page_info = ft.Text(self._page_label(), size=11, color="#6E7681")

        toolbar = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            self._search_field,
                            self._epic_dd,
                            self._status_dd,
                            self._priority_dd,
                            self._tag_dd,
                            self._due_dd,
                            clear_btn,
                        ],
                        spacing=8,
                        wrap=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row(
                        [
                            show_archived_sw,
                            ft.Container(expand=True),
                            self._count_text,
                            ft.Container(width=16),
                            self._prev_btn,
                            self._page_info,
                            self._next_btn,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                spacing=8,
            ),
            padding=ft.Padding(12, 10, 12, 10),
            bgcolor="#252526",
            border=ft.Border(bottom=ft.BorderSide(1, "#3E3E42")),
        )

        self._view = ft.Column(
            [
                toolbar,
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Column(
                                [self._table],
                                scroll=ft.ScrollMode.ALWAYS,
                                expand=True,
                            ),
                        ],
                        scroll=ft.ScrollMode.AUTO,
                        expand=True,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                    ),
                    expand=True,
                    padding=ft.Padding(16, 8, 16, 8),
                ),
            ],
            spacing=0,
            expand=True,
        )
        return self._view

    def refresh(self) -> None:
        """Full re-render: rebuild rows AND filter option lists. Use after
        data/filter changes."""
        self._rerender(refresh_filters=True)

    def select_epic(self, epic: str | None) -> None:
        """Sync the shared epic selection onto the table's epic filter.

        None (no specific epic) maps to "All epics"; a concrete name selects
        that epic. Keeps the Table consistent with the other views' selection.
        """
        new_epic = epic if epic else _ALL
        # Skip refresh if epic hasn't changed (optimization for page navigation)
        if new_epic == self._last_refreshed_epic:
            return
        
        self._last_refreshed_epic = new_epic
        self._filter_epic = new_epic
        self.refresh()

    def _rerender(self, refresh_filters: bool) -> None:
        if self._table is None:
            return
        new_table = self._build_table()
        self._table.columns = new_table.columns
        self._table.rows = new_table.rows
        self._table.sort_column_index = self._sort_col
        self._table.sort_ascending = self._sort_asc
        if self._count_text is not None:
            self._count_text.value = self._count_label()
        self._update_pagination_controls()
        if refresh_filters:
            self._refresh_filter_options()
        self._page.update()

    # ── Filter controls ───────────────────────────────────────────────

    def _make_filter_dropdown(self, label: str, options, on_select) -> ft.Dropdown:
        return ft.Dropdown(
            label=label,
            value=_ALL,
            options=options,
            width=160,
            text_size=12,
            bgcolor="#1E1E1E",
            border_color="#3E3E42",
            color="#C9D1D9",
            label_style=ft.TextStyle(size=11, color="#8B949E"),
            on_select=on_select,
        )

    def _all_cards(self) -> list[dict]:
        """Every card regardless of archived state (for filter option lists)."""
        return operations.list_cards(archived=None)

    def _epic_options(self) -> list[ft.dropdown.Option]:
        names = _epics.list_epics(include_archived=True)
        opts = [ft.dropdown.Option(_ALL, "All epics")]
        opts.append(ft.dropdown.Option(_UNASSIGNED, "Unassigned"))
        opts.extend(ft.dropdown.Option(n, n) for n in sorted(names, key=str.lower))
        return opts

    def _status_options(self) -> list[ft.dropdown.Option]:
        opts = [ft.dropdown.Option(_ALL, "All statuses")]
        opts.extend(ft.dropdown.Option(c, LANE_LABELS.get(c, c)) for c in LANES)
        return opts

    def _priority_options(self) -> list[ft.dropdown.Option]:
        return [
            ft.dropdown.Option(_ALL, "All priorities"),
            ft.dropdown.Option("high", "High"),
            ft.dropdown.Option("medium", "Medium"),
            ft.dropdown.Option("low", "Low"),
        ]

    def _tag_options(self) -> list[ft.dropdown.Option]:
        tags: set[str] = set()
        for r in self._all_cards():
            for t in self._tags_list(r):
                tags.add(t)
        opts = [ft.dropdown.Option(_ALL, "All tags")]
        opts.extend(ft.dropdown.Option(t, t) for t in sorted(tags, key=str.lower))
        return opts

    def _due_options(self) -> list[ft.dropdown.Option]:
        return [
            ft.dropdown.Option(_ALL, "Any due"),
            ft.dropdown.Option("overdue", "Overdue"),
            ft.dropdown.Option("week", "This week"),
            ft.dropdown.Option("none", "No date"),
        ]

    def _refresh_filter_options(self) -> None:
        """Rebuild epic/tag option lists so newly created values appear."""
        if self._epic_dd is not None:
            self._epic_dd.options = self._epic_options()
            if self._filter_epic not in {o.key for o in self._epic_dd.options}:
                self._filter_epic = _ALL
            self._epic_dd.value = self._filter_epic
        if self._tag_dd is not None:
            self._tag_dd.options = self._tag_options()
            if self._filter_tag not in {o.key for o in self._tag_dd.options}:
                self._filter_tag = _ALL
            self._tag_dd.value = self._filter_tag

    def _on_search_change(self, e) -> None:
        self._search = (e.control.value or "").strip()
        self._current_page = 0
        self.refresh()

    def _on_epic_change(self, e) -> None:
        self._filter_epic = e.control.value or _ALL
        self._current_page = 0
        self.refresh()

    def _on_status_change(self, e) -> None:
        self._filter_status = e.control.value or _ALL
        self._current_page = 0
        self.refresh()

    def _on_priority_change(self, e) -> None:
        self._filter_priority = e.control.value or _ALL
        self._current_page = 0
        self.refresh()

    def _on_tag_change(self, e) -> None:
        self._filter_tag = e.control.value or _ALL
        self._current_page = 0
        self.refresh()

    def _on_due_change(self, e) -> None:
        self._filter_due = e.control.value or _ALL
        self._current_page = 0
        self.refresh()

    def _on_clear_filters(self, e) -> None:
        self._search = ""
        self._filter_epic = _ALL
        self._filter_status = _ALL
        self._filter_priority = _ALL
        self._filter_tag = _ALL
        self._filter_due = _ALL
        self._current_page = 0
        if self._search_field is not None:
            self._search_field.value = ""
        for dd in (self._epic_dd, self._status_dd, self._priority_dd, self._tag_dd, self._due_dd):
            if dd is not None:
                dd.value = _ALL
        self.refresh()

    # ── Pagination ────────────────────────────────────────────────

    def _on_prev_page(self, e) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self._rerender(refresh_filters=False)

    def _on_next_page(self, e) -> None:
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            self._rerender(refresh_filters=False)

    def _page_label(self) -> str:
        return f"{self._current_page + 1} / {self._total_pages}"

    def _update_pagination_controls(self) -> None:
        if self._prev_btn is not None:
            self._prev_btn.disabled = self._current_page <= 0
        if self._next_btn is not None:
            self._next_btn.disabled = self._current_page >= self._total_pages - 1
        if self._page_info is not None:
            self._page_info.value = self._page_label()

    # ── Data ────────────────────────────────────────────────────────

    def _filtered_rows(self) -> list[dict]:
        # query_cards archived filter is tri-state: False = active only,
        # True = archived only, None = both. "Show archived" means *include*
        # archived alongside active cards, so it maps to None (no filter).
        archived_val: bool | None = None if self._show_archived else False
        rows = operations.list_cards(archived=archived_val)

        if self._filter_status != _ALL:
            rows = [r for r in rows if r.get("lane") == self._filter_status]

        if self._filter_priority != _ALL:
            rows = [r for r in rows if r.get("priority", "medium") == self._filter_priority]

        if self._filter_epic == _UNASSIGNED:
            rows = [r for r in rows if not r.get("epic")]
        elif self._filter_epic != _ALL:
            rows = [r for r in rows if r.get("epic") == self._filter_epic]

        if self._filter_tag != _ALL:
            rows = [r for r in rows if self._filter_tag in self._tags_list(r)]

        if self._filter_due != _ALL:
            rows = [r for r in rows if self._due_matches(r, self._filter_due)]

        if self._search:
            q = self._search.lower()
            rows = [
                r for r in rows
                if q in (r.get("title") or "").lower()
                or q in (r.get("description") or "").lower()
            ]

        return self._sort_rows(rows)

    @staticmethod
    def _tags_list(r: dict) -> list[str]:
        raw = r.get("tags", "")
        items = raw.split(",") if isinstance(raw, str) else (raw or [])
        return [t.strip() for t in items if t.strip()]

    @staticmethod
    def _age_days(r: dict) -> int | None:
        """Whole days since the card was created, or None if unknown."""
        raw = r.get("created_at") or ""
        if not raw:
            return None
        from datetime import datetime, timezone
        try:
            if "T" in raw:
                dt = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            else:
                dt = datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
        return max(0, (datetime.now(timezone.utc) - dt).days)

    @staticmethod
    def _due_date(r: dict):
        """Parsed due date (datetime.date) or None."""
        raw = (r.get("due_date") or "").strip()
        if not raw:
            return None
        from datetime import date
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None

    @classmethod
    def _is_overdue(cls, r: dict) -> bool:
        due = cls._due_date(r)
        if due is None or r.get("lane") == "done":
            return False
        from datetime import date
        return due < date.today()

    @classmethod
    def _days_until_due(cls, r: dict) -> int | None:
        """Signed whole days until the due date (negative = overdue), or None."""
        due = cls._due_date(r)
        if due is None:
            return None
        from datetime import date
        return (due - date.today()).days

    @staticmethod
    def _due_in_label(days: int | None) -> str:
        """Human relative-time label for days-until-due."""
        if days is None:
            return "—"
        if days == 0:
            return "today"
        if days > 0:
            return f"in {days}d"
        return f"{abs(days)}d ago"

    @classmethod
    def _due_matches(cls, r: dict, mode: str) -> bool:
        due = cls._due_date(r)
        if mode == "none":
            return due is None
        if due is None:
            return False
        from datetime import date, timedelta
        today = date.today()
        if mode == "overdue":
            return r.get("lane") != "done" and due < today
        if mode == "week":
            return today <= due <= today + timedelta(days=7)
        return True

    def _count_label(self) -> str:
        n = self._row_count
        return f"{n} card{'s' if n != 1 else ''}"

    # ── Table ─────────────────────────────────────────────────────

    _COLS = ["#", "Title", "Epic", "Status", "Priority", "Tags", "Assignees", "Created", "Updated", "Age", "Due", "Due In"]

    def _build_table(self) -> ft.DataTable:
        all_rows_data = self._filtered_rows()
        self._row_count = len(all_rows_data)
        self._total_pages = max(1, -(-self._row_count // _PAGE_SIZE))  # ceil div
        # Clamp page
        if self._current_page >= self._total_pages:
            self._current_page = max(0, self._total_pages - 1)
        start = self._current_page * _PAGE_SIZE
        rows_data = all_rows_data[start : start + _PAGE_SIZE]

        def make_sort(idx: int):
            def handler(e):
                if self._sort_col == idx:
                    self._sort_asc = not self._sort_asc
                else:
                    self._sort_col = idx
                    self._sort_asc = True
                self._current_page = 0
                self._rerender(refresh_filters=False)
            return handler

        columns = [
            ft.DataColumn(
                ft.Text(label, size=12, color="#8B949E", weight=ft.FontWeight.BOLD),
                on_sort=make_sort(i),
            )
            for i, label in enumerate(self._COLS)
        ]

        rows = []
        for r in rows_data:
            num = r.get("number")
            tags = self._tags_list(r)
            epic = r.get("epic") or "Unassigned"
            status = r.get("lane", "")
            status_label = LANE_LABELS.get(status, status)
            status_color = _LANE_COLORS.get(status, "#8B949E")
            priority = r.get("priority", "medium")
            priority_color = _PRIORITY_COLORS.get(priority, "#8B949E")
            raw_assignees = r.get("assigned_to", "")
            assignees = raw_assignees if isinstance(raw_assignees, str) else ", ".join(raw_assignees or [])
            assignees = assignees or "—"
            created = (r.get("created_at") or "")[:10]
            updated = (r.get("updated_at") or "")[:10]
            age = self._age_days(r)
            age_str = f"{age}d" if age is not None else "—"
            due = self._due_date(r)
            due_str = due.strftime("%Y-%m-%d") if due is not None else "—"
            overdue = self._is_overdue(r)
            due_color = "#F07878" if overdue else "#8B949E"
            days_until = self._days_until_due(r)
            due_in_str = self._due_in_label(days_until)

            def open_card(e, card_id=r["id"]):
                self._on_open_card(card_id)

            cells = [
                ft.DataCell(ft.Text(f"#{num}" if num else "-", size=12, color="#8B949E")),
                ft.DataCell(ft.Container(
                    content=ft.Text(r["title"], size=12, color="#C9D1D9"),
                    width=260,
                )),
                ft.DataCell(ft.Container(
                    content=ft.Text(epic, size=12, color="#8B949E"),
                    width=120,
                )),
                ft.DataCell(
                    ft.Container(
                        content=ft.Text(status_label, size=11, color=status_color, weight=ft.FontWeight.W_500),
                        padding=ft.Padding(8, 3, 8, 3),
                        bgcolor=ft.Colors.with_opacity(0.15, status_color),
                        border_radius=ft.BorderRadius(10, 10, 10, 10),
                    )
                ),
                ft.DataCell(
                    ft.Text(priority.capitalize(), size=12, color=priority_color, weight=ft.FontWeight.W_500)
                ),
                ft.DataCell(ft.Container(
                    content=ft.Row(
                        [
                            ft.Container(
                                content=ft.Text(t, size=10, color="#58A6FF"),
                                bgcolor="#1C2A4A",
                                border_radius=4,
                                padding=ft.Padding(6, 2, 6, 2),
                            )
                            for t in tags
                        ],
                        wrap=True,
                        spacing=4,
                        run_spacing=4,
                    ) if tags else ft.Text("—", size=12, color="#8B949E"),
                    width=140,
                )),
                ft.DataCell(ft.Text(assignees, size=12, color="#8B949E", max_lines=1)),
                ft.DataCell(ft.Text(created, size=12, color="#8B949E")),
                ft.DataCell(ft.Text(updated, size=12, color="#8B949E")),
                ft.DataCell(ft.Text(age_str, size=12, color="#8B949E")),
                ft.DataCell(ft.Text(due_str, size=12, color=due_color, weight=ft.FontWeight.W_500 if overdue else None)),
                ft.DataCell(ft.Text(due_in_str, size=12, color=due_color, weight=ft.FontWeight.W_500 if overdue else None)),
            ]

            # Add archive/delete actions for archived rows
            if self._show_archived:
                def unarchive(e, cid=r["id"]):
                    operations.unarchive_card(cid)
                    self.refresh()

                def delete(e, cid=r["id"]):
                    def confirm(_):
                        dlg.open = False
                        operations.delete_card(cid)
                        self.refresh()
                        self._page.update()

                    dlg = ft.AlertDialog(
                        title=ft.Text("Delete Card"),
                        content=ft.Text("Permanently delete this card? This cannot be undone."),
                        actions=[
                            ft.TextButton("Cancel", on_click=lambda _: setattr(dlg, "open", False) or self._page.update()),
                            ft.TextButton("Delete", on_click=confirm, style=ft.ButtonStyle(color="#F07878")),
                        ],
                    )
                    self._page.overlay.append(dlg)
                    dlg.open = True
                    self._page.update()

                cells.append(
                    ft.DataCell(
                        ft.Row([
                            ft.TextButton("Restore", on_click=unarchive, style=ft.ButtonStyle(color="#58A6FF")),
                            ft.TextButton("Delete", on_click=delete, style=ft.ButtonStyle(color="#F07878")),
                        ], spacing=4)
                    )
                )

            rows.append(
                ft.DataRow(
                    cells=cells,
                    on_select_change=open_card,
                    color={ft.ControlState.HOVERED: "#2D2D2D"},
                )
            )

        table_columns = columns.copy()
        if self._show_archived:
            table_columns.append(
                ft.DataColumn(ft.Text("Actions", size=12, color="#8B949E", weight=ft.FontWeight.BOLD))
            )

        return ft.DataTable(
            columns=table_columns,
            rows=rows,
            sort_column_index=self._sort_col,
            sort_ascending=self._sort_asc,
            border=ft.Border(
                ft.BorderSide(0, "transparent"),
                ft.BorderSide(0, "transparent"),
                ft.BorderSide(0, "transparent"),
                ft.BorderSide(0, "transparent"),
            ),
            horizontal_lines=ft.BorderSide(1, "#2D2D2D"),
            column_spacing=20,
            data_row_min_height=42,
        )

    def _sort_rows(self, rows: list[dict]) -> list[dict]:
        col = self._sort_col
        asc = self._sort_asc

        def key_fn(r):
            if col == 0:
                return r.get("number") or 0
            if col == 1:
                return (r.get("title") or "").lower()
            if col == 2:
                return (r.get("epic") or "").lower()
            if col == 3:
                return r.get("lane", "")
            if col == 4:
                return _PRIORITY_RANK.get(r.get("priority", "medium"), 1)
            if col == 5:
                return ",".join(self._tags_list(r)).lower()
            if col == 6:
                raw = r.get("assigned_to", "")
                val = raw if isinstance(raw, str) else ",".join(raw or [])
                return val.lower()
            if col == 7:
                return r.get("created_at") or ""
            if col == 8:
                return r.get("updated_at") or ""
            if col == 9:
                age = self._age_days(r)
                return age if age is not None else -1
            if col == 10:
                due = self._due_date(r)
                return due.isoformat() if due is not None else ""
            if col == 11:
                days = self._days_until_due(r)
                return days if days is not None else 10**9
            return ""

        return sorted(rows, key=key_fn, reverse=not asc)

    def _on_toggle_archived(self, value: bool) -> None:
        self._show_archived = bool(value)
        self._current_page = 0
        self.refresh()
