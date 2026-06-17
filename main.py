from __future__ import annotations

import argparse

from dotenv import load_dotenv

from paths import ENV_FILE

load_dotenv(ENV_FILE)


def run_app() -> None:
    import json
    import flet as ft
    import traceback as _tb

    from paths import ASSETS_DIR
    import paths
    from board.storage.sync import full_sync
    from vault.vault import init_vault
    from ui.card_dialog import open_create_dialog, open_edit_dialog
    from ui.chat_panel import ChatPanel
    from ui.vault_panel import VaultPanel
    from ui.kanban_page import KanbanPage
    from ui.table_page import TablePage
    from ui.analytics_page import AnalyticsPage
    from ui.health_page import HealthPage
    from ui.graph_page import GraphPage
    from ui.settings_dialog import open_settings_dialog

    async def app(page: ft.Page) -> None:
        try:
            page.title = "Kanbai"
            page.theme_mode = ft.ThemeMode.DARK
            page.bgcolor = "#1E1E1E"
            page.padding = 0
            page.window.width = 1500
            page.window.height = 1200
            page.update()          # commit dimensions before centering
            try:
                await page.window.center()
            except Exception:
                pass
            page.window.min_width = 900
            page.window.min_height = 600

            synced = full_sync()
            vault_ok = init_vault()

            # Navigation stack so that opening a linked card from within a card
            # dialog returns to the previous card when the new one is closed
            # (instead of dismissing all the way back to the board).
            card_nav_stack: list[str] = []

            def on_open_card(card_id: str) -> None:
                if not (card_nav_stack and card_nav_stack[-1] == card_id):
                    card_nav_stack.append(card_id)

                def _go_back():
                    if card_nav_stack and card_nav_stack[-1] == card_id:
                        card_nav_stack.pop()
                    if card_nav_stack:
                        on_open_card(card_nav_stack[-1])

                def on_updated(cid, col):
                    import threading
                    threading.Thread(target=lambda: (kanban.refresh_board(), table_pg.refresh()), daemon=True).start()
                    analytics_pg.refresh()
                def on_moved(cid, old_col, new_col):
                    import threading
                    threading.Thread(target=lambda: (kanban.refresh_board(), table_pg.refresh()), daemon=True).start()
                    analytics_pg.refresh()
                def on_archived(cid, col):
                    import threading
                    threading.Thread(target=lambda: (kanban.refresh_board(), table_pg.refresh()), daemon=True).start()
                    analytics_pg.refresh()
                    _go_back()

                open_edit_dialog(
                    page,
                    card_id,
                    on_updated,
                    on_moved,
                    on_archived,
                    open_card_fn=on_open_card,
                    on_close=_go_back,
                )

            # Shared epic selection across Board / Graph / Analytics.
            # None = no specific epic chosen → renders as "Unassigned" on the
            # Board/Graph and "All" on Analytics; a concrete name syncs verbatim.
            _shared_epic: dict[str, str | None] = {"value": None}

            def _load_ui_state() -> dict:
                try:
                    return json.loads(paths.UI_STATE_PATH.read_text())
                except Exception:
                    return {}

            def _save_ui_state(state: dict) -> None:
                try:
                    paths.UI_STATE_PATH.write_text(json.dumps(state))
                except Exception:
                    pass

            def _on_epic_changed(epic) -> None:
                _shared_epic["value"] = epic
                state = _load_ui_state()
                state["last_epic"] = epic
                _save_ui_state(state)

            # Single source of truth for the sidebar navigation order. Add,
            # remove, or reorder entries here and every index-dependent piece
            # (destinations, view switching, programmatic navigation) follows.
            NAV_ITEMS = [
                ("board", ft.Icons.VIEW_KANBAN_OUTLINED, ft.Icons.VIEW_KANBAN, "Board"),
                ("analytics", ft.Icons.BAR_CHART_OUTLINED, ft.Icons.BAR_CHART, "Analytics"),
                ("health", ft.Icons.MONITOR_HEART_OUTLINED, ft.Icons.MONITOR_HEART, "Health"),
                ("graph", ft.Icons.ACCOUNT_TREE_OUTLINED, ft.Icons.ACCOUNT_TREE, "Graph"),
                ("table", ft.Icons.TABLE_ROWS_OUTLINED, ft.Icons.TABLE_ROWS, "Table"),
            ]
            NAV_INDEX = {key: i for i, (key, *_rest) in enumerate(NAV_ITEMS)}

            # Health indicator dots embedded in the Analytics nav icon.
            # Two instances — one for unselected state, one for selected state.
            _health_dot_unsel = ft.Container(width=9, height=9, border_radius=5, visible=False, right=0, top=2)
            _health_dot_sel   = ft.Container(width=9, height=9, border_radius=5, visible=False, right=0, top=2)

            # Same dots for the Health nav icon.
            _health_dot_health_unsel = ft.Container(width=9, height=9, border_radius=5, visible=False, right=0, top=2)
            _health_dot_health_sel   = ft.Container(width=9, height=9, border_radius=5, visible=False, right=0, top=2)

            _all_nav_dots = (
                _health_dot_unsel, _health_dot_sel,
                _health_dot_health_unsel, _health_dot_health_sel,
            )

            def _health_icon_stack(base_icon: str, dot: ft.Container) -> ft.Stack:
                return ft.Stack(
                    [ft.Icon(base_icon, size=24), dot],
                    width=28,
                    height=28,
                )

            # Health badge shown in the header, left of Vault Ready.
            _health_header_badge = ft.Container(visible=False)

            def _refresh_health_badge() -> None:
                from board.health import project_health
                h = project_health()
                _BAND_COLORS = {"watch": "#F0A742", "bad": "#F85149", "good": "#56D364"}
                _BAND_BG = {"watch": "#2A2200", "bad": "#2A0A08", "good": "#0D1F12"}
                _BAND_ICONS = {"watch": ft.Icons.WARNING_AMBER_OUTLINED, "bad": ft.Icons.ERROR_OUTLINE, "good": ft.Icons.CHECK_CIRCLE_OUTLINE}
                status = h["status"]
                color = _BAND_COLORS.get(status, "#56D364")
                visible = status in ("watch", "bad")
                tip = h["label"]
                if visible:
                    non_good = h["warnings"]
                    tip_lines = [
                        f"{'⚠' if s['band'] == 'watch' else '✖'} {s['label']}: {s['message']}"
                        for s in non_good
                    ]
                    tip = "\n".join(tip_lines)
                for dot in _all_nav_dots:
                    dot.bgcolor = color
                    dot.visible = visible
                    dot.tooltip = ft.Tooltip(message=tip, prefer_below=True, wait_duration=300) if visible else None
                # Header badge — always visible
                _health_header_badge.content = ft.Row(
                    [
                        ft.Icon(_BAND_ICONS.get(status, ft.Icons.CHECK_CIRCLE_OUTLINE), size=13, color=color),
                        ft.Text(h["label"], size=11, color=color),
                    ],
                    spacing=4,
                    tight=True,
                )
                _health_header_badge.bgcolor = _BAND_BG.get(status)
                _health_header_badge.border_radius = 4
                _health_header_badge.padding = ft.Padding(6, 4, 6, 4)
                _health_header_badge.tooltip = ft.Tooltip(message=tip, prefer_below=True, wait_duration=300) if visible else None
                _health_header_badge.visible = True
                try:
                    page.update()
                except Exception:
                    pass

            def show_graph_for_epic(epic) -> None:
                _shared_epic["value"] = epic
                _show_view("graph")
                sidebar.selected_index = NAV_INDEX["graph"]
                graph_pg.refresh_epics()
                graph_pg.select_epic(epic)
                page.update()

            def show_board_for_epic(epic) -> None:
                _shared_epic["value"] = epic
                _show_view("board")
                sidebar.selected_index = NAV_INDEX["board"]
                kanban.refresh_epics()
                kanban.select_epic(epic)
                page.update()

            kanban = KanbanPage(
                page,
                on_open_card,
                open_create_dialog,
                on_show_graph=show_graph_for_epic,
                on_epic_change=_on_epic_changed,
            )
            table_pg = TablePage(page, on_open_card)
            analytics_pg = AnalyticsPage(page, on_open_card, on_epic_change=_on_epic_changed)
            health_pg = HealthPage(
                page,
                on_open_card=on_open_card,
                on_epic_change=_on_epic_changed,
            )
            graph_pg = GraphPage(
                page,
                on_open_card,
                on_show_board=show_board_for_epic,
                on_epic_change=_on_epic_changed,
            )

            kanban_view = kanban.build()
            table_view = table_pg.build()
            analytics_view = analytics_pg.build()
            health_view = health_pg.build()
            graph_view = graph_pg.build()

            # Map nav keys to their view + epic-selection handler so navigation
            # stays keyed by name rather than fragile positional indices.
            view_by_key = {
                "board": kanban_view,
                "analytics": analytics_view,
                "health": health_view,
                "graph": graph_view,
                "table": table_view,
            }
            select_by_key = {
                "board": kanban.select_epic,
                "analytics": analytics_pg.select_epic,
                "health": health_pg.select_epic,
                "graph": graph_pg.select_epic,
                "table": table_pg.select_epic,
            }

            # Restore the last-used epic (persisted across restarts).
            _saved_epic = _load_ui_state().get("last_epic")
            if _saved_epic:
                _shared_epic["value"] = _saved_epic
                for _sel_fn in select_by_key.values():
                    _sel_fn(_saved_epic)

            # Keep ALL views mounted and just toggle visibility on navigation.
            # Swapping a single container's .content forces Flet to tear down
            # and re-transmit the entire (heavy) view subtree to the browser on
            # every switch — the analytics page alone is ~370 heatmap cells plus
            # chart images, which is what made repeat switches take seconds.
            # Visibility toggling leaves the tree mounted, so switching back to
            # an already-rendered view is instant.
            for _key, _view in view_by_key.items():
                _view.visible = (_key == "board")

            def _show_view(key: str) -> None:
                for k, v in view_by_key.items():
                    v.visible = (k == key)

            def on_board_change() -> None:
                kanban.refresh_board()
                kanban.refresh_epics()
                table_pg.refresh()
                analytics_pg.refresh()
                graph_pg.refresh()
                graph_pg.refresh_epics()
                _refresh_health_badge()

            chat = ChatPanel(page, on_board_change=on_board_change)
            vault = VaultPanel(page)

            vault_badge = ft.Container(
                content=ft.Row(
                    [
                        ft.Text(
                            "🔑 Vault ready" if vault_ok else "🔒 Vault locked",
                            size=11,
                            color="#4A7C59" if vault_ok else "#8C6A1A",
                        ),
                        ft.Icon(
                            ft.Icons.EXPAND_MORE,
                            size=13,
                            color="#4A7C59" if vault_ok else "#8C6A1A",
                        ),
                    ],
                    spacing=2,
                    tight=True,
                ),
                bgcolor="#252526" if vault_ok else "#1A1600",
                border_radius=4,
                padding=ft.Padding(3, 8, 3, 8),
                on_click=vault.toggle,
                ink=True,
                tooltip="Open Vault",
            )

            def on_nav_change(e) -> None:
                idx = e.control.selected_index
                key = NAV_ITEMS[idx][0]
                _show_view(key)
                select_by_key[key](_shared_epic["value"])
                page.update()

            def on_open_settings(_e) -> None:
                def on_settings_change() -> None:
                    kanban.refresh_board()
                    analytics_pg.refresh()
                open_settings_dialog(page, on_change=on_settings_change)

            settings_button = ft.Container(
                content=ft.Icon(
                    ft.Icons.SETTINGS_OUTLINED,
                    size=24,
                    color="#8B949E",
                ),
                padding=ft.Padding(0, 12, 0, 16),
                on_click=on_open_settings,
                ink=True,
                tooltip="Settings",
                alignment=ft.Alignment.CENTER,
            )

            sidebar = ft.NavigationRail(
                selected_index=0,
                label_type=ft.NavigationRailLabelType.ALL,
                min_width=72,
                bgcolor="#252526",
                destinations=[
                    ft.NavigationRailDestination(
                        icon=_health_icon_stack(ft.Icons.BAR_CHART_OUTLINED, _health_dot_unsel) if key == "analytics"
                            else _health_icon_stack(ft.Icons.MONITOR_HEART_OUTLINED, _health_dot_health_unsel) if key == "health"
                            else icon,
                        selected_icon=_health_icon_stack(ft.Icons.BAR_CHART, _health_dot_sel) if key == "analytics"
                            else _health_icon_stack(ft.Icons.MONITOR_HEART, _health_dot_health_sel) if key == "health"
                            else selected_icon,
                        label=label,
                    )
                    for key, icon, selected_icon, label in NAV_ITEMS
                ],
                trailing=settings_button,
                pin_trailing_to_bottom=True,
                on_change=on_nav_change,
            )

            header = ft.Container(
                content=ft.Row(
                    [
                        ft.Row(
                            [
                                ft.Container(
                                    content=ft.Text(
                                        "看板",
                                        size=13,
                                        weight=ft.FontWeight.BOLD,
                                        color="#1C2A4A",
                                    ),
                                    bgcolor="#DB8132",
                                    border_radius=6,
                                    padding=ft.Padding(6, 8, 6, 8),
                                ),
                                ft.Column(
                                    [
                                        ft.Text(
                                            "Kanbai",
                                            size=18,
                                            weight=ft.FontWeight.BOLD,
                                            color="#E6EDF3",
                                        ),
                                        ft.Text(
                                            "AI-powered Kanban",
                                            size=10,
                                            color="#8B949E",
                                            italic=True,
                                        ),
                                    ],
                                    spacing=0,
                                    tight=True,
                                ),
                            ],
                            spacing=10,
                        ),
                        ft.Row([
                            _health_header_badge,
                            vault_badge,
                            ft.Container(
                                content=ft.Text(
                                    f"{synced} card{'s' if synced != 1 else ''} loaded",
                                    size=11,
                                    color="#8B949E",
                                ),
                                padding=ft.Padding(3, 8, 3, 8),
                            ),
                        ], spacing=6),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding(10, 12, 16, 12),
                bgcolor="#252526",
                border=ft.Border(bottom=ft.BorderSide(1, "#3E3E42")),
            )

            board_area = ft.Container(
                content=ft.Row(
                    [
                        sidebar,
                        ft.VerticalDivider(width=1, color="#3E3E42"),
                        kanban_view,
                        analytics_view,
                        health_view,
                        graph_view,
                        table_view,
                    ],
                    spacing=0,
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                expand=True,
            )

            chat_overlays = chat.get_overlay_controls()
            vault_overlays = vault.get_overlay_controls()

            page.add(
                ft.Stack(
                    [
                        ft.Column(
                            [header, board_area],
                            spacing=0,
                            expand=True,
                        ),
                        *chat_overlays,
                        *vault_overlays,
                    ],
                    expand=True,
                )
            )
            _refresh_health_badge()

        except Exception:
            with open("kanbai_crash.log", "w") as f:
                _tb.print_exc(file=f)
            _tb.print_exc()
            raise

    ft.run(app, view=ft.AppView.WEB_BROWSER, port=8765, assets_dir=str(ASSETS_DIR))


def run_mcp() -> None:
    from mcp_server.server import run_mcp_server
    run_mcp_server()


def run_rekey() -> None:
    import getpass
    from vault.vault import rekey_vault, get_rekey_log
    from cryptography.exceptions import InvalidTag

    print("Kanbai Vault — Rekey")
    print("=====================")

    log = get_rekey_log()
    if log:
        last = log[0]
        print(f"Last rekeyed: {last['rekeyed_at']}  ({last['secret_count']} secret(s))")
    else:
        print("No previous rekey events.")

    print()
    old_pass = getpass.getpass("Current passphrase: ")
    new_pass = getpass.getpass("New passphrase:     ")
    new_pass2 = getpass.getpass("Confirm new:        ")

    if new_pass != new_pass2:
        print("ERROR: New passphrases do not match. Aborted.")
        raise SystemExit(1)

    try:
        count = rekey_vault(old_pass, new_pass)
        print(f"Rekeyed {count} secret(s) successfully.")
        print("Update KANBAI_VAULT_PASS in your .env / shell profile to the new passphrase.")
    except InvalidTag:
        print("ERROR: Current passphrase is wrong — no changes made.")
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kanbai — agent-compatible Kanban board")
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Run as MCP server over stdio (for external agent connections)",
    )
    parser.add_argument(
        "--rekey",
        action="store_true",
        help="Re-encrypt vault secrets under a new passphrase",
    )
    args = parser.parse_args()

    if args.mcp:
        run_mcp()
    elif args.rekey:
        run_rekey()
    else:
        run_app()


if __name__ == "__main__":
    main()

