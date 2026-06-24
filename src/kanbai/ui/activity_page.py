from __future__ import annotations

from datetime import date, datetime, timezone
from math import ceil
from typing import Any

import flet as ft

from kanbai.board import epics as _epics
from kanbai.board import operations
from kanbai.board.storage import activity_log
from kanbai.ui import theme

_ALL = "__all__"
_UNASSIGNED = "__none__"
_PAGE_SIZE = 50

_BG_SECTION = "#252526"
_BORDER = "#3E3E42"
_BG_CARD = "#161B22"

_EVENT_COLORS: dict[str, str] = {
    "card_created": "#56D364",
    "card_deleted": "#F85149",
    "system_comment": "#8B949E",
    "comment_deleted": "#F0A742",
    "attachment_added": "#56D364",
    "attachment_removed": "#F0A742",
    "checklist_item_deleted": "#F0A742",
    "moved": "#58A6FF",
    "archived": "#F0883E",
    "restored": "#58A6FF",
}


class ActivityPage:
    def __init__(self, page: ft.Page, on_open_card=None):
        self._page = page
        self._on_open_card = on_open_card
        self._view: ft.Control | None = None
        self._count_text: ft.Text | None = None
        self._search_field: ft.TextField | None = None
        self._type_dd: ft.Dropdown | None = None
        self._epic_dd: ft.Dropdown | None = None
        self._from_field: ft.TextField | None = None
        self._to_field: ft.TextField | None = None
        self._page_info: ft.Text | None = None
        self._prev_btn: ft.IconButton | None = None
        self._next_btn: ft.IconButton | None = None
        self._list: ft.ListView | None = None
        self._events: list[dict[str, Any]] = []
        self._search: str = ""
        self._event_type: str = _ALL
        self._epic: str = _ALL
        self._date_from: str = ""
        self._date_to: str = ""
        self._current_page: int = 0
        self._total_pages: int = 1

    def build(self) -> ft.Control:
        self._count_text = ft.Text("", size=11, color="#6E7681")
        filter_width = 400
        self._search_field = ft.TextField(
            hint_text="Search by card #, title, id, or text…",
            width=filter_width,
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
        self._type_dd = ft.Dropdown(
            width=filter_width,
            value=_ALL,
            options=[
                ft.dropdown.Option(_ALL, "All events"),
                ft.dropdown.Option("system_comment", "System activity"),
                ft.dropdown.Option("card_deleted", "Card deletions"),
                ft.dropdown.Option("attachment_added", "Attachment adds"),
                ft.dropdown.Option("attachment_removed", "Attachment removals"),
                ft.dropdown.Option("checklist_item_deleted", "Checklist removals"),
                ft.dropdown.Option("comment_deleted", "Comment removals"),
            ],
            bgcolor="#21262D",
            border_color="#30363D",
            color="#C9D1D9",
            text_size=12,
            content_padding=ft.Padding(10, 4, 10, 4),
            on_select=self._on_type_change,
        )
        self._epic_dd = theme.epic_dropdown(
            options=self._epic_options(),
            value=_ALL,
            on_select=self._on_epic_change,
        )
        self._epic_dd.width = filter_width
        self._from_field = ft.TextField(
            label="From",
            hint_text="YYYY-MM-DD",
            width=140,
            height=36,
            text_size=12,
            bgcolor="#1E1E1E",
            border_color="#3E3E42",
            color="#C9D1D9",
            cursor_color="#58A6FF",
            content_padding=ft.Padding(10, 6, 10, 6),
            on_change=self._on_date_change,
        )
        self._to_field = ft.TextField(
            label="To",
            hint_text="YYYY-MM-DD",
            width=140,
            height=36,
            text_size=12,
            bgcolor="#1E1E1E",
            border_color="#3E3E42",
            color="#C9D1D9",
            cursor_color="#58A6FF",
            content_padding=ft.Padding(10, 6, 10, 6),
            on_change=self._on_date_change,
        )
        from_field = self._from_field
        to_field = self._to_field
        assert from_field is not None and to_field is not None
        from_picker = self._with_date_picker(from_field, "Pick from date")
        to_picker = self._with_date_picker(to_field, "Pick to date")
        refresh_btn = ft.TextButton(
            "Refresh",
            icon=ft.Icons.REFRESH,
            style=ft.ButtonStyle(color="#C9D1D9"),
            on_click=lambda _e: self.refresh(),
        )
        refresh_chip = ft.Container(
            content=refresh_btn,
            bgcolor="#21262D",
            border=ft.Border(
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER),
            ),
            border_radius=6,
            padding=ft.Padding(0, 0, 0, 0),
        )

        top_bar = ft.Container(
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text("Activity log", size=20, weight=ft.FontWeight.BOLD, color="#E6EDF3"),
                            ft.Text(
                                "Board-wide audit trail from activity.jsonl",
                                size=11,
                                color="#8B949E",
                            ),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    refresh_chip,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(14, 12, 14, 12),
            bgcolor=_BG_SECTION,
            border=ft.Border(
                ft.BorderSide(0, "transparent"),
                ft.BorderSide(0, "transparent"),
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(0, "transparent"),
            ),
        )

        search_field = self._search_field
        type_dd = self._type_dd
        epic_dd = self._epic_dd
        from_field = self._from_field
        to_field = self._to_field
        assert search_field is not None and type_dd is not None and epic_dd is not None
        assert from_field is not None and to_field is not None
        row1 = ft.Row(
            [
                type_dd,
                epic_dd,
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        row2 = ft.Row(
            [
                from_picker,
                to_picker,
                search_field,
            ],
            spacing=22,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        filters = ft.Column(
            [
                row1,
                row2,
                ft.Text(
                    "System activity covers edits/removals like tags, file refs, attachments, due dates, assignees, priorities, archive, and restore.",
                    size=10,
                    color="#6E7681",
                ),
            ],
            spacing=8,
        )

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

        count_text = self._count_text
        prev_btn = self._prev_btn
        page_info = self._page_info
        next_btn = self._next_btn
        pager = ft.Row(
            [
                prev_btn,
                page_info,
                next_btn,
                ft.Container(expand=True),
                count_text,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self._list = ft.ListView(spacing=8, expand=True, auto_scroll=False, padding=0)
        filters_card = ft.Container(
            content=filters,
            padding=ft.Padding(12, 12, 12, 12),
            bgcolor=_BG_SECTION,
            border=ft.Border(
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER),
            ),
            border_radius=8,
        )
        list_card = ft.Container(
            content=self._list,
            border=ft.Border(
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER),
            ),
            border_radius=8,
            padding=ft.Padding(12, 10, 12, 10),
            bgcolor=_BG_CARD,
            expand=True,
        )
        pager_row = ft.Container(
            content=pager,
            padding=ft.Padding(4, 0, 4, 0),
        )

        view = ft.Container(
            content=ft.Column(
                [
                    top_bar,
                    ft.Container(height=12),
                    filters_card,
                    ft.Container(height=12),
                    list_card,
                    ft.Container(height=10),
                    pager_row,
                ],
                spacing=0,
                expand=True,
            ),
            padding=ft.Padding(16, 12, 16, 12),
            expand=True,
        )
        self._view = view
        self.refresh()
        return view

    def refresh(self) -> None:
        self._events = [
            event for event in activity_log.read_all()
            if event.get("type") != "card_reordered"
        ]
        if self._list is None:
            return
        filtered = self._filtered_events()
        self._total_pages = max(1, ceil(len(filtered) / _PAGE_SIZE))
        self._current_page = min(self._current_page, self._total_pages - 1)
        start = self._current_page * _PAGE_SIZE
        page_events = filtered[start : start + _PAGE_SIZE]
        if self._count_text is not None:
            if filtered:
                lo = start + 1
                hi = start + len(page_events)
                self._count_text.value = f"Showing {lo}-{hi} of {len(filtered)} events"
            else:
                self._count_text.value = "No events"
        self._list.controls.clear()
        if not page_events:
            self._list.controls.append(
                ft.Text("No activity yet.", size=11, color="#6E7681", italic=True)
            )
        else:
            for event in page_events:
                self._list.controls.append(self._render_event(event))
        self._update_pagination_controls()
        try:
            self._page.update()
        except Exception:
            pass

    def _with_date_picker(
        self,
        field: ft.TextField,
        tooltip: str,
    ) -> ft.Row:
        picker = ft.DatePicker(
            first_date=datetime(2000, 1, 1),
            last_date=datetime(2100, 12, 31),
            help_text=tooltip,
        )

        def _on_pick(e) -> None:
            sel = e.control.value
            if sel is None:
                return
            if sel.tzinfo is None:
                sel = sel.replace(tzinfo=timezone.utc)
            field.value = sel.astimezone().strftime("%Y-%m-%d")
            self._date_from = self._from_field.value if self._from_field else ""
            self._date_to = self._to_field.value if self._to_field else ""
            self._current_page = 0
            self.refresh()
            try:
                self._page.pop_dialog()
            except Exception:
                pass

        picker.on_change = _on_pick
        picker.on_dismiss = lambda _e: None

        def _open(_e) -> None:
            raw = (field.value or "").strip()
            if raw:
                try:
                    picker.value = datetime.fromisoformat(raw[:10])
                except ValueError:
                    pass
            self._page.show_dialog(picker)

        return ft.Row(
            [
                field,
                ft.IconButton(
                    icon=ft.Icons.CALENDAR_MONTH,
                    tooltip=tooltip,
                    icon_size=18,
                    on_click=_open,
                ),
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _filtered_events(self) -> list[dict[str, Any]]:
        query = self._search.strip().lower()
        from_day = self._parse_date(self._date_from)
        to_day = self._parse_date(self._date_to)
        out: list[dict[str, Any]] = []
        for event in self._events:
            if self._event_type != _ALL and event.get("type") != self._event_type:
                continue
            if self._epic == _UNASSIGNED:
                if event.get("card_epic"):
                    continue
            elif self._epic != _ALL and event.get("card_epic") != self._epic:
                continue
            event_day = self._event_day(event)
            if from_day and (event_day is None or event_day < from_day):
                continue
            if to_day and (event_day is None or event_day > to_day):
                continue
            if query:
                haystack = " ".join(
                    str(event.get(k, ""))
                    for k in (
                        "type",
                        "text",
                        "card_title",
                        "card_id",
                        "card_number",
                        "item_text",
                        "comment_text",
                        "card_epic",
                    )
                ).lower()
                if query not in haystack:
                    continue
            out.append(event)
        return sorted(out, key=self._event_sort_key, reverse=True)

    def _on_search_change(self, e) -> None:
        self._search = e.control.value or ""
        self._current_page = 0
        self.refresh()

    def _on_type_change(self, e) -> None:
        self._event_type = e.control.value or _ALL
        self._current_page = 0
        self.refresh()

    def _on_epic_change(self, e) -> None:
        self._epic = e.control.value or _ALL
        self._current_page = 0
        self.refresh()

    def _on_date_change(self, e) -> None:
        self._date_from = self._from_field.value if self._from_field else ""
        self._date_to = self._to_field.value if self._to_field else ""
        self._current_page = 0
        self.refresh()

    def _on_prev_page(self, e) -> None:
        if self._current_page > 0:
            self._current_page -= 1
            self.refresh()

    def _on_next_page(self, e) -> None:
        if self._current_page < self._total_pages - 1:
            self._current_page += 1
            self.refresh()

    def _page_label(self) -> str:
        return f"{self._current_page + 1} / {self._total_pages}"

    def _update_pagination_controls(self) -> None:
        if self._prev_btn is not None:
            self._prev_btn.disabled = self._current_page <= 0
        if self._next_btn is not None:
            self._next_btn.disabled = self._current_page >= self._total_pages - 1
        if self._page_info is not None:
            self._page_info.value = self._page_label()

    def _render_event(self, event: dict[str, Any]) -> ft.Control:
        event_type = str(event.get("type") or "event")
        color = _EVENT_COLORS.get(event_type, "#58A6FF")
        at = self._fmt_ts(str(event.get("at") or ""))
        card_num = event.get("card_number")
        card_title = event.get("card_title") or ""
        card_id = event.get("card_id")

        title_bits = []
        if card_num is not None:
            title_bits.append(f"#{card_num}")
        if card_title:
            title_bits.append(card_title)
        title = " — ".join(title_bits) if title_bits else card_id or event_type

        summary = self._summarize(event)
        card_exists = bool(card_id and operations.get_card(str(card_id)))
        open_btn = ft.IconButton(
            ft.Icons.OPEN_IN_NEW,
            icon_size=16,
            icon_color="#8B949E",
            tooltip="Open card",
            disabled=not (card_id and self._on_open_card and card_exists),
            on_click=lambda _e, cid=str(card_id): self._on_open_card(cid) if self._on_open_card and cid else None,
        )

        return ft.Container(
            content=ft.Row(
                [
                    ft.Container(width=4, height=36, bgcolor=color, border_radius=2),
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(at, size=10, color="#8B949E", width=110),
                                    ft.Text(title, size=12, weight=ft.FontWeight.W_600, color="#E6EDF3", expand=True),
                                    ft.Text(event_type.replace("_", " "), size=10, color=color),
                                    open_btn,
                                ],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                            ft.Text(summary, size=11, color="#C9D1D9", selectable=True),
                        ],
                        spacing=4,
                        expand=True,
                    ),
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor="#1C2128",
            border_radius=8,
            padding=ft.Padding(10, 8, 10, 8),
            border=ft.Border(
                ft.BorderSide(1, "#30363D"),
                ft.BorderSide(1, "#30363D"),
                ft.BorderSide(1, "#30363D"),
                ft.BorderSide(1, "#30363D"),
            ),
        )

    def _summarize(self, event: dict[str, Any]) -> str:
        et = event.get("type")
        if et == "card_deleted":
            return "Card deleted"
        if et == "attachment_added":
            return f"Added attachment: {event.get('filename', '')}"
        if et == "attachment_removed":
            return f"Removed attachment: {event.get('filename', '')}"
        if et == "checklist_item_deleted":
            return f"Removed checklist item: {event.get('item_text', '')}"
        if et == "comment_deleted":
            return f"Removed comment: {event.get('comment_text', '')}"
        if et == "system_comment":
            return str(event.get("text") or "")
        return " ".join(
            part for part in [
                str(event.get("text") or "").strip(),
                str(event.get("comment_text") or "").strip(),
                str(event.get("item_text") or "").strip(),
            ]
            if part
        ) or str(et or "event").replace("_", " ")

    def _epic_options(self) -> list[ft.dropdown.Option]:
        names = sorted(_epics.list_epics(include_archived=True), key=str.lower)
        opts = [ft.dropdown.Option(_ALL, "All epics")]
        opts.append(ft.dropdown.Option(_UNASSIGNED, "Unassigned"))
        opts.extend(ft.dropdown.Option(name, name) for name in names)
        return opts

    @staticmethod
    def _parse_date(raw: str | None) -> date | None:
        value = (raw or "").strip()
        if not value:
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    @staticmethod
    def _event_day(event: dict[str, Any]) -> date | None:
        raw = str(event.get("at") or "")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone().date()
        except Exception:
            return None

    @staticmethod
    def _event_sort_key(event: dict[str, Any]) -> str:
        return str(event.get("at") or "")

    @staticmethod
    def _fmt_ts(iso: str) -> str:
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.astimezone().strftime("%m/%d %H:%M")
        except Exception:
            return iso or ""
