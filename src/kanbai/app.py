"""Flet application — the human command center for Kanbai."""

from __future__ import annotations


def run_app(port: int = 8765) -> None:
    import json
    import flet as ft
    import traceback as _tb

    from kanbai.paths import ASSETS_DIR
    from kanbai import paths
    from kanbai.board.storage.sync import full_sync
    from kanbai.ui.card_dialog import open_create_dialog, open_edit_dialog
    from kanbai.ui.chat_panel import ChatPanel
    from kanbai.ui.search_overlay import open_search_overlay
    from kanbai.ui.kanban_page import KanbanPage
    from kanbai.ui.table_page import TablePage
    from kanbai.ui.health_page import HealthPage
    from kanbai.ui.graph_page import GraphPage
    from kanbai.ui.settings_dialog import open_settings_dialog

    async def app(page: ft.Page) -> None:
        try:
            page.title = "Kanbai"
            page.theme_mode = ft.ThemeMode.DARK
            page.bgcolor = "#1E1E1E"
            page.padding = 0
            page.window.width = 1500
            page.window.height = 1300
            page.update()
            try:
                await page.window.center()
            except Exception:
                pass
            page.window.min_width = 900
            page.window.min_height = 600

            synced = full_sync()

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
                    threading.Thread(target=lambda: (kanban.refresh_board(), table_pg.refresh(), health_pg.refresh(), _refresh_health_badge(), _refresh_git_status()), daemon=True).start()
                def on_moved(cid, old_col, new_col):
                    import threading
                    threading.Thread(target=lambda: (kanban.refresh_board(), table_pg.refresh(), health_pg.refresh(), _refresh_health_badge(), _refresh_git_status()), daemon=True).start()
                def on_archived(cid, col):
                    import threading
                    threading.Thread(target=lambda: (kanban.refresh_board(), table_pg.refresh(), health_pg.refresh(), _refresh_health_badge(), _refresh_git_status()), daemon=True).start()
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

            NAV_ITEMS = [
                ("board", ft.Icons.VIEW_KANBAN_OUTLINED, ft.Icons.VIEW_KANBAN, "Board"),
                ("health", ft.Icons.MONITOR_HEART_OUTLINED, ft.Icons.MONITOR_HEART, "Health"),
                ("graph", ft.Icons.ACCOUNT_TREE_OUTLINED, ft.Icons.ACCOUNT_TREE, "Graph"),
                ("table", ft.Icons.TABLE_ROWS_OUTLINED, ft.Icons.TABLE_ROWS, "Table"),
            ]
            NAV_INDEX = {key: i for i, (key, *_rest) in enumerate(NAV_ITEMS)}

            _health_dot_health_unsel = ft.Container(width=9, height=9, border_radius=5, visible=False, right=0, top=2)
            _health_dot_health_sel   = ft.Container(width=9, height=9, border_radius=5, visible=False, right=0, top=2)

            _all_nav_dots = (
                _health_dot_health_unsel, _health_dot_health_sel,
            )

            def _health_icon_stack(base_icon: str, dot: ft.Container) -> ft.Stack:
                return ft.Stack(
                    [ft.Icon(base_icon, size=24), dot],
                    width=28,
                    height=28,
                )

            _health_header_badge = ft.Container(visible=False)
            _git_state: dict = {"is_repo": None, "syncing": False}
            _git_badge = ft.Container(visible=False)

            def _refresh_health_badge() -> None:
                from kanbai.board.health import project_health
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
                    page.update(_health_header_badge, *_all_nav_dots)
                except Exception:
                    pass

            def _refresh_git_status() -> None:
                from kanbai.board.storage import git_ops
                from kanbai.board import settings as _s
                # Respect git_tracking setting
                if not _s._load().get("git_tracking", True):
                    return
                if _git_state["syncing"]:
                    return
                try:
                    if _git_state["is_repo"] is None:
                        _git_state["is_repo"] = git_ops.is_git_repo()
                    if not _git_state["is_repo"]:
                        return
                    dirty = git_ops.is_dirty()
                    if dirty:
                        _git_badge.content = ft.Row(
                            [
                                ft.Icon(ft.Icons.CLOUD_UPLOAD_OUTLINED, size=12, color="#F0A742"),
                                ft.Text("uncommitted", size=11, color="#F0A742"),
                            ],
                            spacing=4,
                            tight=True,
                        )
                        _git_badge.bgcolor = "#2A2200"
                        _git_badge.border_radius = 4
                        _git_badge.padding = ft.Padding(6, 4, 6, 4)
                        _git_badge.tooltip = ft.Tooltip(
                            message="Board data has uncommitted changes \u2014 click to commit & push",
                            prefer_below=True,
                            wait_duration=200,
                        )
                        _git_badge.on_click = lambda _e: _do_git_sync()
                        _git_badge.ink = True
                        _git_badge.visible = True
                    else:
                        _git_badge.visible = False
                    try:
                        page.update(_git_badge)
                    except Exception:
                        pass
                except Exception:
                    pass

            def _do_git_sync() -> None:
                if _git_state["syncing"]:
                    return
                _git_state["syncing"] = True
                _git_badge.content = ft.Row(
                    [
                        ft.ProgressRing(width=11, height=11, stroke_width=2, color="#58A6FF"),
                        ft.Text("syncing\u2026", size=11, color="#58A6FF"),
                    ],
                    spacing=4,
                    tight=True,
                )
                _git_badge.bgcolor = "#1C3A5C"
                _git_badge.border_radius = 4
                _git_badge.padding = ft.Padding(6, 4, 6, 4)
                _git_badge.tooltip = None
                _git_badge.on_click = None
                _git_badge.ink = False
                _git_badge.visible = True
                try:
                    page.update(_git_badge)
                except Exception:
                    pass

                def _worker() -> None:
                    from kanbai.board.storage import git_ops
                    try:
                        ok, msg = git_ops.sync_push()
                    except Exception as exc:
                        ok, msg = False, str(exc)
                    _git_state["syncing"] = False
                    if ok:
                        _git_badge.visible = False
                    else:
                        _git_badge.content = ft.Row(
                            [
                                ft.Icon(ft.Icons.CLOUD_OFF_OUTLINED, size=12, color="#F85149"),
                                ft.Text("sync failed", size=11, color="#F85149"),
                            ],
                            spacing=4,
                            tight=True,
                        )
                        _git_badge.bgcolor = "#2A0A08"
                        _git_badge.border_radius = 4
                        _git_badge.padding = ft.Padding(6, 4, 6, 4)
                        _git_badge.tooltip = ft.Tooltip(message=msg, prefer_below=True, wait_duration=200)
                        _git_badge.on_click = lambda _e: _do_git_sync()
                        _git_badge.ink = True
                        _git_badge.visible = True
                    try:
                        page.update(_git_badge)
                    except Exception:
                        pass

                import threading
                threading.Thread(target=_worker, daemon=True).start()

            def _start_git_poll() -> None:
                import threading, time
                def _poll():
                    while True:
                        time.sleep(30)
                        _refresh_git_status()
                threading.Thread(target=_poll, daemon=True).start()

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
                on_open_search=lambda: open_search_overlay(page, on_open_card),
            )
            table_pg = TablePage(page, on_open_card)
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
            health_view = health_pg.build()
            graph_view = graph_pg.build()

            view_by_key = {
                "board": kanban_view,
                "health": health_view,
                "graph": graph_view,
                "table": table_view,
            }
            select_by_key = {
                "board": kanban.select_epic,
                "health": health_pg.select_epic,
                "graph": graph_pg.select_epic,
                "table": table_pg.select_epic,
            }

            _saved_epic = _load_ui_state().get("last_epic")
            if _saved_epic:
                _shared_epic["value"] = _saved_epic
                for _sel_fn in select_by_key.values():
                    _sel_fn(_saved_epic)

            for _key, _view in view_by_key.items():
                _view.visible = (_key == "board")

            def _show_view(key: str) -> None:
                for k, v in view_by_key.items():
                    v.visible = (k == key)

            def on_board_change() -> None:
                kanban.refresh_board()
                kanban.refresh_epics()
                table_pg.refresh()
                graph_pg.refresh_epics()
                _refresh_health_badge()
                _refresh_git_status()

            chat = ChatPanel(page, on_board_change=on_board_change)

            def on_nav_change(e) -> None:
                idx = e.control.selected_index
                key = NAV_ITEMS[idx][0]
                _show_view(key)
                select_by_key[key](_shared_epic["value"])
                if key == "health":
                    health_pg.refresh()
                page.update()

            def on_open_settings(_e) -> None:
                def on_settings_change() -> None:
                    kanban.refresh_board()
                open_settings_dialog(page, on_change=on_settings_change)

            def _on_open_health_doc(_e) -> None:
                from kanbai.board.health_doc import health_model_doc
                doc_md = health_model_doc()
                dlg = ft.AlertDialog(
                    modal=False,
                    title=ft.Text("Kanban Principles & Health Model", size=16, weight=ft.FontWeight.BOLD),
                    content=ft.Container(
                        content=ft.Column(
                            [ft.Markdown(
                                doc_md,
                                selectable=True,
                                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                                md_style_sheet=ft.MarkdownStyleSheet(
                                    block_spacing=4,
                                    h2_padding=ft.Padding(0, 12, 0, 4),
                                    h3_padding=ft.Padding(0, 8, 0, 2),
                                    p_padding=ft.Padding(0, 0, 0, 4),
                                ),
                            )],
                            scroll=ft.ScrollMode.AUTO,
                            expand=True,
                            spacing=0,
                        ),
                        width=800,
                        height=560,
                        padding=ft.Padding(8, 0, 8, 0),
                    ),
                    actions=[ft.TextButton("Close", on_click=lambda e: page.pop_dialog())],
                    actions_alignment=ft.MainAxisAlignment.END,
                )
                page.show_dialog(dlg)

            health_doc_button = ft.Container(
                content=ft.Icon(ft.Icons.MENU_BOOK_OUTLINED, size=24, color="#8B949E"),
                padding=ft.Padding(0, 12, 0, 4),
                on_click=_on_open_health_doc,
                ink=True,
                tooltip="Kanban Principles & Health Model",
                alignment=ft.Alignment.CENTER,
            )

            settings_button = ft.Container(
                content=ft.Icon(ft.Icons.SETTINGS_OUTLINED, size=24, color="#8B949E"),
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
                        icon=_health_icon_stack(ft.Icons.MONITOR_HEART_OUTLINED, _health_dot_health_unsel) if key == "health"
                            else icon,
                        selected_icon=_health_icon_stack(ft.Icons.MONITOR_HEART, _health_dot_health_sel) if key == "health"
                            else selected_icon,
                        label=label,
                    )
                    for key, icon, selected_icon, label in NAV_ITEMS
                ],
                trailing=ft.Column(
                    [health_doc_button, settings_button],
                    spacing=0,
                    tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                pin_trailing_to_bottom=True,
                on_change=on_nav_change,
            )

            header = ft.Container(
                content=ft.Row(
                    [
                        ft.Row(
                            [
                                ft.Container(
                                    content=ft.Text("看板", size=13, weight=ft.FontWeight.BOLD, color="#1C2A4A"),
                                    bgcolor="#DB8132",
                                    border_radius=6,
                                    padding=ft.Padding(6, 8, 6, 8),
                                ),
                                ft.Column(
                                    [
                                        ft.Text("Kanbai", size=18, weight=ft.FontWeight.BOLD, color="#E6EDF3"),
                                        ft.Text("AI-powered Kanban", size=10, color="#8B949E", italic=True),
                                    ],
                                    spacing=0,
                                    tight=True,
                                ),
                            ],
                            spacing=10,
                        ),
                        ft.Row([
                            _health_header_badge,
                            _git_badge,
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

            page.add(
                ft.Stack(
                    [
                        ft.Column(
                            [header, board_area],
                            spacing=0,
                            expand=True,
                        ),
                        *chat_overlays,
                    ],
                    expand=True,
                )
            )
            _refresh_health_badge()
            _refresh_git_status()
            _start_git_poll()

            # File watcher: auto-refresh when agents/CLI modify .kanbai/
            from kanbai.watcher import start_watcher
            from kanbai.paths import BOARD_DIR as _board_dir

            def _on_external_change() -> None:
                """Called (from watcher thread) when .kanbai/ files change externally."""
                try:
                    from kanbai.board.storage.sync import full_sync
                    full_sync()
                    on_board_change()
                except Exception:
                    pass

            _watcher = start_watcher(_board_dir, _on_external_change)

            _prev_kb_handler = page.on_keyboard_event

            def _global_keyboard(e: ft.KeyboardEvent) -> None:
                if e.key == "K" and (e.ctrl or e.meta):
                    open_search_overlay(page, on_open_card)
                    return
                if _prev_kb_handler:
                    _prev_kb_handler(e)

            page.on_keyboard_event = _global_keyboard

        except Exception:
            with open("kanbai_crash.log", "w") as f:
                _tb.print_exc(file=f)
            _tb.print_exc()
            raise

    ft.run(app, view=ft.AppView.WEB_BROWSER, port=port, assets_dir=str(ASSETS_DIR))
