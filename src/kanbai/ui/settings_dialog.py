from __future__ import annotations

import datetime as dt
import threading

import flet as ft

from kanbai.board import settings
from kanbai.board.settings import MAX_AGENT_STEP_OPTIONS, MAX_COMPLETION_TOKEN_OPTIONS, ReasoningEffort

_BG = "#252526"
_BORDER = "#3E3E42"
_TEXT_PRIMARY = "#E6EDF3"
_TEXT_SECONDARY = "#8B949E"


def _local_tz_name() -> str:
    try:
        return dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Z")
    except Exception:
        return ""


def open_settings_dialog(page: ft.Page, on_change=None) -> None:
    """Open the app-wide Settings dialog.

    ``on_change`` is called whenever a setting that affects board rendering is
    changed, so the caller can refresh the kanban / analytics views.
    """
    tz_name = _local_tz_name()
    tz_subtitle = (
        f"Dates shown in local time ({tz_name}). Click to switch to UTC."
        if settings.use_local_tz()
        else "Dates shown in UTC. Click to switch to local time."
    )
    tz_caption = ft.Text(tz_subtitle, size=11, color=_TEXT_SECONDARY)

    tz_label = ft.Text(
        "Local" if settings.use_local_tz() else "UTC",
        size=11,
        color=_TEXT_SECONDARY,
    )

    def _on_tz_toggle(_e) -> None:
        settings.set_use_local_tz(not settings.use_local_tz())
        on = settings.use_local_tz()
        tz_label.value = "Local" if on else "UTC"
        tz_caption.value = (
            f"Dates shown in local time ({tz_name}). Click to switch to UTC."
            if on
            else "Dates shown in UTC. Click to switch to local time."
        )
        page.update()
        if on_change is not None:
            on_change()

    tz_chip = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.SCHEDULE_OUTLINED, size=13, color=_TEXT_SECONDARY),
                tz_label,
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor="#21262D",
        border=ft.Border(
            ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER),
            ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER),
        ),
        border_radius=6,
        padding=ft.Padding(8, 4, 8, 4),
        on_click=_on_tz_toggle,
        ink=True,
        tooltip="Toggle local time / UTC",
    )

    tz_row = ft.Row(
        [
            ft.Column(
                [
                    ft.Text("Timezone", size=13, weight=ft.FontWeight.W_600, color=_TEXT_PRIMARY),
                    tz_caption,
                ],
                spacing=2,
                tight=True,
                expand=True,
            ),
            tz_chip,
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # ------------------------------------------------------------------ AI
    current_effort = settings.reasoning_effort()
    effort_pills: dict[ReasoningEffort, ft.Container] = {}

    def _make_effort_pill(effort: ReasoningEffort) -> ft.Container:
        active = effort == current_effort
        return ft.Container(
            content=ft.Text(effort.label, size=11, weight=ft.FontWeight.W_500,
                            color="#58A6FF" if active else _TEXT_SECONDARY),
            height=28,
            padding=ft.Padding(10, 0, 10, 0),
            alignment=ft.Alignment.CENTER,
            border_radius=6,
            bgcolor="#1C3A5C" if active else None,
            border=ft.Border(
                ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER),
            ),
            ink=True,
            on_click=lambda e, ef=effort: _set_effort(ef),
            tooltip=effort.tooltip,
        )

    def _set_effort(effort: ReasoningEffort) -> None:
        settings.set_reasoning_effort(effort)
        for ef, pill in effort_pills.items():
            active = ef == effort
            pill.bgcolor = "#1C3A5C" if active else None
            pill.content.color = "#58A6FF" if active else _TEXT_SECONDARY
        page.update()

    for ef in ReasoningEffort:
        effort_pills[ef] = _make_effort_pill(ef)

    effort_row = ft.Row(
        [
            ft.Column(
                [
                    ft.Text("Reasoning effort", size=13, weight=ft.FontWeight.W_600, color=_TEXT_PRIMARY),
                    ft.Text("Ignored by models that don't support it", size=11, color=_TEXT_SECONDARY),
                ],
                spacing=2, tight=True, expand=True,
            ),
            ft.Row(list(effort_pills.values()), spacing=4, tight=True),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    _default_tokens = str(settings.max_completion_tokens())
    _token_strs = [str(v) for v in MAX_COMPLETION_TOKEN_OPTIONS]
    tokens_dd = ft.Dropdown(
        value=_default_tokens if _default_tokens in _token_strs else str(MAX_COMPLETION_TOKEN_OPTIONS[2]),
        options=[ft.dropdown.Option(str(v), str(v)) for v in MAX_COMPLETION_TOKEN_OPTIONS],
        width=110,
        dense=True,
        text_style=ft.TextStyle(size=12),
        content_padding=ft.Padding(8, 4, 0, 4),
        on_select=lambda e: settings.set_max_completion_tokens(int(e.control.value)),
    )
    tokens_row = ft.Row(
        [
            ft.Column(
                [
                    ft.Text("Max completion tokens", size=13, weight=ft.FontWeight.W_600, color=_TEXT_PRIMARY),
                    ft.Text("Response token budget", size=11, color=_TEXT_SECONDARY),
                ],
                spacing=2, tight=True, expand=True,
            ),
            tokens_dd,
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    _default_steps = str(settings.max_agent_steps())
    _step_strs = [str(v) for v in MAX_AGENT_STEP_OPTIONS]
    steps_dd = ft.Dropdown(
        value=_default_steps if _default_steps in _step_strs else str(MAX_AGENT_STEP_OPTIONS[1]),
        options=[ft.dropdown.Option(str(v), str(v)) for v in MAX_AGENT_STEP_OPTIONS],
        width=110,
        dense=True,
        text_style=ft.TextStyle(size=12),
        content_padding=ft.Padding(8, 4, 0, 4),
        on_select=lambda e: settings.set_max_agent_steps(int(e.control.value)),
    )
    steps_row = ft.Row(
        [
            ft.Column(
                [
                    ft.Text("Max agent steps", size=13, weight=ft.FontWeight.W_600, color=_TEXT_PRIMARY),
                    ft.Text("Tool-calling turns before returning to you", size=11, color=_TEXT_SECONDARY),
                ],
                spacing=2, tight=True, expand=True,
            ),
            steps_dd,
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # Stream toggle
    _stream_label = ft.Text(
        "On" if settings.stream_response() else "Off",
        size=11, color=_TEXT_SECONDARY,
    )

    def _on_stream_toggle(_e) -> None:
        settings.set_stream_response(not settings.stream_response())
        _stream_label.value = "On" if settings.stream_response() else "Off"
        page.update()

    stream_chip = ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.STREAM_OUTLINED, size=13, color=_TEXT_SECONDARY),
                _stream_label,
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor="#21262D",
        border=ft.Border(
            ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER),
            ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER),
        ),
        border_radius=6,
        padding=ft.Padding(8, 4, 8, 4),
        on_click=_on_stream_toggle,
        ink=True,
        tooltip="Toggle streaming. Off delivers the full response at once.",
    )

    stream_row = ft.Row(
        [
            ft.Column(
                [
                    ft.Text("Stream response", size=13, weight=ft.FontWeight.W_600, color=_TEXT_PRIMARY),
                    ft.Text("Off delivers the full response at once", size=11, color=_TEXT_SECONDARY),
                ],
                spacing=2, tight=True, expand=True,
            ),
            stream_chip,
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # ------------------------------------------------------------------ Storage
    from kanbai.board import operations as _ops
    from kanbai.board import validation as _val

    _att_max_mb = _val.MAX_TOTAL_ATTACHMENTS_MB
    _att_warn_mb = _val.WARN_TOTAL_ATTACHMENTS_MB

    def _storage_bar_color(mb: float) -> str:
        if mb >= _att_max_mb:
            return "#F85149"
        if mb >= _att_warn_mb:
            return "#F0A742"
        return "#56D364"

    _att_mb = _ops.get_attachments_total_size_bytes() / (1024 * 1024)

    _storage_label = ft.Text(
        f"{_att_mb:.1f} MB / {_att_max_mb} MB used for attachments",
        size=11, color=_TEXT_SECONDARY,
    )
    _storage_bar = ft.ProgressBar(
        value=min(_att_mb / _att_max_mb, 1.0) if _att_max_mb else 0,
        bgcolor="#30363D",
        color=_storage_bar_color(_att_mb),
        height=4,
        border_radius=2,
    )
    _cleanup_result = ft.Text("", size=11, color=_TEXT_SECONDARY)

    def _make_pill(label: str, color: str, bgcolor: str | None = None,
                   border_color: str = _BORDER) -> ft.Container:
        return ft.Container(
            content=ft.Text(label, size=11, weight=ft.FontWeight.W_500, color=color),
            height=28,
            padding=ft.Padding(10, 0, 10, 0),
            alignment=ft.Alignment.CENTER,
            border_radius=6,
            bgcolor=bgcolor,
            border=ft.Border(
                ft.BorderSide(1, border_color), ft.BorderSide(1, border_color),
                ft.BorderSide(1, border_color), ft.BorderSide(1, border_color),
            ),
            ink=True,
        )

    _cleanup_dryrun_pill = _make_pill("Dry run", _TEXT_SECONDARY)
    _cleanup_run_pill    = _make_pill("Run cleanup", "#F85149", bgcolor="#2A0A08",
                                      border_color="#5A2020")
    _cleanup_run_pill.visible = False

    def _on_dry_run(_e) -> None:
        _cleanup_dryrun_pill.ink = False
        _cleanup_dryrun_pill.content.color = "#4A5568"
        _cleanup_result.value = "Scanning…"
        _cleanup_result.color = _TEXT_SECONDARY
        _cleanup_run_pill.visible = False
        page.update()

        def _do() -> None:
            result = _ops.cleanup_attachments(dry_run=True)
            if result["files"] == 0:
                _cleanup_result.value = "Nothing to clean up."
                _cleanup_run_pill.visible = False
            else:
                _cleanup_result.value = (
                    f"{result['files']} file(s) \u00b7 {result['size_mb']} MB "
                    f"across {result['eligible_cards']} done card(s) >90d"
                )
                _cleanup_run_pill.visible = True
            _cleanup_dryrun_pill.ink = True
            _cleanup_dryrun_pill.content.color = _TEXT_SECONDARY
            page.update()

        threading.Thread(target=_do, daemon=True).start()

    def _on_run_cleanup(_e) -> None:
        _cleanup_run_pill.ink = False
        _cleanup_run_pill.content.color = "#7A3030"
        _cleanup_result.value = "Cleaning up…"
        _cleanup_result.color = _TEXT_SECONDARY
        page.update()

        def _do() -> None:
            result = _ops.cleanup_attachments(dry_run=False)
            _cleanup_run_pill.visible = False
            _cleanup_run_pill.ink = True
            _cleanup_run_pill.content.color = "#F85149"
            _cleanup_result.value = (
                f"✓ Freed {result['size_mb']} MB \u00b7 {result['files']} file(s) removed"
            )
            _cleanup_result.color = "#56D364"
            new_mb = _ops.get_attachments_total_size_bytes() / (1024 * 1024)
            _storage_label.value = f"{new_mb:.1f} MB / {_att_max_mb} MB used"
            _storage_bar.value = min(new_mb / _att_max_mb, 1.0) if _att_max_mb else 0
            _storage_bar.color = _storage_bar_color(new_mb)
            page.update()

        threading.Thread(target=_do, daemon=True).start()

    _cleanup_dryrun_pill.on_click = _on_dry_run
    _cleanup_run_pill.on_click   = _on_run_cleanup

    storage_section = ft.Column(
        [
            ft.Row(
                [
                    _storage_label,
                    ft.Container(expand=True),
                    _cleanup_dryrun_pill,
                    _cleanup_run_pill,
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Container(content=_storage_bar, padding=ft.Padding(0, 2, 0, 0)),
            _cleanup_result,
        ],
        spacing=4,
        tight=True,
    )

    # ------------------------------------------------------------------ Dialog
    def _close(_e) -> None:
        dlg.open = False
        page.update()

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row(
            [
                ft.Icon(ft.Icons.SETTINGS_OUTLINED, size=18, color=_TEXT_PRIMARY),
                ft.Text("Settings", size=16, weight=ft.FontWeight.W_600),
            ],
            spacing=8,
        ),
        content=ft.Container(
            content=ft.Column(
                [
                    ft.Text("Date & Time", size=11, color=_TEXT_SECONDARY, weight=ft.FontWeight.W_600),
                    tz_row,
                    ft.Divider(height=16, color="#30363D"),
                    ft.Text("AI", size=11, color=_TEXT_SECONDARY, weight=ft.FontWeight.W_600),
                    effort_row,
                    tokens_row,
                    steps_row,
                    stream_row,
                    ft.Divider(height=16, color="#30363D"),
                    ft.Text("Storage", size=11, color=_TEXT_SECONDARY, weight=ft.FontWeight.W_600),
                    storage_section,
                ],
                spacing=10,
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
            width=400,
            height=480,
            padding=ft.Padding(0, 8, 0, 0),
        ),
        actions=[ft.TextButton("Close", on_click=_close)],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(dlg)
    dlg.open = True
    page.update()
