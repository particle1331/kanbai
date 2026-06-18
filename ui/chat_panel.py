from __future__ import annotations

import json
import threading
import time

import flet as ft

from agent import chat as agent_chat
from board import settings as _settings
from board.settings import ModelDef, load_models, find_model


class ChatPanel:
    def __init__(self, page: ft.Page, on_board_change=None) -> None:
        self.page = page
        self._messages: list[dict] = []
        self._visible = False
        self._on_board_change = on_board_change  # called after agent acts
        self._mode = "ask"   # "ask" | "agent" | "plan" | "yolo"
        self._models: list[ModelDef] = load_models()
        self._model: ModelDef = self._models[0]
        self._busy = False   # guards against re-entrant / double sends
        self._build()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_overlay_controls(self) -> list[ft.Control]:
        """Return the controls that should be added to a Stack overlay."""
        return [self._panel_container, self._fab_container]

    def toggle(self, e=None) -> None:
        self._visible = not self._visible
        self._panel_container.visible = self._visible
        self._fab_container.visible = not self._visible
        self.page.update()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self._msg_list = ft.ListView(
            expand=True,
            spacing=6,
            auto_scroll=True,
            padding=ft.Padding(10, 8, 10, 4),
        )
        self._input = ft.TextField(
            hint_text="Ask Kanbai anything…",
            expand=True,
            on_submit=self._on_send,
            border_radius=20,
            content_padding=ft.Padding(8, 14, 8, 14),
        )
        self._send_btn = ft.IconButton(
            ft.Icons.SEND_ROUNDED,
            icon_color=ft.Colors.BLUE_600,
            on_click=self._on_send,
            tooltip="Send",
        )
        # typing indicator bubble (three animated dots + status label + elapsed time)
        self._dot1 = ft.Container(width=7, height=7, bgcolor="#30363D", border_radius=4)
        self._dot2 = ft.Container(width=7, height=7, bgcolor="#30363D", border_radius=4)
        self._dot3 = ft.Container(width=7, height=7, bgcolor="#30363D", border_radius=4)
        self._status_text = ft.Text("Thinking\u2026", size=11, color="#8B949E")
        self._elapsed_text = ft.Text("0s", size=11, color="#636E7A")
        self._typing_bubble = ft.Container(
            content=ft.Row(
                [self._dot1, self._dot2, self._dot3, self._status_text, self._elapsed_text],
                spacing=6,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor="#1C2128",
            border_radius=ft.BorderRadius(top_left=12, top_right=12, bottom_left=12, bottom_right=0),
            padding=ft.Padding(10, 12, 10, 12),
            margin=ft.Margin(left=10, right=60, top=0, bottom=0),
            visible=False,
        )
        self._typing_stop = threading.Event()
        self._start_time: float = 0.0
        self._tool_call_log: list[dict] = []

        # --- mode pills (ask / agent / plan / yolo) ---
        self._mode_pills: dict[str, ft.Container] = {}
        for key, label in [("ask", "Ask"), ("plan", "Plan"), ("agent", "Agent"), ("yolo", "🔥 Yolo")]:
            self._mode_pills[key] = ft.Container(
                content=ft.Text(label, size=11, weight=ft.FontWeight.W_500),
                height=42,
                padding=ft.Padding(8, 0, 8, 0),
                alignment=ft.Alignment.CENTER,
                border_radius=6,
                on_click=lambda e, k=key: self._set_mode(k),
                ink=True,
                tooltip={
                    "ask": "Read-only Q&A",
                    "agent": "Can modify the board",
                    "plan": "Probe & synthesize a plan",
                    "yolo": "Agent + permanent delete",
                }[key],
            )

        # --- model selector ---
        self._model_dd = ft.Dropdown(
            value=self._model.handle,
            options=[
                ft.dropdown.Option(m.handle, m.handle)
                for m in self._models
            ],
            width=90,
            dense=True,
            text_style=ft.TextStyle(size=11),
            content_padding=ft.Padding(6, 4, 0, 4),
            on_select=self._on_model_change,
        )

        # --- context usage + reset ---
        self._ctx_text = ft.Text(
            "ctx\n0%",
            size=10,
            color="#8B949E",
            text_align=ft.TextAlign.CENTER,
            tooltip="0 / 0 tokens",
        )
        self._reset_btn = ft.IconButton(
            ft.Icons.REFRESH_ROUNDED,
            icon_size=15,
            icon_color="#8B949E",
            tooltip="Reset context",
            on_click=self._reset_context,
        )

        control_bar = ft.Container(
            content=ft.Row(
                [
                    ft.Row(list(self._mode_pills.values()), spacing=2, tight=True),
                    ft.Row(
                        [self._model_dd, self._ctx_text, self._reset_btn],
                        spacing=4,
                        tight=True,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(8, 6, 8, 6),
            bgcolor="#1C2128",
            border=ft.Border(bottom=ft.BorderSide(1, "#30363D")),
        )
        self._render_mode_pills()

        panel = ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Row(
                                    [
                                        ft.Icon(ft.Icons.SMART_TOY_OUTLINED, size=18, color=ft.Colors.WHITE),
                                        ft.Text("Kanbai Bot", weight=ft.FontWeight.BOLD, size=14, color=ft.Colors.WHITE),
                                    ],
                                    spacing=6,
                                ),
                                ft.IconButton(
                                    ft.Icons.REMOVE,
                                    icon_size=16,
                                    icon_color=ft.Colors.WHITE,
                                    tooltip="Minimize",
                                    on_click=self.toggle,
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        bgcolor="#0D2549",
                        padding=ft.Padding(8, 6, 8, 6),
                        border_radius=ft.BorderRadius(
                            top_left=10, top_right=10, bottom_left=0, bottom_right=0
                        ),
                    ),
                    control_bar,
                    self._msg_list,
                    self._typing_bubble,
                    ft.Container(
                        content=ft.Row(
                            [self._input, self._send_btn],
                            spacing=4,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=ft.Padding(10, 8, 10, 10),
                    ),
                ],
                spacing=0,
                expand=True,
            ),
            width=432,
            height=594,
            padding=0,
            bgcolor="#161B22",
            border_radius=10,
            border=ft.Border(
                ft.BorderSide(1, "#30363D"),
                ft.BorderSide(1, "#30363D"),
                ft.BorderSide(1, "#30363D"),
                ft.BorderSide(1, "#30363D"),
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
            bottom=20,
            right=20,
        )

        fab = ft.FloatingActionButton(
            icon=ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED,
            tooltip="Open AI Chat",
            on_click=self.toggle,
            bgcolor="#1C2A4A",
            foreground_color="#58A6FF",
            mini=False,
        )
        self._fab_container = ft.Container(content=fab, bottom=20, right=20)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _token_tooltip_text(meta: dict) -> str:
        deployment = meta.get("deployment", "")
        prompt = meta.get("prompt_tokens", 0)
        completion = meta.get("completion_tokens", 0)
        reasoning = meta.get("reasoning_tokens", 0)
        total = meta.get("total_tokens", 0)

        rows: list[tuple[str, str]] = []
        if deployment:
            rows.append(("Model:", deployment))
        rows += [
            ("Input tokens:", f"{prompt:,}"),
            ("Output tokens:", f"{completion:,}"),
        ]
        if reasoning:
            rows.append(("Reasoning tokens:", f"{reasoning:,}"))
        rows.append(("Total tokens:", f"{total:,}"))

        return "\n".join(f"{lbl} {val}" for lbl, val in rows)

    def _add_bubble(self, role: str, text: str) -> ft.Container:
        is_user = role == "user"
        if is_user:
            content = ft.Text(
                text,
                size=12,
                selectable=True,
                color="#E6EDF3",
            )
        else:
            content = ft.Markdown(
                text,
                selectable=True,
                extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK,
            )
        bubble = ft.Container(
            content=content,
            bgcolor="#1C3A5C" if is_user else "#1C2128",
            border_radius=ft.BorderRadius(
                top_left=12,
                top_right=12,
                bottom_left=12 if is_user else 0,
                bottom_right=0 if is_user else 12,
            ),
            padding=ft.Padding(8, 12, 8, 12),
            margin=ft.Margin(
                left=60 if is_user else 0,
                right=0 if is_user else 60,
                top=0,
                bottom=0,
            ),
        )
        if is_user:
            self._msg_list.controls.append(bubble)
            self.page.update()
            return bubble
        bubble.tooltip = ft.Tooltip(message="", prefer_below=True, wait_duration=400)
        self._msg_list.controls.append(bubble)
        self.page.update()
        return bubble

    def _set_busy(self, busy: bool) -> None:
        self._input.disabled = busy
        self._send_btn.disabled = busy
        if busy:
            self._tool_call_log = []
            self._typing_stop.clear()
            self._start_time = time.time()
            self._status_text.value = "Thinking\u2026"
            self._elapsed_text.value = "0s"
            self._typing_bubble.visible = True
            self.page.update()
            threading.Thread(target=self._animate_dots, daemon=True).start()
        else:
            self._typing_stop.set()
            self._typing_bubble.visible = False
            self.page.update()

    def _set_status(self, text: str) -> None:
        self._status_text.value = text
        try:
            self._typing_bubble.update()
        except Exception:
            pass

    def _animate_dots(self) -> None:
        """Cycle dot colours and update elapsed time."""
        dots = [self._dot1, self._dot2, self._dot3]
        step = 0
        while not self._typing_stop.is_set():
            elapsed = int(time.time() - self._start_time)
            for i, dot in enumerate(dots):
                active = (i == step % 3)
                dot.bgcolor = "#58A6FF" if active else "#30363D"
            self._elapsed_text.value = f"{elapsed}s"
            try:
                # Only update the typing indicator — no full page repaint needed.
                self._typing_bubble.update()
            except Exception:
                break
            step += 1
            self._typing_stop.wait(0.35)

    def _on_send(self, e) -> None:
        if self._busy:
            return
        text = (self._input.value or "").strip()
        if not text:
            return
        self._busy = True
        self._input.value = ""
        self._set_busy(True)
        self._add_bubble("user", text)
        self._messages.append({"role": "user", "content": text})

        def _run() -> None:
            try:
                _settings.reload()
                # Per-turn streaming state
                # We stream into a plain ft.Text (no markdown parsing per token)
                # and swap it for ft.Markdown on the final flush.
                stream_txt: ft.Text | None = None
                stream_bubble: ft.Container | None = None
                stream_text = ""
                last_ui_t = [0.0]

                def _on_token(token: str) -> None:
                    nonlocal stream_txt, stream_bubble, stream_text
                    stream_text += token
                    if stream_txt is None:
                        self._set_status("Typing\u2026")
                        stream_txt, stream_bubble = self._start_streaming_bubble()
                    stream_txt.value = stream_text
                    now = time.time()
                    if now - last_ui_t[0] >= 0.12:
                        last_ui_t[0] = now
                        try:
                            # Only repaint the message list — not the whole page.
                            self._msg_list.update()
                        except Exception:
                            pass

                _WRITE_NAMES = {
                    "create_card", "update_card", "move_card", "archive_card",
                    "unarchive_card", "delete_card", "set_card_epic",
                    "create_epic", "archive_epic", "unarchive_epic",
                    "link_cards", "unlink_cards",
                }
                base_board_refresh = self._on_board_change

                def _on_tool_call(name: str, args: dict) -> None:
                    self._tool_call_log.append({"name": name, "args": args})
                    self._set_status("Executing\u2026")
                    if name in _WRITE_NAMES and base_board_refresh:
                        # Run in a background thread so it doesn't block the
                        # agent loop — board UI updates are nice-to-have during
                        # execution, not on the critical path.
                        threading.Thread(target=base_board_refresh, daemon=True).start()

                reply, updated, meta = agent_chat.chat(
                    self._messages, mode=self._mode, model=self._model,
                    on_token=_on_token,
                    on_tool_call=_on_tool_call,
                    reasoning_effort=_settings.reasoning_effort(),
                    max_completion_tokens=_settings.max_completion_tokens(),
                    stream=_settings.stream_response(),
                    max_steps=_settings.max_agent_steps(),
                )
                self._messages = [
                    m for m in updated if m.get("role") in ("user", "assistant")
                ]
                # Final flush — replace streaming ft.Text with rendered ft.Markdown,
                # then do a single full page.update() to repaint everything at once.
                if stream_txt is not None and stream_bubble is not None:
                    final_md = ft.Markdown(
                        stream_text,
                        selectable=True,
                        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                        code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK,
                    )
                    stream_bubble.content = final_md
                    try:
                        self.page.update()
                    except Exception:
                        pass
                elif reply:
                    stream_bubble = self._add_bubble("assistant", reply)
                # Tool call log bubble goes after the reply bubble
                if self._tool_call_log:
                    self._add_tool_log_bubble(list(self._tool_call_log))
                # Truncation warning
                if meta.get("finish_reason") == "length":
                    self._add_truncation_warning()
                # Token breakdown tooltip on the reply bubble
                if stream_bubble is not None:
                    stream_bubble.tooltip = ft.Tooltip(
                        message=self._token_tooltip_text(meta),
                        prefer_below=True,
                        wait_duration=400,
                    )
                    try:
                        self.page.update()
                    except Exception:
                        pass
                self._update_ctx(meta)
                if meta.get("board_changed") and self._on_board_change:
                    self._on_board_change()
            except Exception as ex:
                self._add_bubble("assistant", f"\u26a0 {ex}")
            finally:
                self._busy = False
                self._set_busy(False)

        threading.Thread(target=_run, daemon=True).start()

    # ------------------------------------------------------------------
    # Mode / model / context controls
    # ------------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._render_mode_pills()
        self.page.update()

    def _render_mode_pills(self) -> None:
        for key, pill in self._mode_pills.items():
            active = key == self._mode
            if key == "yolo":
                pill.bgcolor = "#4A1F1F" if active else None
                pill.content.color = "#F07878" if active else "#8B949E"
            else:
                pill.bgcolor = "#1C3A5C" if active else None
                pill.content.color = "#58A6FF" if active else "#8B949E"

    def _toggle_yolo(self, e=None) -> None:
        self._yolo = not self._yolo
        self._render_yolo_pill()
        self.page.update()

    def _render_yolo_pill(self) -> None:
        self._yolo_pill.bgcolor = "#4A1F1F" if self._yolo else None
        self._yolo_pill.content.color = "#F07878" if self._yolo else "#8B949E"

    def _on_model_change(self, e) -> None:
        self._model = find_model(self._model_dd.value or self._models[0].handle, self._models)

    def _update_ctx(self, meta: dict) -> None:
        pct = meta.get("percent", 0)
        total_tokens = meta.get("total_tokens", 0)
        context_window = meta.get("context_window", 0)
        self._ctx_text.value = f"ctx\n{pct:g}%"
        self._ctx_text.tooltip = f"{total_tokens:,} / {context_window:,} tokens"
        if pct >= 85:
            self._ctx_text.color = "#F85149"
        elif pct >= 60:
            self._ctx_text.color = "#D29922"
        else:
            self._ctx_text.color = "#8B949E"
        self.page.update()

    def _add_truncation_warning(self) -> None:
        """Append a red warning note when the response was cut off by the token limit."""
        warning = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=13, color="#F85149"),
                    ft.Text(
                        spans=[
                            ft.TextSpan(
                                "Response cut off — max completion tokens reached. Increase in ",
                                style=ft.TextStyle(size=11, color="#F85149", italic=True),
                            ),
                            ft.TextSpan(
                                "\u2699 Settings.",
                                style=ft.TextStyle(size=11, color="#F85149", italic=False),
                            ),
                        ],
                        expand=True,
                    ),
                ],
                spacing=5,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            margin=ft.Margin(left=10, right=10, top=0, bottom=0),
        )
        self._msg_list.controls.append(warning)
        try:
            self.page.update()
        except Exception:
            pass

    def _add_tool_log_bubble(self, tool_calls: list[dict]) -> None:
        """Insert a permanent collapsible tool-call record into the chat."""
        count = len(tool_calls)
        label = ft.Text(
            f"{count} tool call{'s' if count != 1 else ''} executed",
            size=11,
            color="#8B5CF6",
            weight=ft.FontWeight.W_500,
        )
        chevron = ft.Icon(ft.Icons.EXPAND_MORE, size=14, color="#8B5CF6")

        detail_rows = []
        for entry in tool_calls:
            args_str = json.dumps(entry["args"], indent=2)
            detail_rows.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(entry["name"], size=11, weight=ft.FontWeight.W_600, color="#58A6FF"),
                            ft.Text(args_str, size=10, color="#8B949E", selectable=True,
                                    font_family="monospace"),
                        ],
                        spacing=2,
                        tight=True,
                    ),
                    bgcolor="#161B22",
                    border_radius=6,
                    padding=ft.Padding(8, 6, 8, 6),
                    margin=ft.Margin(left=0, right=0, top=4, bottom=0),
                )
            )

        detail_col = ft.Column(detail_rows, spacing=0, visible=False, tight=True)

        def _toggle(_e) -> None:
            detail_col.visible = not detail_col.visible
            chevron.name = ft.Icons.EXPAND_LESS if detail_col.visible else ft.Icons.EXPAND_MORE
            try:
                self.page.update()
            except Exception:
                pass

        header = ft.Container(
            content=ft.Row(
                [ft.Icon(ft.Icons.BUILD_CIRCLE_OUTLINED, size=13, color="#8B5CF6"),
                 label, chevron],
                spacing=4,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            on_click=_toggle,
            ink=True,
            border_radius=6,
        )
        bubble = ft.Container(
            content=ft.Column([header, detail_col], spacing=0, tight=True),
            bgcolor="#1A1428",
            border_radius=ft.BorderRadius(top_left=8, top_right=8, bottom_left=8, bottom_right=8),
            border=ft.Border(
                ft.BorderSide(1, "#3D2A6B"), ft.BorderSide(1, "#3D2A6B"),
                ft.BorderSide(1, "#3D2A6B"), ft.BorderSide(1, "#3D2A6B"),
            ),
            padding=ft.Padding(10, 8, 10, 8),
            margin=ft.Margin(left=10, right=60, top=0, bottom=0),
        )
        self._msg_list.controls.append(bubble)
        try:
            self.page.update()
        except Exception:
            pass

    def _start_streaming_bubble(self) -> tuple[ft.Text, ft.Container]:
        """Append an empty assistant bubble; return (Text, Container) for live token updates.

        We use a plain ft.Text during streaming to avoid re-parsing the growing
        markdown string on every token update.  The caller swaps it out for
        ft.Markdown on the final flush.
        """
        txt = ft.Text(
            "",
            selectable=True,
            size=12,
            color="#E6EDF3",
        )
        bubble = ft.Container(
            content=txt,
            bgcolor="#1C2128",
            border_radius=ft.BorderRadius(top_left=12, top_right=12, bottom_left=0, bottom_right=12),
            padding=ft.Padding(8, 12, 8, 12),
            margin=ft.Margin(left=0, right=60, top=0, bottom=0),
            tooltip=ft.Tooltip(message="", prefer_below=True, wait_duration=400),
        )
        self._msg_list.controls.append(bubble)
        try:
            self.page.update()
        except Exception:
            pass
        return txt, bubble

    def _reset_context(self, e=None) -> None:
        if not self._messages:
            return

        def _cancel(ev):
            dlg.open = False
            self.page.update()

        def _confirm(ev):
            dlg.open = False
            self._messages = []
            self._msg_list.controls.clear()
            self._ctx_text.value = "ctx\n0%"
            self._ctx_text.color = ft.Colors.GREY_500
            self.page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Reset context?"),
            content=ft.Text(
                "This clears the current conversation and the model's context. "
                "This can't be undone."
            ),
            actions=[
                ft.TextButton("Cancel", on_click=_cancel),
                ft.FilledButton(
                    "Reset",
                    on_click=_confirm,
                    style=ft.ButtonStyle(bgcolor=ft.Colors.RED_600, color=ft.Colors.WHITE),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.overlay.append(dlg)
        dlg.open = True
        self.page.update()
