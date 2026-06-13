from __future__ import annotations

import flet as ft

from vault.vault import (
    VaultLockedException,
    delete_secret,
    get_secret,
    is_unlocked,
    list_secret_names,
    store_secret,
)


class VaultPanel:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self._visible = False
        self._build()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_overlay_controls(self) -> list[ft.Control]:
        """Return only the panel container (no FAB — trigger is in the header)."""
        return [self._panel_container]

    def toggle(self, e=None) -> None:
        self._visible = not self._visible
        self._panel_container.visible = self._visible
        if self._visible:
            self._refresh()
        self.page.update()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self._secret_list = ft.ListView(expand=True, spacing=4, padding=ft.Padding(0, 0, 0, 4))

        self._name_field = ft.TextField(
            label="Name",
            hint_text="MY_API_KEY",
            expand=True,
            border_radius=8,
            content_padding=ft.Padding(8, 10, 8, 10),
        )
        self._value_field = ft.TextField(
            label="Value",
            password=True,
            can_reveal_password=True,
            expand=True,
            border_radius=8,
            content_padding=ft.Padding(8, 10, 8, 10),
        )
        self._add_error = ft.Text("", color=ft.Colors.RED_400, size=11)
        self._status_text = ft.Text("", size=11)

        add_form = ft.Column(
            [
                ft.Text("Add secret", size=12, weight=ft.FontWeight.W_600, color="#8B949E"),
                ft.Row([self._name_field, self._value_field], spacing=6),
                ft.Row(
                    [
                        self._add_error,
                        ft.FilledButton("Save", on_click=self._on_add, style=ft.ButtonStyle(
                            padding=ft.Padding(0, 16, 0, 16)
                        )),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=6,
            tight=True,
        )

        panel = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Row(
                                [
                                    ft.Icon(ft.Icons.LOCK_OUTLINE, size=16, color="#D29922"),
                                    ft.Text("Vault", weight=ft.FontWeight.BOLD, size=14, color="#E6EDF3"),
                                ],
                                spacing=6,
                            ),
                            ft.IconButton(
                                ft.Icons.CANCEL,
                                icon_size=16,
                                icon_color="#8B949E",
                                on_click=self.toggle,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=1, color="#30363D"),
                    self._status_text,
                    self._secret_list,
                    ft.Divider(height=1, color="#30363D"),
                    add_form,
                ],
                spacing=8,
                expand=True,
            ),
            width=380,
            height=520,
            padding=12,
            bgcolor="#161B22",
            border_radius=10,
            border=ft.Border(
                ft.BorderSide(1, "#5C4100"),
                ft.BorderSide(1, "#5C4100"),
                ft.BorderSide(1, "#5C4100"),
                ft.BorderSide(1, "#5C4100"),
            ),
            shadow=ft.BoxShadow(
                blur_radius=24,
                color=ft.Colors.with_opacity(0.5, ft.Colors.BLACK),
                offset=ft.Offset(0, 6),
            ),
        )

        self._panel_container = ft.Container(
            content=panel,
            visible=False,
            top=52,   # just below the header
            right=16,
        )

        fab = ft.FloatingActionButton(
            icon=ft.Icons.KEY_OUTLINED,
            tooltip="Open Vault",
            on_click=self.toggle,
            bgcolor=ft.Colors.AMBER_600,
            foreground_color=ft.Colors.WHITE,
            mini=True,
        )
        self._fab_container = ft.Container(content=fab, bottom=26, right=90)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        self._secret_list.controls.clear()

        if not is_unlocked():
            self._status_text.value = "🔒 Vault locked — set env KANBAI_VAULT_PASS and restart."
            self._status_text.color = "#D29922"
            self.page.update()
            return

        names = list_secret_names()
        self._status_text.value = f"{len(names)} secret{'s' if len(names) != 1 else ''} stored."
        self._status_text.color = "#8B949E"

        for name in names:
            self._secret_list.controls.append(self._make_row(name))

        self.page.update()

    def _make_row(self, name: str) -> ft.Container:
        value_text = ft.Text(
            "••••••••",
            size=12,
            color="#8B949E",
            selectable=True,
            expand=True,
        )
        revealed = {"v": False}

        def _toggle_reveal(e, n=name):
            if not revealed["v"]:
                try:
                    value_text.value = get_secret(n)
                    value_text.color = "#E6EDF3"
                    reveal_btn.icon = ft.Icons.VISIBILITY_OFF_OUTLINED
                    revealed["v"] = True
                except Exception as ex:
                    value_text.value = f"⚠ {ex}"
            else:
                value_text.value = "••••••••"
                value_text.color = "#8B949E"
                reveal_btn.icon = ft.Icons.VISIBILITY_OUTLINED
                revealed["v"] = False
            self.page.update()

        def _delete(e, n=name):
            try:
                delete_secret(n)
                self._refresh()
            except VaultLockedException:
                pass

        reveal_btn = ft.IconButton(
            ft.Icons.VISIBILITY_OUTLINED,
            icon_size=16,
            icon_color="#8B949E",
            tooltip="Reveal",
            on_click=_toggle_reveal,
        )

        return ft.Container(
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(name, size=12, weight=ft.FontWeight.W_600, color="#C9D1D9"),
                            value_text,
                        ],
                        spacing=2,
                        tight=True,
                        expand=True,
                    ),
                    reveal_btn,
                    ft.IconButton(
                        ft.Icons.DELETE_OUTLINE,
                        icon_size=16,
                        icon_color="#7A1A1A",
                        tooltip="Delete",
                        on_click=_delete,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor="#1C2128",
            border_radius=6,
            padding=ft.Padding(8, 6, 4, 6),
        )

    def _on_add(self, e) -> None:
        name = (self._name_field.value or "").strip()
        value = (self._value_field.value or "").strip()

        if not name:
            self._add_error.value = "Name is required."
            self.page.update()
            return
        if not value:
            self._add_error.value = "Value is required."
            self.page.update()
            return

        try:
            if name in list_secret_names():
                self._add_error.value = f'"{name}" already exists.'
                self.page.update()
                return
            store_secret(name, value)
            self._name_field.value = ""
            self._value_field.value = ""
            self._add_error.value = ""
            self._refresh()
        except VaultLockedException:
            self._add_error.value = "Vault is locked."
            self.page.update()
