"""Global card search overlay (Ctrl+K).

Reuses the same query_cards() path as the relationship search in card_dialog.
Opens as a centered modal with real-time filtering, keyboard navigation, and
click-to-open.
"""

from __future__ import annotations

from typing import Callable

import flet as ft

from board.storage import sqlite_cache as _sc


def open_search_overlay(page: ft.Page, open_card_fn: Callable[[str], None]) -> None:
    """Show the global search overlay. Escape or clicking outside dismisses it."""

    selected_index: dict[str, int] = {"value": -1}
    result_cards: list[dict] = []
    prev_kb_handler = page.on_keyboard_event

    results_col = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, height=0)

    def _dismiss(_e=None) -> None:
        page.on_keyboard_event = prev_kb_handler
        page.pop_dialog()

    def _matching_cards(raw: str) -> list[dict]:
        q = raw.strip().lstrip("#").lower()
        if not q:
            return []
        exact_number = raw.strip().startswith("#") and raw != raw.rstrip()
        out: list[dict] = []
        for r in _sc.query_cards(archived=None):
            num = r.get("number")
            if num is None:
                continue
            if exact_number:
                if str(num) != q:
                    continue
            elif q not in str(num) and q not in (r.get("title") or "").lower():
                continue
            out.append(r)
        out.sort(key=lambda r: 0 if str(r.get("number")) == q else 1)
        return out

    def _build_result_row(r: dict, idx: int) -> ft.Container:
        num = r["number"]
        title = r.get("title") or ""
        lane = r.get("lane") or ""
        epic = r.get("epic") or ""
        archived = r.get("archived", False)

        lane_colors = {
            "backlog": "#8B949E",
            "in_progress": "#58A6FF",
            "review": "#D29922",
            "done": "#56D364",
        }
        lane_label = lane.replace("_", " ").title() if lane else ""
        lane_color = lane_colors.get(lane, "#8B949E")

        is_selected = idx == selected_index["value"]

        badges = []
        badges.append(
            ft.Container(
                content=ft.Text(lane_label, size=10, color=lane_color),
                border=ft.Border(
                    left=ft.BorderSide(1, lane_color),
                    right=ft.BorderSide(1, lane_color),
                    top=ft.BorderSide(1, lane_color),
                    bottom=ft.BorderSide(1, lane_color),
                ),
                border_radius=3,
                padding=ft.Padding(4, 2, 4, 2),
            )
        )
        if epic:
            badges.append(ft.Text(epic, size=10, color="#8B949E", italic=True))
        if archived:
            badges.append(ft.Text("archived", size=10, color="#F85149", italic=True))

        def _pick(_e, card_id=r["id"]):
            _dismiss()
            open_card_fn(card_id)

        return ft.Container(
            content=ft.Row(
                [
                    ft.Text(f"#{num}", size=12, color="#8B949E", width=50),
                    ft.Text(
                        title, size=13, color="#C9D1D9", expand=True,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                    *badges,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_click=_pick,
            ink=True,
            border_radius=4,
            bgcolor="#1F2937" if is_selected else "#161B22",
            border=ft.Border(
                left=ft.BorderSide(2, "#58A6FF") if is_selected else ft.BorderSide(0, "transparent"),
                right=ft.BorderSide(0, "transparent"),
                top=ft.BorderSide(0, "transparent"),
                bottom=ft.BorderSide(0, "transparent"),
            ),
            padding=ft.Padding(10, 8, 10, 8),
        )

    def _refresh_results(_e=None) -> None:
        nonlocal result_cards
        rawval = search_field.value or ""
        result_cards = _matching_cards(rawval)
        selected_index["value"] = 0 if result_cards else -1
        _rebuild_list()

    def _rebuild_list() -> None:
        results_col.controls.clear()
        if not result_cards:
            if (search_field.value or "").strip():
                results_col.height = 40
                results_col.controls.append(
                    ft.Container(
                        content=ft.Text("No cards found.", size=12, color="#6E7681", italic=True),
                        padding=ft.Padding(10, 8, 10, 8),
                    )
                )
            else:
                results_col.height = 0
        else:
            visible = result_cards[:12]
            extra = len(result_cards) - 12
            results_col.height = min(len(visible) * 44 + (24 if extra > 0 else 0), 500)
            for i, r in enumerate(visible):
                results_col.controls.append(_build_result_row(r, i))
            if extra > 0:
                results_col.controls.append(
                    ft.Text(
                        f"…and {extra} more — refine your search",
                        size=11, color="#6E7681", italic=True,
                    )
                )
        page.update()

    def _on_key(e: ft.KeyboardEvent) -> None:
        if e.key == "Escape":
            _dismiss()
            return
        if not result_cards:
            return
        max_idx = min(len(result_cards), 12) - 1
        if e.key == "Arrow Down":
            selected_index["value"] = min(selected_index["value"] + 1, max_idx)
            _rebuild_list()
        elif e.key == "Arrow Up":
            selected_index["value"] = max(selected_index["value"] - 1, 0)
            _rebuild_list()
        elif e.key == "Enter":
            idx = selected_index["value"]
            if 0 <= idx < len(result_cards):
                card_id = result_cards[idx]["id"]
                _dismiss()
                open_card_fn(card_id)

    search_field = ft.TextField(
        hint_text="Search cards by # or title…",
        autofocus=True,
        width=528,
        on_change=_refresh_results,
        border_radius=8,
        content_padding=ft.Padding(12, 14, 12, 14),
        prefix_icon=ft.Icons.SEARCH,
        text_size=14,
    )

    hint_text = ft.Text(
        "Type to search • ↑↓ to navigate • Enter to open • Esc to close",
        size=11,
        color="#6E7681",
        text_align=ft.TextAlign.CENTER,
    )

    content = ft.Container(
        content=ft.Column(
            [search_field, results_col, hint_text],
            spacing=8,
            tight=True,
        ),
        width=560,
        padding=ft.Padding(16, 16, 16, 12),
    )

    dlg = ft.AlertDialog(
        modal=False,
        bgcolor="#0D1117",
        content=content,
        content_padding=0,
        shape=ft.RoundedRectangleBorder(radius=12),
        shadow_color="#000000",
        elevation=24,
    )

    page.on_keyboard_event = _on_key
    page.show_dialog(dlg)
    page.update()
