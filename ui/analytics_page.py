from __future__ import annotations

import base64
import datetime as dt
import io

import matplotlib
matplotlib.use("Agg")  # non-interactive backend — must be before pyplot import
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import logging
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

import flet as ft
import flet_charts as fch

from board import settings
from board.storage import sqlite_cache
from ui import theme

# ── Colour palette ────────────────────────────────────────────────────────────
_BG_CARD = "#1C2128"
_BG_SECTION = "#161B22"
_BORDER = "#30363D"
_TEXT_PRIMARY = "#E6EDF3"
_TEXT_SECONDARY = "#8B949E"
_TEXT_LABEL = "#C9D1D9"

_COLOR_STATUS = theme.STATUS_COLORS
_COLOR_PRIORITY = theme.PRIORITY_COLORS
_COLOR_TAG = "#58A6FF"

# Trailing window used for the "Recently Updated" / "Recently Completed" count
# badges (the lists themselves show only the 5 most recent regardless of age).
_RECENT_WINDOW_DAYS = 7

# Sentinel for "never refreshed yet". Distinct from any real epic value
# (including None, the valid "All" selection) so the first select_epic()
# always renders instead of being skipped as a no-op.
_NEVER_REFRESHED = object()


class AnalyticsPage:
    def __init__(self, page: ft.Page, on_open_card=None, on_epic_change=None):
        self._page = page
        self._on_open_card = on_open_card
        self._on_epic_change_cb = on_epic_change
        self._view: ft.Column | None = None
        self._heatmap_year: int = dt.date.today().year
        self._selected_epic: str | None = None  # None = All
        self._last_refreshed_epic: object = _NEVER_REFRESHED  # Track last refresh to skip redundant nav refreshes
        self._lazy_chart_targets: dict[str, ft.Container] = {}  # Track chart containers for lazy rendering

    # ── Timezone helpers ──────────────────────────────────────────────────────

    def _local_offset(self) -> dt.timezone:
        """Return the local UTC offset as a timezone object.

        Uses datetime.now(utc).astimezone() which reads the OS timezone on all
        platforms (Windows registry, /etc/localtime on Linux/macOS). No
        third-party library required.
        """
        return dt.datetime.now(dt.timezone.utc).astimezone().tzinfo  # type: ignore[return-value]

    def _fmt_date(self, iso_str: str) -> str:
        """Format a stored UTC ISO-8601 string to a YYYY-MM-DD display date.

        Returns the local date when _use_local_tz is True, UTC date otherwise.
        Gracefully handles naive strings (treated as UTC).
        """
        if not iso_str:
            return ""
        try:
            ts = dt.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=dt.timezone.utc)
            if settings.use_local_tz():
                ts = ts.astimezone(self._local_offset())
            return ts.date().isoformat()
        except ValueError:
            return iso_str[:10]

    def _heatmap_data_for_year(self, year: int) -> dict[str, int]:
        """Re-bucket raw UTC timestamps into per-local-day counts for *year*.

        When UTC mode is on, falls back to the fast pre-aggregated SQL query.
        """
        from board.storage import sqlite_cache
        epic = self._selected_epic
        if not settings.use_local_tz():
            return sqlite_cache.transition_activity_heatmap(year, epic=epic)
        # Local-tz bucketing: fetch raw timestamps around the year boundary
        # and convert each one to the local date.
        raw = sqlite_cache.transition_activity_heatmap_timestamps(year, epic=epic)
        tz = self._local_offset()
        counts: dict[str, int] = {}
        for iso in raw:
            try:
                ts = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=dt.timezone.utc)
                local_date = ts.astimezone(tz).date()
                if local_date.year == year:
                    key = local_date.isoformat()
                    counts[key] = counts.get(key, 0) + 1
            except ValueError:
                pass
        return counts

    def _today(self) -> dt.date:
        """Return today's date in the currently selected timezone."""
        if settings.use_local_tz():
            return dt.datetime.now(self._local_offset()).date()
        return dt.datetime.now(dt.timezone.utc).date()

    def _on_epic_change(self, e) -> None:
        val = e.control.value
        self._selected_epic = None if val == "" else val
        self._last_refreshed_epic = self._selected_epic
        self.refresh()
        if self._on_epic_change_cb is not None:
            self._on_epic_change_cb(self._selected_epic)

    def select_epic(self, epic: str | None) -> None:
        """Programmatically select an epic (None = All) and refresh.

        Skips the (expensive) re-render only when the epic is unchanged since
        the last refresh — this guard belongs here, on the navigation entry
        point, NOT in refresh(), so that data/settings changes always re-render.
        """
        if epic == self._last_refreshed_epic:
            return
        self._last_refreshed_epic = epic
        self._selected_epic = epic
        self.refresh()

    def _render_chart_async(self, container: ft.Container | None, chart_func, *args) -> None:
        """Render a chart asynchronously and update its container."""
        if container is None:
            return
        
        import threading
        def _do_render():
            try:
                content = chart_func(*args)
                container.content = content
                self._page.update()
            except Exception as e:
                print(f"Chart render error: {e}")
        
        threading.Thread(target=_do_render, daemon=True).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def build(self) -> ft.Column:
        body = self._build_body()
        self._view = ft.Column(
            [
                self._build_header(),
                ft.Container(
                    content=body,
                    padding=ft.Padding(16, 0, 16, 16),
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )
        # build() already rendered for the current epic; record it so the first
        # navigation to this page is a no-op instead of a redundant rebuild.
        self._last_refreshed_epic = self._selected_epic
        return self._view

    def _build_header(self) -> ft.Container:
        from board.epics import list_epics
        active_epics = list_epics(include_archived=False)
        epic_options = [
            ft.dropdown.Option(key="", text="All"),
            *[ft.dropdown.Option(key=e, text=e) for e in sorted(active_epics)],
        ]
        epic_dropdown = theme.epic_dropdown(
            options=epic_options,
            value=self._selected_epic if self._selected_epic is not None else "",
            on_select=self._on_epic_change,
        )
        return ft.Container(
            content=ft.Row(
                [
                    ft.Text("Epic:", size=12, color="#8B949E"),
                    epic_dropdown,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(16, 12, 16, 8),
        )

    def refresh(self) -> None:
        if self._view is None:
            return

        self._last_refreshed_epic = self._selected_epic
        self._lazy_chart_targets.clear()  # Clear old placeholders
        
        import threading
        def _do_refresh():
            header = self._build_header()
            body = self._build_body()
            self._view.controls[0] = header  # type: ignore[index]
            self._view.controls[1].content = body  # type: ignore[index]
            self._page.update()
        
        threading.Thread(target=_do_refresh, daemon=True).start()

    # ── Body ──────────────────────────────────────────────────────────────────

    def _build_body(self) -> ft.Column:
        # Fetch all data up-front, filtered by selected epic
        epic = self._selected_epic
        by_lane = sqlite_cache.count_cards_by_lane(epic=epic)
        by_priority = sqlite_cache.count_cards_by_priority(epic=epic)
        archived = sqlite_cache.count_archived(epic=epic)
        archived_28d = sqlite_cache.count_archived_within(28, epic=epic)
        recent_created = sqlite_cache.recently_created_cards(10, epic=epic)
        recent_updated = sqlite_cache.recently_updated_cards(5, epic=epic)
        recent_completed = sqlite_cache.recently_completed_cards(5, epic=epic)
        updated_7d = sqlite_cache.count_updated_within(_RECENT_WINDOW_DAYS, epic=epic)
        completed_7d = sqlite_cache.count_completed_within(_RECENT_WINDOW_DAYS, epic=epic)
        completed_28d = sqlite_cache.count_completed_within(28, epic=epic)
        started_28d = sqlite_cache.count_started_within(28, epic=epic)
        blockers = sqlite_cache.blocker_cards(5, epic=epic)
        blocker_count = sqlite_cache.count_blocker_cards(epic=epic)
        deadlines = sqlite_cache.due_soon_cards(5, epic=epic)
        deadline_count = sqlite_cache.count_with_due_date(epic=epic)
        top_tags = sqlite_cache.count_cards_by_tag(20, epic=epic)
        due_soon = sqlite_cache.count_due_within(settings.DEADLINE_SOON_DAYS, epic=epic)
        
        # Defer expensive queries: don't fetch CFD and age_series yet
        # They'll be fetched on background threads during lazy rendering

        flow_counts_7 = self._flow_counts(7, epic=epic)
        flow_counts_28 = self._flow_counts(28, epic=epic)
        flow_weekly = sqlite_cache.transition_flow_weekly(12, epic=epic)
        age_stats = sqlite_cache.open_card_age_stats(epic=epic)
        days_since_move = sqlite_cache.days_since_last_transition(epic=epic)
        days_since_started = sqlite_cache.days_since_last_started(epic=epic)
        cycle_time_7 = sqlite_cache.avg_cycle_time_days(7, epic=epic)
        cycle_time_28 = sqlite_cache.avg_cycle_time_days(28, epic=epic)

        total = sum(by_lane.values())
        done = by_lane.get("done", 0)
        in_progress = by_lane.get("in_progress", 0)
        open_count = total - done

        pull_rate_7 = self._ratio(flow_counts_7["started"], flow_counts_7["created"])
        pull_rate_28 = self._ratio(flow_counts_28["started"], flow_counts_28["created"])
        throughput_7 = self._ratio(flow_counts_7["completed"], flow_counts_7["started"])
        throughput_28 = self._ratio(flow_counts_28["completed"], flow_counts_28["started"])

        max_age = age_stats["max_age"]
        max_age_band = settings.band(max_age, settings.HEALTH_MAX_AGE_WATCH_DAYS, settings.HEALTH_MAX_AGE_BAD_DAYS)
        max_age_color = settings.band_color(max_age_band)
        move_band = settings.band(
            days_since_move if open_count > 0 else None,
            settings.HEALTH_LAST_MOVE_WATCH_DAYS,
            settings.HEALTH_LAST_MOVE_BAD_DAYS,
        )
        move_color = settings.band_color(move_band)
        started_band = settings.band(
            days_since_started,
            settings.HEALTH_LAST_STARTED_WATCH_DAYS,
            settings.HEALTH_LAST_STARTED_BAD_DAYS,
        )
        started_color = settings.band_color(started_band)
        pull_rate_band = settings.band_inverted(
            pull_rate_7,
            settings.HEALTH_PULL_RATE_WATCH,
            settings.HEALTH_PULL_RATE_BAD,
        )
        throughput_band = settings.band_inverted(
            throughput_7,
            settings.HEALTH_THROUGHPUT_WATCH,
            settings.HEALTH_THROUGHPUT_BAD,
        )
        cycle_time_band = settings.band(
            cycle_time_7,
            settings.HEALTH_CYCLE_TIME_WATCH_DAYS,
            settings.HEALTH_CYCLE_TIME_BAD_DAYS,
        )
        blockers_band = settings.band(blocker_count, settings.HEALTH_BLOCKER_WATCH, settings.HEALTH_BLOCKER_BAD)
        due_soon_band = settings.band(due_soon, settings.HEALTH_DUE_SOON_WATCH, settings.HEALTH_DUE_SOON_BAD)

        body = ft.Column(
            [
                # ── Row 1: Stat cards ─────────────────────────────────────────
                ft.Row(
                    [
                        self._stat_card(
                            ft.Icons.CHECK_CIRCLE_OUTLINE,
                            str(completed_7d),
                            "Completed 7d",
                            "#56D364",
                            sublabel="tasks done",
                        ),
                        self._stat_card(
                            ft.Icons.CHECK_CIRCLE_OUTLINE,
                            str(completed_28d),
                            "Completed 28d",
                            "#56D364",
                            sublabel="tasks done",
                        ),
                        self._stat_card(
                            ft.Icons.AUTORENEW,
                            str(in_progress),
                            "WIP now",
                            "#39C5CF",
                            sublabel="in progress",
                        ),
                        self._stat_card(
                            ft.Icons.TASK_ALT,
                            self._fmt_ratio(throughput_7),
                            "Throughput 7d",
                            "#39C5CF",
                            sublabel="completed \u00f7 started",
                            band=throughput_band,
                            tooltip="completed \u00f7 started over the last 7 days. ~1.0 means WIP is stable; <1.0 means in-progress work is piling up.",
                        ),
                        self._stat_card(
                            ft.Icons.BLOCK,
                            str(blocker_count),
                            "Blockers",
                            "#F85149" if blocker_count else "#8B949E",
                            sublabel="blocking tasks",
                            band=blockers_band,
                        ),
                        self._stat_card(
                            ft.Icons.SCHEDULE,
                            str(due_soon),
                            f"Due \u2264 {settings.DEADLINE_SOON_DAYS}d",
                            "#F0A742" if due_soon else "#8B949E",
                            sublabel="upcoming deadlines",
                            band=due_soon_band,
                        ),
                    ],
                    spacing=12,
                ),

                # ── Row 1b: Flow & freshness stat cards ───────────────────────
                ft.Row(
                    [
                        self._stat_card(
                            ft.Icons.LOGIN,
                            self._fmt_ratio(pull_rate_7),
                            "Pull rate (7d)",
                            "#58A6FF",
                            sublabel=f"Last 28d: {self._fmt_ratio(pull_rate_28)}",
                            band=pull_rate_band,
                            tooltip="started ÷ created over the last 7 days. \u22651.0 means the backlog is draining faster than it grows.",
                        ),
                        self._stat_card(
                            ft.Icons.TASK_ALT,
                            self._fmt_ratio(throughput_7),
                            "Throughput (7d)",
                            "#39C5CF",
                            sublabel=f"Last 28d: {self._fmt_ratio(throughput_28)}",
                            band=throughput_band,
                            tooltip="completed ÷ started over the last 7 days. ~1.0 means WIP is stable; <1.0 means in-progress work is piling up.",
                        ),
                        self._stat_card(
                            ft.Icons.TIMELAPSE,
                            f"{cycle_time_7:.1f}d" if cycle_time_7 is not None else "\u2014",
                            "Avg cycle time (7d)",
                            "#A371F7",
                            sublabel=f"Last 28d: {f'{cycle_time_28:.1f}d' if cycle_time_28 is not None else chr(0x2014)}",
                            band=cycle_time_band,
                            tooltip="Average days from 'in progress' to 'done' for cards completed in the last 7 days. Target: 1\u20133d (watch > 3d, bad > 5d).",
                        ),
                        self._stat_card(
                            ft.Icons.UPDATE,
                            f"{days_since_move}d" if days_since_move is not None else "\u2014",
                            "Last activity",
                            move_color,
                            sublabel="days since last move",
                            band=move_band,
                        ),
                        self._stat_card(
                            ft.Icons.PLAY_ARROW,
                            f"{days_since_started}d" if days_since_started is not None else "\u2014",
                            "Last started",
                            started_color,
                            sublabel="days since last started",
                            band=started_band,
                        ),
                    ],
                    spacing=12,
                ),

                # ── Row 2: Overall progress ───────────────────────────────────
                ft.Row(
                    [
                        self._section_card(
                            "Overall Progress",
                            self._progress_section(done, total),
                            expand=True,
                        ),
                    ],
                    spacing=12,
                ),

                # ── Row 2b: Flow ratios (last 28 days) ────────────────────────
                ft.Row(
                    [
                        self._section_card(
                            "Flow Ratios (last 28 days)",
                            self._flow_ratios_section(flow_counts_28),
                            expand=True,
                        ),
                    ],
                    spacing=12,
                ),

                # ── Row 2c: Weekly flow-ratio trends (lazy-loaded) ────────────
                ft.Row(
                    [
                        self._section_card(
                            "Pull Rate by Week (started \u00f7 created)",
                            self._create_lazy_placeholder("flow_ratio_start"),
                            expand=True,
                        ),
                        self._section_card(
                            "Throughput by Week (completed \u00f7 started)",
                            self._create_lazy_placeholder("flow_ratio_finish"),
                            expand=True,
                        ),
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),

                # ── Row 3: Status + Priority distributions ────────────────────
                ft.Row(
                    [
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text("Status Distribution", size=13, weight=ft.FontWeight.W_600, color=_TEXT_LABEL),
                                    self._dist_rows(by_lane, _COLOR_STATUS, total or 1),
                                ],
                                spacing=10,
                            ),
                            expand=True,
                            bgcolor=_BG_CARD,
                            border=ft.Border(ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER)),
                            border_radius=10,
                            padding=ft.Padding(14, 12, 14, 14),
                            height=200,
                        ),
                        ft.Container(
                            content=ft.Column(
                                [
                                    ft.Text("Priority Distribution", size=13, weight=ft.FontWeight.W_600, color=_TEXT_LABEL),
                                    self._dist_rows(by_priority, _COLOR_PRIORITY, total or 1),
                                ],
                                spacing=10,
                            ),
                            expand=True,
                            bgcolor=_BG_CARD,
                            border=ft.Border(ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER)),
                            border_radius=10,
                            padding=ft.Padding(14, 12, 14, 14),
                            height=200,
                        ),
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),

                # ── Row 4: Tags distribution (pie chart, lazy-loaded) ────────────────────────
                ft.Row(
                    [self._section_card(
                        "Top Tags",
                        self._create_lazy_placeholder("tag_pie"),
                        expand=True,
                        height=200,
                    )],
                    expand=True,
                ),

                # ── Row 5: Recently Updated + Completed + Blockers + Deadlines ──
                ft.Row(
                    [
                        ft.Container(
                            content=self._section_card(
                                "Recently Updated",
                                self._recent_list(recent_updated, date_field="updated_at"),
                                count=updated_7d,
                                count_color=_COLOR_TAG,
                                count_tooltip=f"{updated_7d} card{'s' if updated_7d != 1 else ''} updated in the last {_RECENT_WINDOW_DAYS} days (list shows the 5 most recent)",
                                expand=True,
                            ),
                            expand=True,
                            height=380,
                        ),
                        ft.Container(
                            content=self._section_card(
                                "Recently Completed",
                                self._recent_list(recent_completed, date_field="updated_at"),
                                count=completed_7d,
                                count_color="#56D364",
                                count_tooltip=f"{completed_7d} card{'s' if completed_7d != 1 else ''} completed in the last {_RECENT_WINDOW_DAYS} days (list shows the 5 most recent)",
                                expand=True,
                            ),
                            expand=True,
                            height=380,
                        ),
                        ft.Container(
                            content=self._section_card(
                                "Blockers",
                                self._blocker_list(blockers),
                                count=blocker_count,
                                expand=True,
                            ),
                            expand=True,
                            height=380,
                        ),
                        ft.Container(
                            content=self._section_card(
                                "Deadlines",
                                self._deadline_list(deadlines),
                                count=deadline_count,
                                expand=True,
                            ),
                            expand=True,
                            height=380,
                        ),
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),

                # ── Row 6: Cumulative Flow Diagram (lazy-loaded) ────────────────────────────
                ft.Row(
                    [self._section_card(
                        "Cumulative Flow Diagram (last 90 days)",
                        self._create_lazy_placeholder("cfd"),
                        expand=True,
                    )],
                    expand=True,
                ),

                # ── Row 6b: Task Age Trend (lazy-loaded) ─────────────────────────────
                ft.Row(
                    [self._section_card(
                        "Open Task Age Over Time (last 90 days)",
                        self._create_lazy_placeholder("task_age"),
                        expand=True,
                    )],
                    expand=True,
                ),

                # ── Row 7: Activity Heatmap ───────────────────────────────────
                ft.Row(
                    [self._activity_heatmap_section()],
                    expand=True,
                ),

                ft.Container(height=72),
            ],
            spacing=12,
        )
        
        # Schedule lazy loading of expensive charts on background threads
        self._schedule_lazy_charts(epic, flow_weekly, top_tags)
        
        return body
    
    def _create_lazy_placeholder(self, chart_id: str) -> ft.Container:
        """Create a placeholder container for lazy-loaded charts."""
        container = ft.Container(
            content=ft.Text("Loading...", color=_TEXT_SECONDARY),
            expand=True,
        )
        self._lazy_chart_targets[chart_id] = container
        return container
    
    def _schedule_lazy_charts(self, epic: str | None, flow_weekly: list[dict], top_tags: list[tuple[str, int]]) -> None:
        """Schedule lazy rendering of expensive charts."""
        self._render_chart_async(
            self._lazy_chart_targets.get("flow_ratio_start"),
            lambda: self._flow_ratio_weekly_chart(flow_weekly, "started", "created", "Pull rate", "#58A6FF"),
        )
        self._render_chart_async(
            self._lazy_chart_targets.get("flow_ratio_finish"),
            lambda: self._flow_ratio_weekly_chart(flow_weekly, "completed", "started", "Throughput", "#39C5CF"),
        )
        self._render_chart_async(
            self._lazy_chart_targets.get("tag_pie"),
            lambda: self._tag_pie_chart(top_tags),
        )
        self._render_chart_async(
            self._lazy_chart_targets.get("cfd"),
            lambda: self._cfd_chart(sqlite_cache.cfd_data(90, epic=epic)),
        )
        self._render_chart_async(
            self._lazy_chart_targets.get("task_age"),
            lambda: self._task_age_chart(sqlite_cache.task_age_timeseries(90, epic=epic)),
        )

    # ── Stat card ─────────────────────────────────────────────────────────────

    def _stat_card(self, icon: str | ft.IconData, value: str, label: str, color: str, sublabel: str | None = None, band: str | None = None, tooltip: str | None = None) -> ft.Container:
        _BAND_VALUE = {"good": "#56D364", "watch": "#F0A742", "bad": "#F85149"}
        value_color = _BAND_VALUE.get(band or "", _TEXT_PRIMARY)
        label_row: ft.Control = ft.Row(
            [
                ft.Icon(icon, size=12, color=_TEXT_SECONDARY),  # type: ignore[arg-type]
                ft.Text(label.upper(), size=10, weight=ft.FontWeight.W_600, color=_TEXT_SECONDARY),
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )
        items: list[ft.Control] = [
            label_row,
            ft.Text(value, size=28, weight=ft.FontWeight.BOLD, color=value_color, font_family="monospace"),
        ]
        if sublabel is not None:
            items.append(ft.Text(sublabel, size=11, color=_TEXT_SECONDARY))
        _BAND_BORDER = {"watch": "#F0A742", "bad": "#F85149"}
        border_color = _BAND_BORDER.get(band or "", _BORDER)
        bg = (
            ft.Colors.with_opacity(0.07, border_color)
            if band in ("watch", "bad")
            else _BG_CARD
        )
        return ft.Container(
            content=ft.Column(
                items,
                spacing=4,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
            expand=True,
            bgcolor=bg,
            border=ft.Border(ft.BorderSide(1, border_color), ft.BorderSide(1, border_color), ft.BorderSide(1, border_color), ft.BorderSide(1, border_color)),
            border_radius=8,
            padding=ft.Padding(14, 14, 14, 14),
            tooltip=ft.Tooltip(message=tooltip, prefer_below=True, wait_duration=300) if tooltip else None,
        )

    # ── Section card wrapper ──────────────────────────────────────────────────

    def _section_card(self, title: str, content: ft.Control, expand: bool = False, count: int | None = None, height: int | None = None, count_tooltip: str | None = None, count_color: str | None = None) -> ft.Container:
        header: ft.Control = ft.Text(title, size=13, weight=ft.FontWeight.W_600, color=_TEXT_LABEL)
        if count is not None:
            badge_color = count_color if count_color is not None else ("#F85149" if count else _TEXT_SECONDARY)
            header = ft.Row(
                [
                    ft.Text(title, size=13, weight=ft.FontWeight.W_600, color=_TEXT_LABEL),
                    ft.Container(
                        content=ft.Text(str(count), size=11, color=badge_color, weight=ft.FontWeight.W_600),
                        bgcolor=ft.Colors.with_opacity(0.15, badge_color),
                        border_radius=10,
                        padding=ft.Padding(8, 2, 8, 2),
                        tooltip=ft.Tooltip(message=count_tooltip, prefer_below=True, wait_duration=300) if count_tooltip else None,
                    ),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        return ft.Container(
            content=ft.Column(
                [
                    header,
                    content,
                ],
                spacing=10,
            ),            bgcolor=_BG_CARD,
            border=ft.Border(ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER), ft.BorderSide(1, _BORDER)),
            border_radius=10,
            padding=ft.Padding(14, 12, 14, 14),
            expand=expand,
            height=height,
        )

    # ── Progress section ──────────────────────────────────────────────────────

    def _progress_section(self, done: int, total: int) -> ft.Control:
        pct = done / total if total else 0
        remaining = total - done
        return ft.Column(
            [
                ft.ProgressBar(
                    value=pct,
                    bgcolor="#21262D",
                    color="#56D364",
                    height=10,
                    border_radius=6,
                ),
                ft.Row(
                    [
                        ft.Text(f"{done} completed", size=11, color=_TEXT_SECONDARY),
                        ft.Text(f"{remaining} remaining", size=11, color=_TEXT_SECONDARY),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ],
            spacing=6,
        )

    # ── Flow ratios ───────────────────────────────────────────────────────────

    def _flow_counts(self, days: int, epic: str | None = None) -> dict[str, int]:
        """Flow-event counts over the trailing ``days`` window (UTC)."""
        now = dt.datetime.now(dt.timezone.utc)
        since = now - dt.timedelta(days=days)
        return sqlite_cache.transition_flow_counts(
            since.isoformat(), now.isoformat(), epic=epic
        )

    @staticmethod
    def _ratio(numer: int, denom: int) -> float | None:
        """Safe ratio: None when the denominator is zero (avoids div-by-zero/∞)."""
        return (numer / denom) if denom else None

    @staticmethod
    def _fmt_ratio(ratio: float | None) -> str:
        return f"{ratio:.2f}" if ratio is not None else "\u2014"

    def _flow_ratio_row(
        self,
        label: str,
        numer: int,
        denom: int,
        numer_label: str,
        denom_label: str,
        healthy_at_one: bool,
    ) -> ft.Control:
        """Render one ratio row. ``healthy_at_one`` picks the colour rule:
        throughput is healthiest near 1.0, pull rate is healthy when >= 1.0.
        """
        if denom == 0:
            value_text = "\u2014"  # em dash: not enough data yet
            color = _TEXT_SECONDARY
        else:
            ratio = numer / denom
            value_text = f"{ratio:.2f}"
            if healthy_at_one:
                color = "#56D364" if 0.8 <= ratio <= 1.25 else "#F0A742"
            else:
                color = "#56D364" if ratio >= 1.0 else "#F0A742"
        return ft.Row(
            [
                ft.Column(
                    [
                        ft.Text(label, size=12, weight=ft.FontWeight.W_600, color=_TEXT_LABEL),
                        ft.Text(
                            f"{numer_label}: {numer}  \u00b7  {denom_label}: {denom}",
                            size=11,
                            color=_TEXT_SECONDARY,
                        ),
                    ],
                    spacing=2,
                    tight=True,
                ),
                ft.Text(value_text, size=20, weight=ft.FontWeight.BOLD, color=color),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _flow_ratios_section(self, counts: dict[str, int]) -> ft.Control:
        created = counts.get("created", 0)
        started = counts.get("started", 0)
        completed = counts.get("completed", 0)
        return ft.Column(
            [
                # Pull rate: are we pulling work out of the backlog as fast as
                # it arrives? >= 1.0 means the backlog is draining.
                self._flow_ratio_row(
                    "Pull rate (started \u00f7 created)",
                    started,
                    created,
                    "started",
                    "created",
                    healthy_at_one=False,
                ),
                ft.Divider(height=1, color=_BORDER),
                # Throughput: are we finishing what we start? ~1.0 means WIP is
                # stable; < 1.0 means in-progress/review is piling up.
                self._flow_ratio_row(
                    "Throughput (completed \u00f7 started)",
                    completed,
                    started,
                    "completed",
                    "started",
                    healthy_at_one=True,
                ),
            ],
            spacing=10,
        )

    # ── Distribution rows ─────────────────────────────────────────────────────

    def _dist_rows(self, data: dict[str, int], colors: dict[str, str], total: int) -> ft.Control:
        if not data:
            return ft.Text("No data", size=12, color=_TEXT_SECONDARY)
        rows = []
        for key, count in sorted(data.items(), key=lambda x: x[1], reverse=True):
            pct = count / total if total else 0
            color = colors.get(key, "#58A6FF")
            label = key.replace("_", " ").title()
            rows.append(
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Container(
                                    content=ft.Text(label, size=11, color=_TEXT_PRIMARY),
                                    expand=True,
                                ),
                                ft.Container(
                                    content=ft.Text(str(count), size=11, weight=ft.FontWeight.W_600, color=_TEXT_PRIMARY),
                                    width=28,
                                    alignment=ft.Alignment(1, 0),
                                ),
                                ft.Container(
                                    content=ft.Text(f"{round(pct * 100)}%", size=10, color=_TEXT_SECONDARY),
                                    width=40,
                                    alignment=ft.Alignment(1, 0),
                                ),
                            ],
                            spacing=0,
                        ),
                        ft.ProgressBar(
                            value=pct,
                            bgcolor="#21262D",
                            color=color,
                            height=6,
                            border_radius=4,
                        ),
                    ],
                    spacing=4,
                )
            )
        return ft.Column(rows, spacing=10)

    # ── Tag treemap (squarify) ────────────────────────────────────────────────

    # ── Tag pie chart ─────────────────────────────────────────────────────────

    def _tag_pie_chart(self, top_tags: list[tuple[str, int]]) -> ft.Control:
        if not top_tags:
            return ft.Text("No tags yet", size=12, color=_TEXT_SECONDARY)

        tags = [t for t, _ in top_tags]
        sizes = [float(c) for _, c in top_tags]

        _PALETTE = [
            "#58a6ff", "#a371f7", "#56d364", "#f0a742",
            "#f85149", "#79c0ff", "#d2a8ff", "#7ee787",
            "#ffa657", "#ff7b72",
        ]
        colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(tags))]

        fig, ax = plt.subplots(figsize=(3.2, 3.2))
        fig.patch.set_facecolor(_BG_CARD)
        ax.set_facecolor(_BG_CARD)

        wedges, _ = ax.pie(
            sizes,
            colors=colors,
            startangle=90,
            wedgeprops={"linewidth": 1.5, "edgecolor": _BG_CARD},
            counterclock=False,
        )
        # Donut hole
        ax.add_patch(mpatches.Circle((0, 0), 0.55, color=_BG_CARD, zorder=10))

        plt.tight_layout(pad=0)
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=_BG_CARD)
        plt.close(fig)
        buf.seek(0)
        pie_b64 = buf.read()

        # Legend with counts
        legend_items = []
        for i, (tag, count) in enumerate(zip(tags, [int(s) for s in sizes])):
            color = colors[i]
            legend_items.append(
                ft.Row(
                    [
                        ft.Text(str(count), size=11, weight=ft.FontWeight.W_600, color=_TEXT_SECONDARY),
                        ft.Container(width=10, height=10, bgcolor=color, border_radius=2),
                        ft.Container(
                            content=ft.Text(
                                tag,
                                size=11,
                                color=_TEXT_PRIMARY,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            expand=True,
                        ),
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )

        n = len(legend_items)
        col_size = (n + 3) // 4  # ceil(n/4)
        cols = [legend_items[i * col_size:(i + 1) * col_size] for i in range(4)]
        legend = ft.Row(
            [ft.Column(c, spacing=6, expand=True) for c in cols if c],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

        return ft.Row(
            [
                ft.Image(src=pie_b64, width=160, height=160, fit=ft.BoxFit.CONTAIN),
                ft.Container(content=legend, expand=True, padding=ft.Padding(8, 0, 0, 0)),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # ── Recent card list ──────────────────────────────────────────────────────

    def _recent_list(self, cards: list[dict], date_field: str) -> ft.Control:
        if not cards:
            return ft.Text("None yet", size=12, color=_TEXT_SECONDARY)
        items = []
        for c in cards:
            task_num = f"#{c['number']}" if c.get("number") else c["id"][:8]
            date_raw = self._fmt_date(c.get(date_field) or "")
            card_id = c["id"]
            priority = (c.get("priority") or "medium").lower()
            lane_key = (c.get("lane") or "").lower()
            lane_label = lane_key.replace("_", " ").title()
            lane_color = _COLOR_STATUS.get(lane_key, "#8B949E")
            epic = c.get("epic") or ""

            # Bottom detail row
            details: list[ft.Control] = [
                ft.Text(f"{task_num} • {date_raw}", size=10, color=_TEXT_SECONDARY),
                ft.Container(width=8, height=8, bgcolor=_COLOR_PRIORITY.get(priority, "#58A6FF"), border_radius=4),
                ft.Text(priority.title(), size=10, color=_TEXT_SECONDARY),
            ]
            if lane_label:
                details.append(
                    ft.Container(
                        content=ft.Text(lane_label, size=10, color=lane_color, weight=ft.FontWeight.W_500),
                        bgcolor=ft.Colors.with_opacity(0.15, lane_color),
                        border_radius=10,
                        padding=ft.Padding(7, 2, 7, 2),
                    )
                )
            if epic:
                details.append(
                    ft.Text(epic, size=10, color=_COLOR_TAG, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                )

            items.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                c["title"],
                                size=12,
                                color=_TEXT_PRIMARY,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Row(
                                details,
                                spacing=5,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                run_spacing=2,
                            ),
                        ],
                        spacing=4,
                        tight=True,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    bgcolor=_BG_SECTION,
                    border_radius=6,
                    padding=ft.Padding(10, 8, 10, 8),
                    height=58,
                    on_click=(lambda e, cid=card_id: self._on_open_card(cid)) if self._on_open_card else None,  # type: ignore[misc]
                    ink=bool(self._on_open_card),
                )
            )
        return ft.Column(items, spacing=6, scroll=ft.ScrollMode.AUTO, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    def _blocker_list(self, cards: list[dict]) -> ft.Control:
        if not cards:
            return ft.Text("No blockers", size=12, color=_TEXT_SECONDARY)
        items = []
        for c in cards:
            task_num = f"#{c['number']}" if c.get("number") else c["id"][:8]
            date_raw = self._fmt_date(c.get("updated_at") or "")
            card_id = c["id"]
            priority = (c.get("priority") or "medium").lower()
            lane_key = (c.get("lane") or "").lower()
            lane_label = lane_key.replace("_", " ").title()
            lane_color = _COLOR_STATUS.get(lane_key, "#8B949E")
            epic = c.get("epic") or ""
            block_count = int(c.get("block_count") or 0)

            details: list[ft.Control] = [
                ft.Text(f"{task_num} • {date_raw}", size=10, color=_TEXT_SECONDARY),
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.BLOCK, size=10, color="#F85149"),
                            ft.Text(
                                f"Blocks {block_count}",
                                size=10,
                                color="#F85149",
                                weight=ft.FontWeight.W_500,
                            ),
                        ],
                        spacing=3,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=ft.Colors.with_opacity(0.15, "#F85149"),
                    border_radius=10,
                    padding=ft.Padding(7, 2, 7, 2),
                ),
            ]
            if lane_label:
                details.append(
                    ft.Container(
                        content=ft.Text(lane_label, size=10, color=lane_color, weight=ft.FontWeight.W_500),
                        bgcolor=ft.Colors.with_opacity(0.15, lane_color),
                        border_radius=10,
                        padding=ft.Padding(7, 2, 7, 2),
                    )
                )
            if epic:
                details.append(
                    ft.Text(epic, size=10, color=_COLOR_TAG, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                )

            items.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                c["title"],
                                size=12,
                                color=_TEXT_PRIMARY,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Row(
                                details,
                                spacing=5,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                run_spacing=2,
                            ),
                        ],
                        spacing=4,
                        tight=True,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    bgcolor=_BG_SECTION,
                    border_radius=6,
                    padding=ft.Padding(10, 8, 10, 8),
                    height=58,
                    on_click=(lambda e, cid=card_id: self._on_open_card(cid)) if self._on_open_card else None,  # type: ignore[misc]
                    ink=bool(self._on_open_card),
                )
            )
        return ft.Column(items, spacing=6, scroll=ft.ScrollMode.AUTO, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    def _deadline_list(self, cards: list[dict]) -> ft.Control:
        if not cards:
            return ft.Text("No upcoming deadlines", size=12, color=_TEXT_SECONDARY)
        today = self._today()
        items = []
        for c in cards:
            task_num = f"#{c['number']}" if c.get("number") else c["id"][:8]
            due_raw = c.get("due_date") or ""
            card_id = c["id"]
            priority = (c.get("priority") or "medium").lower()
            lane_key = (c.get("lane") or "").lower()
            lane_label = lane_key.replace("_", " ").title()
            lane_color = _COLOR_STATUS.get(lane_key, "#8B949E")
            epic = c.get("epic") or ""

            # Compute days-to-due and overdue state
            due_color = "#F0A742"
            due_label = due_raw
            try:
                due_date = dt.date.fromisoformat(due_raw[:10])
                delta = (due_date - today).days
                if delta < 0:
                    due_color = "#F85149"
                    due_label = f"{due_raw} (overdue {abs(delta)}d)"
                elif delta == 0:
                    due_color = "#F85149"
                    due_label = f"{due_raw} (today)"
                else:
                    due_label = f"{due_raw} (in {delta}d)"
            except ValueError:
                pass

            details: list[ft.Control] = [
                ft.Text(task_num, size=10, color=_TEXT_SECONDARY),
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.SCHEDULE, size=10, color=due_color),
                            ft.Text(
                                due_label,
                                size=10,
                                color=due_color,
                                weight=ft.FontWeight.W_500,
                            ),
                        ],
                        spacing=3,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=ft.Colors.with_opacity(0.15, due_color),
                    border_radius=10,
                    padding=ft.Padding(7, 2, 7, 2),
                ),
            ]
            if lane_label:
                details.append(
                    ft.Container(
                        content=ft.Text(lane_label, size=10, color=lane_color, weight=ft.FontWeight.W_500),
                        bgcolor=ft.Colors.with_opacity(0.15, lane_color),
                        border_radius=10,
                        padding=ft.Padding(7, 2, 7, 2),
                    )
                )
            if epic:
                details.append(
                    ft.Text(epic, size=10, color=_COLOR_TAG, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                )

            items.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                c["title"],
                                size=12,
                                color=_TEXT_PRIMARY,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS,
                            ),
                            ft.Row(
                                details,
                                spacing=5,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                run_spacing=2,
                            ),
                        ],
                        spacing=4,
                        tight=True,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                    bgcolor=_BG_SECTION,
                    border_radius=6,
                    padding=ft.Padding(10, 8, 10, 8),
                    height=58,
                    on_click=(lambda e, cid=card_id: self._on_open_card(cid)) if self._on_open_card else None,  # type: ignore[misc]
                    ink=bool(self._on_open_card),
                )
            )
        return ft.Column(items, spacing=6, scroll=ft.ScrollMode.AUTO, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    # ── Project health footer ─────────────────────────────────────────────────

    def _health_inline(self, health: dict) -> ft.Control:
        """Compact health badge; hover for per-signal details."""
        status = health["label"]
        color = health["color"]
        signals = health["signals"]

        non_good = [s for s in signals if s["band"] != "good"]
        if non_good:
            tip_lines = [
                f"{'⚠' if s['band'] == 'watch' else '✖'} {s['label']}: {s['message']}"
                for s in non_good
            ]
            tooltip_msg = "\n".join(tip_lines)
        else:
            avg_sig = next((s for s in signals if s["key"] == "avg_age"), None)
            done_sig = next((s for s in signals if s["key"] == "days_since_done"), None)
            parts = []
            if avg_sig and avg_sig.get("value") is not None:
                parts.append(f"Avg card age: {avg_sig['value']:.0f}d")
            if done_sig:
                parts.append(done_sig["message"])
            tooltip_msg = " · ".join(parts) if parts else "All signals healthy"

        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.MONITOR_HEART_OUTLINED, size=14, color=color),
                    ft.Text(status, size=11, color=color),
                ],
                spacing=6,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            tooltip=ft.Tooltip(message=tooltip_msg, prefer_below=True, wait_duration=300),
        )

    # ── Activity Heatmap (GitHub-style tile graph) ────────────────────────────

    def _set_heatmap_year(self, year: int) -> None:
        self._heatmap_year = year
        self.refresh()

    def _activity_heatmap_section(self) -> ft.Container:
        year = self._heatmap_year
        data = self._heatmap_data_for_year(year)
        total_year = sum(data.values())

        current_year = dt.date.today().year
        years = list(range(current_year - 2, current_year + 1))

        year_btns = []
        for y in years:
            selected = y == year
            year_btns.append(
                ft.Container(
                    content=ft.Text(str(y), size=10, color=_TEXT_PRIMARY if selected else _TEXT_SECONDARY),
                    bgcolor="#30363D" if selected else "transparent",
                    border_radius=4,
                    padding=ft.Padding(6, 2, 6, 2),
                    on_click=lambda e, yr=y: self._set_heatmap_year(yr),
                    ink=True,
                )
            )

        header = ft.Row(
            [
                ft.Column(
                    [
                        ft.Text("Activity Heatmap", size=13, weight=ft.FontWeight.W_600, color=_TEXT_LABEL),
                        ft.Text(
                            f"{total_year} card movement{'s' if total_year != 1 else ''} in {year}",
                            size=10,
                            color=_TEXT_SECONDARY,
                        ),
                    ],
                    spacing=2,
                    tight=True,
                ),
                ft.Row(year_btns, spacing=4),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        grid = ft.Row(
            [self._heatmap_grid(data, year)],
            alignment=ft.MainAxisAlignment.CENTER,
        )

        return ft.Container(
            content=ft.Column([header, grid], spacing=10),
            bgcolor=_BG_CARD,
            border=ft.Border(
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER),
                ft.BorderSide(1, _BORDER),
            ),
            border_radius=10,
            padding=ft.Padding(14, 12, 14, 14),
            expand=True,
        )

    def _heatmap_grid(self, data: dict[str, int], year: int) -> ft.Control:
        CELL = 16
        GAP = 3
        TODAY = self._today()

        _HEAT_COLORS = ["#161B22", "#0e4429", "#006d32", "#26a641", "#39d353"]

        def color_for(count: int, future: bool) -> str:
            if future and count == 0:
                return "#0d1117"
            if count == 0:
                return _HEAT_COLORS[0]
            if count == 1:
                return _HEAT_COLORS[1]
            if count <= 3:
                return _HEAT_COLORS[2]
            if count <= 6:
                return _HEAT_COLORS[3]
            return _HEAT_COLORS[4]

        start = dt.date(year, 1, 1)
        end = dt.date(year, 12, 31)

        # Align to the Monday of the week containing Jan 1
        grid_start = start - dt.timedelta(days=start.weekday())

        # Build week columns
        weeks: list[list[dt.date]] = []
        cur = grid_start
        while cur <= end:
            weeks.append([cur + dt.timedelta(days=d) for d in range(7)])
            cur += dt.timedelta(weeks=1)

        # Month label row — show abbreviated month when the 1st of that month appears
        month_cells: list[ft.Control] = []
        seen_months: set[int] = set()
        for week in weeks:
            label = ""
            for day in week:
                if day.year == year and day.day == 1 and day.month not in seen_months:
                    label = day.strftime("%b")
                    seen_months.add(day.month)
                    break
            month_cells.append(
                ft.Container(
                    content=ft.Text(label, size=8, color=_TEXT_SECONDARY) if label else None,
                    width=CELL,
                )
            )

        # 7 day rows (Mon–Sun)
        day_rows: list[ft.Control] = []
        for day_idx in range(7):
            cells: list[ft.Control] = []
            for week in weeks:
                day = week[day_idx]
                if day.year == year:
                    count = data.get(day.isoformat(), 0)
                    future = day > TODAY
                    cells.append(
                        ft.Container(
                            width=CELL,
                            height=CELL,
                            bgcolor=color_for(count, future),
                            border_radius=2,
                            tooltip=f"{day.strftime('%b %d, %Y')}: {count} movement{'s' if count != 1 else ''}",
                        )
                    )
                else:
                    cells.append(ft.Container(width=CELL, height=CELL))
            day_rows.append(ft.Row(cells, spacing=GAP))

        # Legend
        legend = ft.Row(
            [
                ft.Text("Less", size=9, color=_TEXT_SECONDARY),
                *[
                    ft.Container(width=CELL, height=CELL, bgcolor=c, border_radius=2)
                    for c in _HEAT_COLORS
                ],
                ft.Text("More", size=9, color=_TEXT_SECONDARY),
            ],
            spacing=3,
            vertical_alignment=ft.CrossAxisAlignment.CENTER
        )

        return ft.Column(
            [
                ft.Row(month_cells, spacing=GAP),
                *day_rows,
                ft.Container(height=4),
                legend,
            ],
            spacing=GAP,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # ── Cumulative Flow Diagram (matplotlib / xkcd) ────────────────────────────

    def _cfd_chart(self, cfd: list[dict]) -> ft.Control:
        if len(cfd) < 2:
            return ft.Text("Not enough history yet — add cards across multiple days.", size=12, color=_TEXT_SECONDARY)

        from datetime import datetime
        dates = [datetime.strptime(d["date"], "%Y-%m-%d") for d in cfd]

        with plt.style.context("dark_background"):
            fig, ax = plt.subplots(figsize=(10, 3.8))
            ax.stackplot(
                dates,  # type: ignore[arg-type]
                [d["backlog"]     for d in cfd],
                [d["in_progress"] for d in cfd],
                [d["review"]      for d in cfd],
                [d["done"]        for d in cfd],
                labels=["Backlog", "In Progress", "Review", "Done"],
                colors=[
                    _COLOR_STATUS["backlog"],
                    _COLOR_STATUS["in_progress"],
                    _COLOR_STATUS["review"],
                    _COLOR_STATUS["done"],
                ],
            )
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
            ax.xaxis.set_major_locator(mticker.FixedLocator([mdates.date2num(dates[0]), mdates.date2num(dates[-1])]))
            fig.autofmt_xdate(rotation=30)
            ax.set_ylabel("Counts")
            ax.set_title("Daily Cumulative Flow")
            ax.legend(loc="upper left", fontsize=8)
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format="png", dpi=120, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)

        b64 = buf.read()
        return ft.Row(
            [ft.Image(src=b64, border_radius=8)],
            alignment=ft.MainAxisAlignment.CENTER,
        )

    # ── Task age trend (twin-axis line chart) ─────────────────────────────

    def _flow_ratio_weekly_chart(
        self,
        weekly: list[dict],
        numer_key: str,
        denom_key: str,
        label: str,
        color: str,
    ) -> ft.Control:
        """Line chart of a weekly ratio (numer ÷ denom).

        Weeks where the denominator is zero are plotted as gaps (NaN) rather
        than spiking to infinity — a per-week ratio is only meaningful when
        there was inflow to divide by.
        """
        plottable = [w for w in weekly if w.get(denom_key, 0) > 0]
        if not plottable:
            return ft.Text(
                "Not enough flow yet — move some cards across columns.",
                size=12,
                color=_TEXT_SECONDARY,
            )

        import math
        from datetime import datetime

        dates = [datetime.strptime(w["week_start"], "%Y-%m-%d") for w in weekly]
        values = [
            (w[numer_key] / w[denom_key]) if w.get(denom_key, 0) > 0 else math.nan
            for w in weekly
        ]
        finite = [v for v in values if not math.isnan(v)]

        with plt.style.context("dark_background"):
            fig, ax = plt.subplots(figsize=(5, 3.4))
            ax.plot(dates, values, color=color, linewidth=2, marker="o", markersize=4, label=label)  # type: ignore[arg-type]
            # Reference line at 1.0 — the "balanced flow" mark.
            ax.axhline(y=1.0, color="#8B949E", linestyle="--", linewidth=1, alpha=0.7)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
            ax.xaxis.set_major_locator(mticker.FixedLocator([mdates.date2num(dates[0]), mdates.date2num(dates[-1])]))
            fig.autofmt_xdate(rotation=30)

            ax.set_ylabel("Ratio")
            y_top = max([1.0, *finite]) * 1.15 if finite else 1.2
            ax.set_ylim(0, y_top)
            ax.set_title(label)
            ax.legend(loc="upper left", fontsize=8)
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format="png", dpi=120, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)

        b64 = buf.read()
        return ft.Row(
            [ft.Image(src=b64, border_radius=8)],
            alignment=ft.MainAxisAlignment.CENTER,
        )

    def _task_age_chart(self, series: list[dict]) -> ft.Control:
        if len(series) < 2:
            return ft.Text("Not enough history yet — add cards across multiple days.", size=12, color=_TEXT_SECONDARY)

        from datetime import datetime
        dates = [datetime.strptime(d["date"], "%Y-%m-%d") for d in series]
        mn = [d["min_age"] for d in series]
        avg = [d["avg_age"] for d in series]
        mx = [d["max_age"] for d in series]

        min_color = "#56D364"
        avg_color = "#58A6FF"
        max_color = "#F0A742"

        with plt.style.context("dark_background"):
            fig, ax = plt.subplots(figsize=(10, 3.8))
            ax.plot(dates, mn, color=min_color, linewidth=2, label="Min age")  # type: ignore[arg-type]
            ax.plot(dates, avg, color=avg_color, linewidth=2, label="Avg age")  # type: ignore[arg-type]
            ax.plot(dates, mx, color=max_color, linewidth=2, label="Max age")  # type: ignore[arg-type]

            # Threshold guide lines (dashed). Amber = watch, red = needs attention.
            # Average-age bands and oldest(max)-age bands come from settings so
            # the chart and the health indicator stay in lockstep.
            ax.axhline(y=settings.HEALTH_AVG_AGE_WATCH_DAYS, color=avg_color, linestyle="--", linewidth=1, alpha=0.5)
            ax.axhline(y=settings.HEALTH_AVG_AGE_BAD_DAYS, color="#F85149", linestyle="--", linewidth=1, alpha=0.5)
            ax.axhline(y=settings.HEALTH_MAX_AGE_WATCH_DAYS, color=max_color, linestyle="--", linewidth=1, alpha=0.5)
            ax.axhline(y=settings.HEALTH_MAX_AGE_BAD_DAYS, color="#F85149", linestyle="--", linewidth=1, alpha=0.7)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
            ax.xaxis.set_major_locator(mticker.FixedLocator([mdates.date2num(dates[0]), mdates.date2num(dates[-1])]))
            fig.autofmt_xdate(rotation=30)

            ax.set_ylabel("Age (days)")
            # Keep threshold lines visible even when actual ages are well below them.
            y_max = max([*mx, settings.HEALTH_AVG_AGE_BAD_DAYS]) if mx else 0
            ax.set_ylim(-0.1 * y_max, y_max * 1.1 if y_max else 1)
            ax.set_title("Open Task Age Over Time")
            ax.legend(loc="upper left", fontsize=8)
            plt.tight_layout()

            buf = io.BytesIO()
            plt.savefig(buf, format="png", dpi=120, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)

        b64 = buf.read()
        return ft.Column(
            [
                ft.Row(
                    [ft.Image(src=b64, border_radius=8)],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                ft.Text(
                    "Age = days since a card was created. Done cards are excluded — "
                    "only open (backlog, in progress, review) cards count toward these figures. "
                    f"Dashed guides: avg-age watch {settings.HEALTH_AVG_AGE_WATCH_DAYS:.0f}d / "
                    f"bad {settings.HEALTH_AVG_AGE_BAD_DAYS:.0f}d, oldest-age watch "
                    f"{settings.HEALTH_MAX_AGE_WATCH_DAYS:.0f}d / bad {settings.HEALTH_MAX_AGE_BAD_DAYS:.0f}d.",
                    size=11,
                    color=_TEXT_SECONDARY,
                    italic=True,
                ),
            ],
            spacing=8,
        )

    # ── Throughput line chart (kept) ──────────────────────────────────────────

    def _line_chart(self, data: list[dict]) -> ft.Control:
        if not data:
            return ft.Text("No done cards yet", size=12, color=_TEXT_SECONDARY)

        points = [
            fch.LineChartDataPoint(x=float(i), y=float(d["count"]))
            for i, d in enumerate(data)
        ]

        line_data = fch.LineChartData(
            points=points,
            stroke_width=2,
            color="#6DD98A",
            curved=True,
            rounded_stroke_cap=True,
        )

        labels = [d["date"] for d in data]
        bottom_axis = fch.ChartAxis(
            labels=[
                fch.ChartAxisLabel(
                    value=float(i),
                    label=ft.Container(
                        content=ft.Text(labels[i], size=9, color=_TEXT_SECONDARY),
                        padding=ft.Padding(0, 4, 0, 0),
                    ),
                )
                for i in range(0, len(labels), max(1, len(labels) // 6))
            ],
            label_size=28,
        )

        max_count = max(d["count"] for d in data) if data else 1

        return fch.LineChart(
            data_series=[line_data],
            border=ft.Border(bottom=ft.BorderSide(1, "#3E3E42")),
            horizontal_grid_lines=fch.ChartGridLines(color="#2D2D2D", width=1),
            bottom_axis=bottom_axis,
            min_y=0,
            max_y=float(max_count + 1),
            height=180,
            bgcolor="#252526",
            interactive=True,
        )
