"""ui/health_page.py — Project Health dashboard page.
"""
from __future__ import annotations

import base64
import datetime as dt
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import logging
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

import flet as ft
import flet_charts as fch

from kanbai.board import settings
from kanbai.board.storage import sqlite_cache
from kanbai.ui import theme

# ── Palette (app dark theme) ──────────────────────────────────────────────────
_BG_CARD    = "#1C2128"
_BG_SECTION = "#161B22"
_BORDER     = "#30363D"
_TEXT       = "#E6EDF3"
_TEXT_MUT   = "#8B949E"
_TEXT_DIM   = "#484F58"
_CYAN       = "#58A6FF"
_AMBER      = "#F0A742"
_RED        = "#F85149"
_GREEN      = "#56D364"
_DIM        = "#484F58"

# matplotlib
_MPL_BG     = "#1C2128"
_MPL_GRID   = "#30363D"

_BAND_COLOR = {"good": _GREEN, "watch": _AMBER, "bad": _RED}

# Faster tooltip for every interactive element on this page
_TIP_MS = 400
def _tip(msg: str) -> ft.Tooltip:
    return ft.Tooltip(message=msg, wait_duration=_TIP_MS)

# Sentinel — distinct from any real epic (including None = "All")
_NEVER_REFRESHED = object()


# ── Type / priority colour maps (module-level so _pie_panel can reach them) ───
_TYPE_COLORS: dict[str, str] = {
    "bugfix":      "#F85149",
    "feature":     "#58A6FF",
    "enhancement": "#56D364",
    "other":       "#484F58",
}

_PRI_COLORS: dict[str, str] = {
    "high":   "#F85149",
    "medium": "#F0A742",
    "low":    "#58A6FF",
}


# ── Band helpers (spec thresholds, distinct from settings.py) ─────────────────

def _band_inv(v: float | int | None, watch: float, bad: float) -> str:
    """Inverted: lower is worse (completed count, throughput)."""
    if v is None:
        return "good"
    if v < bad:
        return "bad"
    if v < watch:
        return "watch"
    return "good"


def _band_dir(v: float | int | None, watch: float, bad: float) -> str:
    """Direct: higher is worse (WIP count)."""
    if v is None:
        return "good"
    if v > bad:
        return "bad"
    if v > watch:
        return "watch"
    return "good"


def _band_gte(v: float | int | None, watch: float, bad: float) -> str:
    """Threshold (>= triggers band): blockers, due-soon."""
    if v is None:
        return "good"
    if v >= bad:
        return "bad"
    if v >= watch:
        return "watch"
    return "good"


class HealthPage:
    def __init__(self, page: ft.Page, on_open_card=None, on_epic_change=None):
        self._page = page
        self._on_open_card = on_open_card
        self._on_epic_change_cb = on_epic_change
        self._view: ft.Column | None = None
        self._selected_epic: str | None = None
        self._last_refreshed_epic: object = _NEVER_REFRESHED
        self._heatmap_year: int = dt.date.today().year
        self._heatmap_container: ft.Container | None = None

        # ── Chart range state (persist across epic changes) ────────────────
        self._range_pull_rate: int = 28
        self._range_tp_wip: int = 28
        self._range_ct_trend: int = 28
        self._range_ct_hist: int = 28
        self._range_cfd: int = 60
        self._range_backlog_age: int = 28

        # ── Per-chart updatable containers (set during _build_body) ──────────
        self._chart_cnt_pull_rate: ft.Container | None = None
        self._chart_cnt_tp_wip: ft.Container | None = None
        self._chart_cnt_ct_trend: ft.Container | None = None
        self._chart_cnt_ct_hist: ft.Container | None = None
        self._chart_cnt_cfd: ft.Container | None = None
        self._chart_cnt_backlog_age: ft.Container | None = None
        self._btns_cnt_pull_rate: ft.Container | None = None
        self._btns_cnt_tp_wip: ft.Container | None = None
        self._btns_cnt_ct_trend: ft.Container | None = None
        self._btns_cnt_ct_hist: ft.Container | None = None
        self._btns_cnt_cfd: ft.Container | None = None
        self._btns_cnt_backlog_age: ft.Container | None = None

    # ── Flow helpers ──────────────────────────────────────────────────────────

    def _flow(self, days: int) -> dict[str, int]:
        now = dt.datetime.now(dt.timezone.utc)
        since = now - dt.timedelta(days=days)
        return sqlite_cache.transition_flow_counts(
            since.isoformat(), now.isoformat(), epic=self._selected_epic
        )

    # ── Epic selector ─────────────────────────────────────────────────────────

    def _on_epic_change(self, e) -> None:
        val = e.control.value
        self._selected_epic = None if val == "" else val
        self._last_refreshed_epic = self._selected_epic
        self.refresh()
        if self._on_epic_change_cb:
            self._on_epic_change_cb(self._selected_epic)

    def select_epic(self, epic: str | None) -> None:
        if epic == self._last_refreshed_epic:
            return
        self._last_refreshed_epic = epic
        self._selected_epic = epic
        self.refresh()

    # ── Public API ────────────────────────────────────────────────────────────

    def build(self) -> ft.Column:
        body_content = self._build_body()
        self._view = ft.Column(
            [
                self._build_header(),
                ft.Container(
                    content=body_content,
                    padding=ft.Padding(16, 0, 16, 24),
                    expand=True,
                ),
            ],
            spacing=0,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        )
        self._last_refreshed_epic = self._selected_epic
        return self._view

    def refresh(self) -> None:
        if self._view is None:
            return
        self._last_refreshed_epic = self._selected_epic
        import threading
        import traceback
        def _do() -> None:
            try:
                header = self._build_header()
                body = self._build_body()
                self._view.controls[0] = header          # type: ignore[index]
                self._view.controls[1].content = body    # type: ignore[index]
                try:
                    self._page.update()
                except Exception:
                    pass
            except Exception:
                traceback.print_exc()
        threading.Thread(target=_do, daemon=True).start()

    def _build_header(self) -> ft.Container:
        from kanbai.board.epics import list_epics
        epics = list_epics(include_archived=False)
        options = [
            ft.dropdown.Option(key="", text="All"),
            *[ft.dropdown.Option(key=e, text=e) for e in sorted(epics)],
        ]
        return ft.Container(
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text("Project Health", size=16,
                                    weight=ft.FontWeight.BOLD, color=_TEXT),
                            ft.Text("28-day operational view", size=11, color=_TEXT_MUT),
                        ],
                        spacing=2, tight=True,
                    ),
                    ft.Row(
                        [
                            ft.Text("Epic:", size=12, color=_TEXT_MUT),
                            theme.epic_dropdown(
                                options=options,
                                value=self._selected_epic or "",
                                on_select=self._on_epic_change,
                            ),
                            ft.Text(
                                dt.datetime.now().strftime("%a %d %b %Y · %H:%M"),
                                size=11,
                                color=_TEXT_DIM,
                                font_family="monospace",
                            ),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding(16, 14, 16, 12),
            border=ft.Border(bottom=ft.BorderSide(1, _BORDER)),
        )

    # ── Body ──────────────────────────────────────────────────────────────────

    def _build_body(self) -> ft.Column:
        epic = self._selected_epic

        # ── Fetch all data ────────────────────────────────────────────────────
        by_lane       = sqlite_cache.count_cards_by_lane(epic=epic)
        completed_7d  = sqlite_cache.count_completed_within(7,  epic=epic)
        flow_7        = self._flow(7)
        blocker_count = sqlite_cache.count_blocker_cards(epic=epic)
        due_1d        = sqlite_cache.count_due_within(1, epic=epic)
        due_3d        = sqlite_cache.count_due_within(3, epic=epic)

        wip_now = by_lane.get("in_progress", 0) + by_lane.get("review", 0)
        completed_in_window_7d = sqlite_cache.count_completed_from_window(7, epic=epic)
        tp7_val = (
            flow_7["completed"] / flow_7["started"]
            if flow_7["started"] > 0 else None
        )

        pull_7d = (
            flow_7["started"] / flow_7["created"]
            if flow_7["created"] > 0 else None
        )

        # A-series stat card bands (all keyed off settings.py constants)
        def _due_band_fn(d3: int, d1: int) -> str:
            if d3 >= settings.HEALTH_DUE_BAD or d1 >= 1:
                return "bad"
            if d3 >= settings.HEALTH_DUE_WATCH:
                return "watch"
            return "good"

        kpi_bands = {
            "wip":  _band_dir(wip_now,       settings.HEALTH_WIP_WATCH,         settings.HEALTH_WIP_BAD),
            "tp7":  _band_inv(tp7_val,       settings.HEALTH_THROUGHPUT_WATCH,  settings.HEALTH_THROUGHPUT_BAD),
            "pull": _band_inv(pull_7d,       settings.HEALTH_PULL_RATE_WATCH,   settings.HEALTH_PULL_RATE_BAD),
            "blk":  _band_gte(blocker_count, settings.HEALTH_BLOCKER_WATCH,     settings.HEALTH_BLOCKER_BAD),
            "due":  _due_band_fn(due_3d, due_1d),
        }

        wip_age       = sqlite_cache.wip_age_stats(epic=epic)
        backlog_age   = sqlite_cache.backlog_age_stats(epic=epic)
        median_ct_7d  = sqlite_cache.median_cycle_time_days(7, epic=epic)
        stale_cards   = sqlite_cache.stale_wip_cards_started(settings.STALE_WIP_DAYS, epic=epic)
        blocker_cards      = sqlite_cache.blocker_cards(9999, epic=epic)
        aged_backlog_cards = sqlite_cache.backlog_aged_cards(epic=epic)
        aging_series  = sqlite_cache.backlog_aging_timeseries(self._range_backlog_age, epic=epic)
        ct_hist       = sqlite_cache.cycle_time_histogram(self._range_ct_hist, epic=epic)

        # B1: % of completed cards with cycle time > 3d (higher = worse)
        _ct_total = sum(ct_hist.values())
        _ct_over_3d = sum(ct_hist.get(k, 0) for k in ["4", "5", "6+"])
        ct_over_3d_pct: float | None = (_ct_over_3d / _ct_total * 100) if _ct_total > 0 else None
        ct_hist_band = _band_dir(ct_over_3d_pct, 50, 70)
        ct_hist_badge: tuple[str, str] | None = (
            (f"{ct_over_3d_pct:.0f}% > 3d", ct_hist_band)
            if ct_over_3d_pct is not None else None
        )
        ct_weekly     = sqlite_cache.cycle_time_rolling(self._range_ct_trend, window=14, epic=epic)
        wip_comp      = sqlite_cache.wip_composition(epic=epic)
        backlog_comp  = sqlite_cache.backlog_composition(epic=epic)
        comp_comp     = sqlite_cache.completed_composition(28, epic=epic)
        tp_wip_series = sqlite_cache.daily_throughput_wip(self._range_tp_wip, epic=epic)
        pull_series   = sqlite_cache.daily_pull_rate(self._range_pull_rate, epic=epic)
        cfd           = sqlite_cache.cfd_data(self._range_cfd, epic=epic)

        # ── Per-chart range-selector containers ─────────────────────────────
        self._btns_cnt_pull_rate = ft.Container(
            content=self._range_btn_row([14, 28, 60], self._range_pull_rate, self._set_range_pull_rate)
        )
        self._chart_cnt_pull_rate = ft.Container(
            content=self._chart_pull_rate(pull_series)
        )

        self._btns_cnt_tp_wip = ft.Container(
            content=self._range_btn_row([14, 28, 60], self._range_tp_wip, self._set_range_tp_wip)
        )
        self._chart_cnt_tp_wip = ft.Container(
            content=self._chart_throughput_wip(tp_wip_series)
        )

        self._btns_cnt_ct_trend = ft.Container(
            content=self._range_btn_row([14, 28, 60], self._range_ct_trend, self._set_range_ct_trend)
        )
        self._chart_cnt_ct_trend = ft.Container(
            content=self._chart_ct_weekly_trend_mpl(ct_weekly)
        )

        self._btns_cnt_ct_hist = ft.Container(
            content=self._range_btn_row([14, 28, 60], self._range_ct_hist, self._set_range_ct_hist)
        )
        self._chart_cnt_ct_hist = ft.Container(
            content=self._chart_ct_histogram_mpl(ct_hist)
        )

        self._btns_cnt_cfd = ft.Container(
            content=self._range_btn_row([30, 60, 90], self._range_cfd, self._set_range_cfd)
        )
        self._chart_cnt_cfd = ft.Container(
            content=self._chart_cfd_mpl(cfd)
        )

        self._btns_cnt_backlog_age = ft.Container(
            content=self._range_btn_row([14, 28, 60], self._range_backlog_age, self._set_range_backlog_age)
        )
        self._chart_cnt_backlog_age = ft.Container(
            content=self._chart_backlog_aging_mpl(aging_series)
        )

        return ft.Column(
            [
                # ── 0. Stat cards (inflow → process → outflow → age → risk) ──
                ft.Row(
                    [
                        self._stat_card_throughput(
                            tp7_val,
                            flow_7["completed"],
                            flow_7["started"],
                        ),
                        self._stat_card_wip(
                            wip_now,
                            by_lane.get("in_progress", 0),
                            by_lane.get("review", 0),
                            kpi_bands["wip"],
                        ),
                        self._stat_card_wip_age(wip_age),
                        self._stat_card_median_ct(median_ct_7d),
                        self._stat_card_pull_rate(
                            flow_7["started"],
                            flow_7["created"],
                        ),
                        self._stat_card_backlog_age(backlog_age),
                        self._stat_card_blockers(
                            blocker_count,
                            sum(int(c.get("block_count") or 0) for c in blocker_cards),
                            kpi_bands["blk"],
                        ),
                        self._stat_card_due(
                            due_3d,
                            due_1d,
                            kpi_bands["due"],
                        ),
                    ],
                    spacing=10,
                ),
                ft.Container(height=14),

                # ── Row 1: Pull Rate 1/2 | Throughput & WIP 1/2 ──────────────
                ft.Row(
                    [
                        ft.Container(
                            content=self._panel(
                                "Pull Rate (7d rolling)",
                                self._chart_cnt_pull_rate,
                                header_right=self._btns_cnt_pull_rate,
                                reading_guide="Sustained values below 0.5 mean backlog is growing faster than work is pulled.",
                            ),
                            expand=1,
                            height=340,
                        ),
                        ft.Container(width=12),
                        ft.Container(
                            content=self._panel(
                                "Throughput & WIP Trend",
                                self._chart_cnt_tp_wip,
                                header_right=self._btns_cnt_tp_wip,
                                reading_guide="Rising WIP with flat throughput signals accumulation. Watch for divergence.",
                            ),
                            expand=1,
                            height=340,
                        ),
                    ],
                    spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Container(height=14),

                # ── Row 2: Cycle Time Trend 1/2 | CT Distribution 1/2 ────────
                ft.Row(
                    [
                        ft.Container(
                            content=self._panel(
                                "Cycle Time Trend (median + IQR)",
                                self._chart_cnt_ct_trend,
                                header_right=self._btns_cnt_ct_trend,
                                reading_guide="Rising median signals slower delivery. Wide IQR means inconsistent sizing.",
                            ),
                            expand=1,
                            height=340,
                        ),
                        ft.Container(width=12),
                        ft.Container(
                            content=self._panel(
                                "Cycle Time Distribution",
                                self._chart_cnt_ct_hist,
                                header_right=self._btns_cnt_ct_hist,
                                badge=ct_hist_badge,
                                reading_guide="Heavy right tail means too many cards taking longer than 3 days.",
                            ),
                            expand=1,
                            height=340,
                        ),
                    ],
                    spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Container(height=14),

                # ── Row 3: CFD 1/2 | Backlog Age 1/2 ─────────────────────────
                ft.Row(
                    [
                        ft.Container(
                            content=self._panel(
                                "Cumulative Flow Diagram",
                                self._chart_cnt_cfd,
                                header_right=self._btns_cnt_cfd,
                                reading_guide="Widening bands = growing WIP. Parallel bands = stable flow. Flat done line = stalled delivery.",
                            ),
                            expand=1,
                            height=340,
                        ),
                        ft.Container(width=12),
                        ft.Container(
                            content=self._panel(
                                "Backlog Age (Median + IQR)",
                                self._chart_cnt_backlog_age,
                                header_right=self._btns_cnt_backlog_age,
                                reading_guide="Rising median means backlog is aging. Prune or prioritise stale items.",
                            ),
                            expand=1,
                            height=340,
                        ),
                    ],
                    spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Container(height=14),

                # ── 4. Recents row: Stale WIP · Blockers · Aged Backlog ──────
                ft.Row(
                    [
                        ft.Container(
                            content=self._panel(
                                f"Stale WIP (started > {settings.STALE_WIP_DAYS}d)",
                                self._stale_wip_recents(stale_cards),
                                badge=self._stale_badge(stale_cards),
                                scrollable=True,
                            ),
                            expand=True,
                            height=380,
                        ),
                        ft.Container(
                            content=self._panel(
                                "Blockers",
                                self._blockers_recents(blocker_cards),
                                badge=(
                                    (str(blocker_count), kpi_bands["blk"])
                                    if blocker_count
                                    else ("0", "good")
                                ),
                                scrollable=True,
                            ),
                            expand=True,
                            height=380,
                        ),
                        ft.Container(
                            content=self._panel(
                                f"Aged Backlog (> {settings.AGED_BACKLOG_MULTIPLIER}× median)",
                                self._aged_backlog_recents(aged_backlog_cards),
                                badge=self._aged_backlog_badge(aged_backlog_cards),
                                scrollable=True,
                            ),
                            expand=True,
                            height=380,
                        ),
                    ],
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Container(height=14),

                # ── 5. Pie rows ───────────────────────────────────────────────
                ft.Row(
                    [
                        ft.Container(
                            content=self._pie_panel(
                                "WIP by Type",
                                wip_comp["by_tag"],
                                _TYPE_COLORS,
                                wip_comp["total"],
                                self._wip_type_flag(wip_comp),
                            ),
                            expand=True,
                            height=280,
                        ),
                        ft.Container(width=12),
                        ft.Container(
                            content=self._pie_panel(
                                "WIP by Priority",
                                wip_comp["by_priority"],
                                _PRI_COLORS,
                                wip_comp["total"],
                                self._wip_pri_flag(wip_comp, backlog_comp),
                            ),
                            expand=True,
                            height=280,
                        ),
                    ],
                    spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Container(height=14),
                ft.Row(
                    [
                        ft.Container(
                            content=self._pie_panel(
                                "Completed by Type (28d)",
                                comp_comp["by_tag"],
                                _TYPE_COLORS,
                                comp_comp["total"],
                                self._comp_type_flag(comp_comp),
                            ),
                            expand=True,
                            height=280,
                        ),
                        ft.Container(width=12),
                        ft.Container(
                            content=self._pie_panel(
                                "Completed by Priority (28d)",
                                comp_comp["by_priority"],
                                _PRI_COLORS,
                                comp_comp["total"],
                                None,
                            ),
                            expand=True,
                            height=280,
                        ),
                    ],
                    spacing=0,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Container(height=14),

                # ── Activity Heatmap ──────────────────────────────────────────
                ft.Row(
                    [self._activity_heatmap_section()],
                    expand=True,
                ),
                ft.Container(height=14),
            ],
            spacing=0,
        )

    # ── Activity Heatmap ─────────────────────────────────────────────────────

    def _set_heatmap_year(self, year: int) -> None:
        self._heatmap_year = year
        if self._heatmap_container is not None:
            self._heatmap_container.content = self._heatmap_content()
            try:
                self._page.update()
            except Exception:
                pass

    def _activity_heatmap_section(self) -> ft.Container:
        bs = ft.BorderSide(1, _BORDER)
        self._heatmap_container = ft.Container(
            content=self._heatmap_content(),
            bgcolor=_BG_CARD,
            border=ft.Border(bs, bs, bs, bs),
            border_radius=8,
            padding=ft.Padding(14, 12, 14, 14),
            expand=True,
        )
        return self._heatmap_container

    def _heatmap_content(self) -> ft.Column:
        year = self._heatmap_year
        data = sqlite_cache.transition_activity_heatmap(year, epic=self._selected_epic)
        total_year = sum(data.values())

        current_year = dt.date.today().year
        years = list(range(current_year - 2, current_year + 1))

        year_btns = []
        for y in years:
            selected = y == year
            year_btns.append(
                ft.Container(
                    content=ft.Text(str(y), size=10, color=_TEXT if selected else _TEXT_MUT),
                    bgcolor=_BORDER if selected else "transparent",
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
                        ft.Text("ACTIVITY HEATMAP", size=10, weight=ft.FontWeight.W_600, color=_TEXT_MUT),
                        ft.Text(
                            f"{total_year} card movement{'s' if total_year != 1 else ''} in {year}",
                            size=10, color=_TEXT_DIM,
                        ),
                    ],
                    spacing=2, tight=True,
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

        return ft.Column([header, grid], spacing=10)

    def _heatmap_grid(self, data: dict[str, int], year: int) -> ft.Control:
        CELL = 16
        GAP = 3
        today = dt.date.today()

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
        grid_start = start - dt.timedelta(days=start.weekday())

        weeks: list[list[dt.date]] = []
        cur = grid_start
        while cur <= end:
            weeks.append([cur + dt.timedelta(days=d) for d in range(7)])
            cur += dt.timedelta(weeks=1)

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
                    content=ft.Text(label, size=8, color=_TEXT_MUT) if label else None,
                    width=CELL,
                )
            )

        day_rows: list[ft.Control] = []
        for day_idx in range(7):
            cells: list[ft.Control] = []
            for week in weeks:
                day = week[day_idx]
                if day.year == year:
                    count = data.get(day.isoformat(), 0)
                    future = day > today
                    cells.append(
                        ft.Container(
                            width=CELL, height=CELL,
                            bgcolor=color_for(count, future),
                            border_radius=2,
                            tooltip=_tip(f"{day.strftime('%b %d, %Y')}: {count} movement{'s' if count != 1 else ''}"),
                        )
                    )
                else:
                    cells.append(ft.Container(width=CELL, height=CELL))
            day_rows.append(ft.Row(cells, spacing=GAP))

        legend = ft.Row(
            [
                ft.Text("Less", size=9, color=_TEXT_MUT),
                *[ft.Container(width=CELL, height=CELL, bgcolor=c, border_radius=2) for c in _HEAT_COLORS],
                ft.Text("More", size=9, color=_TEXT_MUT),
            ],
            spacing=3,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
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

    # ── Stat cards ──────────────────────────────────────────────────────────────

    def _stat_card_wip(
        self,
        wip_now: int,
        in_progress: int,
        review: int,
        band: str,
    ) -> ft.Container:
        """Large WIP Count Now stat card."""
        value_color  = _BAND_COLOR.get(band, _TEXT)
        border_color = {"watch": _AMBER, "bad": _RED}.get(band, _BORDER)
        bg = (
            ft.Colors.with_opacity(0.07, border_color)
            if band in ("watch", "bad")
            else _BG_CARD
        )
        bs = ft.BorderSide(1, border_color)

        card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.AUTORENEW, size=13, color=_TEXT_MUT),
                            ft.Text(
                                "WIP COUNT NOW", size=10,
                                weight=ft.FontWeight.W_600,
                                color=_TEXT_MUT,
                            ),
                        ],
                        spacing=5,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        tight=True,
                    ),
                    ft.Text(
                        str(wip_now), size=42,
                        weight=ft.FontWeight.BOLD,
                        color=value_color,
                        font_family="monospace",
                    ),
                    ft.Text("in progress + review", size=11, color=_TEXT_MUT),
                ],
                spacing=4,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor=bg,
            border=ft.Border(bs, bs, bs, bs),
            border_radius=8,
            padding=ft.Padding(20, 18, 24, 18),
            expand=True,
            height=140,
            tooltip=_tip(f"In Progress: {in_progress}  │  Review: {review}\n⚠ Watch: >{settings.HEALTH_WIP_WATCH}  │  Bad: >{settings.HEALTH_WIP_BAD}"),
        )
        return card

    def _stat_card_blockers(
        self,
        blocker_count: int,
        blocked_tasks: int,
        band: str,
    ) -> ft.Container:
        """Large Blockers stat card."""
        value_color  = _BAND_COLOR.get(band, _TEXT)
        border_color = {"watch": _AMBER, "bad": _RED}.get(band, _BORDER)
        bg = (
            ft.Colors.with_opacity(0.07, border_color)
            if band in ("watch", "bad")
            else _BG_CARD
        )
        bs = ft.BorderSide(1, border_color)

        card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.BLOCK, size=13, color=_TEXT_MUT),
                            ft.Text(
                                "BLOCKERS", size=10,
                                weight=ft.FontWeight.W_600,
                                color=_TEXT_MUT,
                            ),
                        ],
                        spacing=5,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        tight=True,
                    ),
                    ft.Text(
                        str(blocker_count), size=42,
                        weight=ft.FontWeight.BOLD,
                        color=value_color,
                        font_family="monospace",
                    ),
                    ft.Text("active blocker cards", size=11, color=_TEXT_MUT),
                ],
                spacing=4,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor=bg,
            border=ft.Border(bs, bs, bs, bs),
            border_radius=8,
            padding=ft.Padding(20, 18, 24, 18),
            expand=True,
            height=140,
            tooltip=_tip(f"Blocked tasks: {blocked_tasks}\n⚠ Watch: ≥{settings.HEALTH_BLOCKER_WATCH}  │  Bad: ≥{settings.HEALTH_BLOCKER_BAD}"),
        )
        return card

    def _stat_card_due(
        self,
        due_3d: int,
        due_1d: int,
        band: str,
    ) -> ft.Container:
        """Large Due in 3d stat card."""
        value_color  = _BAND_COLOR.get(band, _TEXT)
        border_color = {"watch": _AMBER, "bad": _RED}.get(band, _BORDER)
        bg = (
            ft.Colors.with_opacity(0.07, border_color)
            if band in ("watch", "bad")
            else _BG_CARD
        )
        bs = ft.BorderSide(1, border_color)

        card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.EVENT, size=13, color=_TEXT_MUT),
                            ft.Text(
                                "DUE IN 3 DAYS", size=10,
                                weight=ft.FontWeight.W_600,
                                color=_TEXT_MUT,
                            ),
                        ],
                        spacing=5,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        tight=True,
                    ),
                    ft.Text(
                        str(due_3d), size=42,
                        weight=ft.FontWeight.BOLD,
                        color=value_color,
                        font_family="monospace",
                    ),
                    ft.Text(
                        f"≤1d: {due_1d}  ·  2–3d: {due_3d - due_1d}",
                        size=11, color=_TEXT_MUT,
                    ),
                ],
                spacing=4,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor=bg,
            border=ft.Border(bs, bs, bs, bs),
            border_radius=8,
            padding=ft.Padding(20, 18, 24, 18),
            expand=True,
            height=140,
            tooltip=_tip(f"Due ≤1d: {due_1d}  ·  Due 2–3d: {due_3d - due_1d}\n⚠ Watch: ≥{settings.HEALTH_DUE_WATCH} in 3d  │  Bad: ≥{settings.HEALTH_DUE_BAD} in 3d or any ≤1d"),
        )
        return card

    def _stat_card_wip_age(
        self,
        stats: dict,
    ) -> ft.Container:
        """WIP Median Age stat card."""
        median = stats.get("median_age")
        avg    = stats.get("avg_age")
        mx     = stats.get("max_age")

        val_str = f"{median:.0f}d" if median is not None else "—"

        # band: >WATCH_DAYS watch, >BAD_DAYS bad
        if median is None:
            band = "good"
        elif median > settings.HEALTH_WIP_AGE_BAD_DAYS:
            band = "bad"
        elif median > settings.HEALTH_WIP_AGE_WATCH_DAYS:
            band = "watch"
        else:
            band = "good"

        value_color  = _BAND_COLOR.get(band, _TEXT)
        border_color = {"watch": _AMBER, "bad": _RED}.get(band, _BORDER)
        bg = (
            ft.Colors.with_opacity(0.07, border_color)
            if band in ("watch", "bad")
            else _BG_CARD
        )
        bs = ft.BorderSide(1, border_color)

        tooltip_parts = []
        if avg is not None:
            tooltip_parts.append(f"Avg: {avg:.1f}d")
        if mx is not None:
            tooltip_parts.append(f"Max: {mx:.1f}d")
        tooltip_msg = "  │  ".join(tooltip_parts) if tooltip_parts else "No WIP cards"
        tooltip_msg += f"\n⚠ Watch: >{settings.HEALTH_WIP_AGE_WATCH_DAYS:.0f}d  │  Bad: >{settings.HEALTH_WIP_AGE_BAD_DAYS:.0f}d"

        card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.HOURGLASS_BOTTOM, size=13, color=_TEXT_MUT),
                            ft.Text(
                                "WIP AGE", size=10,
                                weight=ft.FontWeight.W_600,
                                color=_TEXT_MUT,
                            ),
                        ],
                        spacing=5,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        tight=True,
                    ),
                    ft.Text(
                        val_str, size=42,
                        weight=ft.FontWeight.BOLD,
                        color=value_color,
                        font_family="monospace",
                    ),
                    ft.Text("median days in WIP lanes", size=11, color=_TEXT_MUT),
                ],
                spacing=4,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor=bg,
            border=ft.Border(bs, bs, bs, bs),
            border_radius=8,
            padding=ft.Padding(20, 18, 24, 18),
            expand=True,
            height=140,
            tooltip=_tip(tooltip_msg),
        )
        return card

    def _stat_card_median_ct(
        self,
        ct: dict,
    ) -> ft.Container:
        """Median cycle time (7d) stat card."""
        median = ct.get("median")
        p25    = ct.get("p25")
        p75    = ct.get("p75")

        val_str = f"{median:.0f}d" if median is not None else "—"

        if median is None:
            band = "good"
        elif median >= settings.HEALTH_CYCLE_TIME_BAD_DAYS:
            band = "bad"
        elif median >= settings.HEALTH_CYCLE_TIME_WATCH_DAYS:
            band = "watch"
        else:
            band = "good"

        value_color  = _BAND_COLOR.get(band, _TEXT)
        border_color = {"watch": _AMBER, "bad": _RED}.get(band, _BORDER)
        bg = (
            ft.Colors.with_opacity(0.07, border_color)
            if band in ("watch", "bad")
            else _BG_CARD
        )
        bs = ft.BorderSide(1, border_color)

        card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.TIMER_OUTLINED, size=13, color=_TEXT_MUT),
                            ft.Text(
                                "CYCLE TIME", size=10,
                                weight=ft.FontWeight.W_600,
                                color=_TEXT_MUT,
                            ),
                        ],
                        spacing=5,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        tight=True,
                    ),
                    ft.Text(
                        val_str, size=42,
                        weight=ft.FontWeight.BOLD,
                        color=value_color,
                        font_family="monospace",
                    ),
                    ft.Text("median completed in past 7d", size=11, color=_TEXT_MUT),
                ],
                spacing=4,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor=bg,
            border=ft.Border(bs, bs, bs, bs),
            border_radius=8,
            padding=ft.Padding(20, 18, 24, 18),
            expand=True,
            height=140,
            tooltip=_tip(
                (f"P25: {p25:.1f}d  ·  P75: {p75:.1f}d"
                if p25 is not None and p75 is not None
                else "No completions in 7d")
                + f"\n⚠ Watch: ≥{settings.HEALTH_CYCLE_TIME_WATCH_DAYS:.0f}d  │  Bad: ≥{settings.HEALTH_CYCLE_TIME_BAD_DAYS:.0f}d"
            ),
        )
        return card

    def _stat_card_throughput(
        self,
        tp7: float | None,
        completed: int,
        started: int,
    ) -> ft.Container:
        """Throughput 7d stat card (completed / started)."""
        val_str = f"{tp7:.2f}" if tp7 is not None else "—"

        # band mirrors settings.HEALTH_THROUGHPUT_* thresholds
        if tp7 is None:
            band = "good"
        elif tp7 < settings.HEALTH_THROUGHPUT_BAD:
            band = "bad"
        elif tp7 < settings.HEALTH_THROUGHPUT_WATCH:
            band = "watch"
        else:
            band = "good"

        value_color  = _BAND_COLOR.get(band, _TEXT)
        border_color = {"watch": _AMBER, "bad": _RED}.get(band, _BORDER)
        bg = (
            ft.Colors.with_opacity(0.07, border_color)
            if band in ("watch", "bad")
            else _BG_CARD
        )
        bs = ft.BorderSide(1, border_color)

        card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.BOLT, size=13, color=_TEXT_MUT),
                            ft.Text(
                                "THROUGHPUT 7D", size=10,
                                weight=ft.FontWeight.W_600,
                                color=_TEXT_MUT,
                            ),
                        ],
                        spacing=5,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        tight=True,
                    ),
                    ft.Text(
                        val_str, size=42,
                        weight=ft.FontWeight.BOLD,
                        color=value_color,
                        font_family="monospace",
                    ),
                    ft.Text("completed ÷ started", size=11, color=_TEXT_MUT),
                ],
                spacing=4,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor=bg,
            border=ft.Border(bs, bs, bs, bs),
            border_radius=8,
            padding=ft.Padding(20, 18, 24, 18),
            expand=True,
            height=140,
            tooltip=_tip(f"Completed: {completed}  │  Started: {started}\n⚠ Watch: <{settings.HEALTH_THROUGHPUT_WATCH}  │  Bad: <{settings.HEALTH_THROUGHPUT_BAD}"),
        )
        return card

    def _stat_card_backlog_age(
        self,
        stats: dict,
    ) -> ft.Container:
        """Median backlog age stat card; tooltip shows outlier count."""
        median   = stats.get("median_age")
        outliers = stats.get("outlier_count_1_5x", 0)

        val_str = f"{median:.0f}d" if median is not None else "—"

        if median is None:
            band = "good"
        elif median > settings.HEALTH_BACKLOG_AGE_BAD_DAYS:
            band = "bad"
        elif median > settings.HEALTH_BACKLOG_AGE_WATCH_DAYS:
            band = "watch"
        else:
            band = "good"

        value_color  = _BAND_COLOR.get(band, _TEXT)
        border_color = {"watch": _AMBER, "bad": _RED}.get(band, _BORDER)
        bg = (
            ft.Colors.with_opacity(0.07, border_color)
            if band in ("watch", "bad")
            else _BG_CARD
        )
        bs = ft.BorderSide(1, border_color)

        tooltip_msg = (
            f"{outliers} card{'s' if outliers != 1 else ''} > {settings.AGED_BACKLOG_MULTIPLIER}× median"
            if median is not None else "No backlog cards"
        )
        tooltip_msg += f"\n⚠ Watch: >{settings.HEALTH_BACKLOG_AGE_WATCH_DAYS:.0f}d  │  Bad: >{settings.HEALTH_BACKLOG_AGE_BAD_DAYS:.0f}d"

        card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.INBOX, size=13, color=_TEXT_MUT),
                            ft.Text(
                                "BACKLOG AGE", size=10,
                                weight=ft.FontWeight.W_600,
                                color=_TEXT_MUT,
                            ),
                        ],
                        spacing=5,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        tight=True,
                    ),
                    ft.Text(
                        val_str, size=42,
                        weight=ft.FontWeight.BOLD,
                        color=value_color,
                        font_family="monospace",
                    ),
                    ft.Text("median days in backlog", size=11, color=_TEXT_MUT),
                ],
                spacing=4,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor=bg,
            border=ft.Border(bs, bs, bs, bs),
            border_radius=8,
            padding=ft.Padding(20, 18, 24, 18),
            expand=True,
            height=140,
            tooltip=_tip(tooltip_msg),
        )
        return card

    def _stat_card_pull_rate(
        self,
        started: int,
        created: int,
    ) -> ft.Container:
        """Pull rate 7d stat card (started / created)."""
        ratio   = started / created if created > 0 else None
        val_str = f"{ratio:.2f}" if ratio is not None else "—"

        if ratio is None:
            band = "good"
        elif ratio < settings.HEALTH_PULL_RATE_BAD:
            band = "bad"
        elif ratio < settings.HEALTH_PULL_RATE_WATCH:
            band = "watch"
        else:
            band = "good"

        value_color  = _BAND_COLOR.get(band, _TEXT)
        border_color = {"watch": _AMBER, "bad": _RED}.get(band, _BORDER)
        bg = (
            ft.Colors.with_opacity(0.07, border_color)
            if band in ("watch", "bad")
            else _BG_CARD
        )
        bs = ft.BorderSide(1, border_color)

        card = ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(ft.Icons.ARROW_CIRCLE_DOWN, size=13, color=_TEXT_MUT),
                            ft.Text(
                                "PULL RATE 7D", size=10,
                                weight=ft.FontWeight.W_600,
                                color=_TEXT_MUT,
                            ),
                        ],
                        spacing=5,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        tight=True,
                    ),
                    ft.Text(
                        val_str, size=42,
                        weight=ft.FontWeight.BOLD,
                        color=value_color,
                        font_family="monospace",
                    ),
                    ft.Text("started ÷ created", size=11, color=_TEXT_MUT),
                ],
                spacing=4,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor=bg,
            border=ft.Border(bs, bs, bs, bs),
            border_radius=8,
            padding=ft.Padding(20, 18, 24, 18),
            expand=True,
            height=140,
            tooltip=_tip(f"Started: {started}  │  Created: {created}\n⚠ Watch: <{settings.HEALTH_PULL_RATE_WATCH}  │  Bad: <{settings.HEALTH_PULL_RATE_BAD}"),
        )
        return card

    # ── KPI row ───────────────────────────────────────────────────────────────

    def _kpi_row(
        self, c7, pull7, wip, tp7, blk, due, bands: dict
    ) -> ft.Row:
        def fmt_tp(v: float | None) -> str:
            return f"{v:.2f}" if v is not None else "—"

        def fmt_ratio(v: float | None) -> str:
            return f"{v:.2f}" if v is not None else "—"

        cards = [
            (ft.Icons.CHECK_CIRCLE_OUTLINE, "Completed 7d",  str(c7),              "tasks done",          bands["c7"]),
            (ft.Icons.MOVE_TO_INBOX,        "Pull Rate 7d",   fmt_ratio(pull7),     "started ÷ created",   bands["pull"]),
            (ft.Icons.AUTORENEW,            "WIP Now",        str(wip),             "in-progress",         bands["wip"]),
            (ft.Icons.TRENDING_UP,          "Throughput 7d",  fmt_tp(tp7),         "completed ÷ started", bands["tp7"]),
            (ft.Icons.BLOCK,                "Blockers",       str(blk),             "active",              bands["blk"]),
            (ft.Icons.SCHEDULE,             "Due in 3d",      str(due),             "tasks",               bands["due"]),
        ]
        return ft.Row(
            [self._kpi_card(icon, label, val, sub, band) for icon, label, val, sub, band in cards],
            spacing=10,
        )

    def _kpi_card(
        self, icon: str | ft.IconData, label: str, value_str: str, sub: str, band: str
    ) -> ft.Container:
        value_color = _BAND_COLOR.get(band, _TEXT)
        label_row = ft.Row(
            [
                ft.Icon(icon, size=12, color=_TEXT_MUT),
                ft.Text(label.upper(), size=10, weight=ft.FontWeight.W_600, color=_TEXT_MUT),
            ],
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        )
        items: list[ft.Control] = [
            label_row,
            ft.Text(value_str, size=28, weight=ft.FontWeight.BOLD,
                    color=value_color, font_family="monospace"),
        ]
        if sub:
            items.append(ft.Text(sub, size=11, color=_TEXT_MUT))
        _BAND_BORDER = {"watch": _AMBER, "bad": _RED}
        border_color = _BAND_BORDER.get(band, _BORDER)
        bg = (
            ft.Colors.with_opacity(0.07, border_color)
            if band in ("watch", "bad")
            else _BG_CARD
        )
        bs = ft.BorderSide(1, border_color)
        return ft.Container(
            content=ft.Column(
                items,
                spacing=4,
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
            expand=True,
            bgcolor=bg,
            border=ft.Border(bs, bs, bs, bs),
            border_radius=8,
            padding=ft.Padding(14, 14, 14, 14),
        )

    # ── Range selector helper ─────────────────────────────────────────────────

    def _range_btn_row(self, options: list[int], current: int, on_change) -> ft.Row:
        """Compact toggle button group for selecting a chart time range."""
        btns: list[ft.Control] = []
        for days in options:
            selected = days == current
            btns.append(
                ft.Container(
                    content=ft.Text(
                        f"{days}d", size=10,
                        color=_TEXT if selected else _TEXT_MUT,
                        weight=ft.FontWeight.W_600 if selected else ft.FontWeight.NORMAL,
                    ),
                    bgcolor=_BORDER if selected else "transparent",
                    border_radius=4,
                    padding=ft.Padding(8, 2, 8, 2),
                    on_click=lambda e, d=days: on_change(d),
                    ink=True,
                )
            )
        return ft.Row(btns, spacing=3, tight=True,
                      vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # ── Per-chart range setters ───────────────────────────────────────────────

    def _set_range_pull_rate(self, days: int) -> None:
        self._range_pull_rate = days
        data = sqlite_cache.daily_pull_rate(days, epic=self._selected_epic)
        if self._chart_cnt_pull_rate:
            self._chart_cnt_pull_rate.content = self._chart_pull_rate(data)
        if self._btns_cnt_pull_rate:
            self._btns_cnt_pull_rate.content = self._range_btn_row(
                [14, 28, 60], days, self._set_range_pull_rate
            )
        try:
            self._page.update()
        except Exception:
            pass

    def _set_range_tp_wip(self, days: int) -> None:
        self._range_tp_wip = days
        data = sqlite_cache.daily_throughput_wip(days, epic=self._selected_epic)
        if self._chart_cnt_tp_wip:
            self._chart_cnt_tp_wip.content = self._chart_throughput_wip(data)
        if self._btns_cnt_tp_wip:
            self._btns_cnt_tp_wip.content = self._range_btn_row(
                [14, 28, 60], days, self._set_range_tp_wip
            )
        try:
            self._page.update()
        except Exception:
            pass

    def _set_range_ct_trend(self, days: int) -> None:
        self._range_ct_trend = days
        data = sqlite_cache.cycle_time_rolling(days, window=14, epic=self._selected_epic)
        if self._chart_cnt_ct_trend:
            self._chart_cnt_ct_trend.content = self._chart_ct_weekly_trend_mpl(data)
        if self._btns_cnt_ct_trend:
            self._btns_cnt_ct_trend.content = self._range_btn_row(
                [14, 28, 60], days, self._set_range_ct_trend
            )
        try:
            self._page.update()
        except Exception:
            pass

    def _set_range_ct_hist(self, days: int) -> None:
        self._range_ct_hist = days
        data = sqlite_cache.cycle_time_histogram(days, epic=self._selected_epic)
        if self._chart_cnt_ct_hist:
            self._chart_cnt_ct_hist.content = self._chart_ct_histogram_mpl(data)
        if self._btns_cnt_ct_hist:
            self._btns_cnt_ct_hist.content = self._range_btn_row(
                [14, 28, 60], days, self._set_range_ct_hist
            )
        try:
            self._page.update()
        except Exception:
            pass

    def _set_range_cfd(self, days: int) -> None:
        self._range_cfd = days
        data = sqlite_cache.cfd_data(days, epic=self._selected_epic)
        if self._chart_cnt_cfd:
            self._chart_cnt_cfd.content = self._chart_cfd_mpl(data)
        if self._btns_cnt_cfd:
            self._btns_cnt_cfd.content = self._range_btn_row(
                [30, 60, 90], days, self._set_range_cfd
            )
        try:
            self._page.update()
        except Exception:
            pass

    def _set_range_backlog_age(self, days: int) -> None:
        self._range_backlog_age = days
        data = sqlite_cache.backlog_aging_timeseries(days, epic=self._selected_epic)
        if self._chart_cnt_backlog_age:
            self._chart_cnt_backlog_age.content = self._chart_backlog_aging_mpl(data)
        if self._btns_cnt_backlog_age:
            self._btns_cnt_backlog_age.content = self._range_btn_row(
                [14, 28, 60], days, self._set_range_backlog_age
            )
        try:
            self._page.update()
        except Exception:
            pass

    # ── Panel wrapper ─────────────────────────────────────────────────────────

    def _panel(
        self,
        title: str,
        content: ft.Control,
        badge: tuple[str, str] | None = None,
        scrollable: bool = False,
        expand_content: bool = False,
        header_right: ft.Control | None = None,
        reading_guide: str | None = None,
    ) -> ft.Container:
        # Right-side header control: header_right takes precedence over badge
        if header_right is not None:
            right_widget: ft.Control = header_right
        elif badge:
            badge_text, badge_band = badge
            badge_color = _BAND_COLOR.get(badge_band, _CYAN)
            right_widget = ft.Container(
                content=ft.Text(
                    badge_text, size=10, weight=ft.FontWeight.W_600,
                    color=badge_color,
                ),
                bgcolor=ft.Colors.with_opacity(0.15, badge_color),
                border_radius=10,
                padding=ft.Padding(8, 3, 8, 3),
            )
        else:
            right_widget = ft.Container()

        # Title with optional reading-guide info icon
        if reading_guide:
            title_widget: ft.Control = ft.Row(
                [
                    ft.Text(
                        title.upper(), size=10,
                        weight=ft.FontWeight.W_600,
                        color=_TEXT_MUT,
                    ),
                    ft.Icon(
                        ft.Icons.INFO_OUTLINE,
                        size=12,
                        color=_TEXT_DIM,
                        tooltip=_tip(reading_guide),
                    ),
                ],
                spacing=4,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
            )
        else:
            title_widget = ft.Text(
                title.upper(), size=10,
                weight=ft.FontWeight.W_600,
                color=_TEXT_MUT,
            )

        bs = ft.BorderSide(1, _BORDER)
        return ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Row(
                            [
                                title_widget,
                                right_widget,
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        bgcolor=_BG_SECTION,
                        padding=ft.Padding(14, 10, 14, 10),
                    ),
                    ft.Container(
                        content=content,
                        padding=ft.Padding(14, 12, 14, 14),
                        expand=scrollable or expand_content,
                    ),
                ],
                spacing=0,
                tight=not (scrollable or expand_content),
                expand=scrollable or expand_content,
            ),
            bgcolor=_BG_CARD,
            border=ft.Border(bs, bs, bs, bs),
            border_radius=8,
            expand=True,
        )

    # ── Stale WIP list ────────────────────────────────────────────────────────

    def _stale_badge(self, cards: list[dict]) -> tuple[str, str] | None:
        """Badge based on days_started: bad if any > BAD_DAYS, watch if any > WATCH_DAYS."""
        critical = sum(1 for c in cards if c.get("days_started", 0) > settings.STALE_WIP_BAD_DAYS)
        warn     = sum(1 for c in cards if c.get("days_started", 0) > settings.STALE_WIP_WATCH_DAYS)
        if critical:
            return (str(critical), "bad")
        if warn:
            return (str(warn), "watch")
        return ("0", "good")

    def _stale_wip_recents(self, cards: list[dict]) -> ft.Control:
        """Recents-style list for stale WIP cards (sorted by started age)."""
        if not cards:
            return ft.Text("No stale WIP cards", size=12, color=_TEXT_MUT)
        return self._recents_list(
            cards,
            age_label_fn=lambda c: (
                f"{c['days_started']}d started",
                _RED if c["days_started"] > settings.STALE_WIP_BAD_DAYS else _AMBER,
            ),
        )

    # ── Recents list (shared renderer, analytics style) ───────────────────────

    def _recents_list(
        self,
        cards: list[dict],
        age_label_fn,  # callable(card) -> (label: str, color: str)
    ) -> ft.Control:
        """Render a compact recents-style list used across Stale WIP, Blockers,
        and Aged Backlog panels.
        """
        from kanbai.ui import theme as _theme
        _STATUS_COLORS = _theme.STATUS_COLORS
        _PRI_COLORS_A  = _theme.PRIORITY_COLORS

        rows: list[ft.Control] = []
        for c in cards:
            card_id   = c["id"]
            task_num  = f"#{c['number']}" if c.get("number") else c["id"][:8]
            priority  = (c.get("priority") or "medium").lower()
            lane_key  = (c.get("lane") or "").lower()
            lane_label = lane_key.replace("_", " ").title()
            lane_color = _STATUS_COLORS.get(lane_key, _TEXT_MUT)
            epic_val   = c.get("epic") or ""
            age_text, age_color = age_label_fn(c)

            # ── Detail chips ──────────────────────────────────────────────────
            details: list[ft.Control] = [
                ft.Text(task_num, size=10, color=_TEXT_MUT),
                ft.Container(width=6, height=6,
                             bgcolor=_PRI_COLORS_A.get(priority, _CYAN),
                             border_radius=3),
                ft.Text(priority.title(), size=10, color=_TEXT_MUT),
            ]
            if lane_label:
                details.append(
                    ft.Container(
                        content=ft.Text(
                            lane_label, size=10, color=lane_color,
                            weight=ft.FontWeight.W_500,
                        ),
                        bgcolor=ft.Colors.with_opacity(0.15, lane_color),
                        border_radius=8,
                        padding=ft.Padding(6, 1, 6, 1),
                    )
                )
            if epic_val:
                details.append(
                    ft.Text(
                        epic_val, size=10, color=_CYAN,
                        max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                    )
                )

            rows.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Text(
                                        c["title"], size=12, color=_TEXT,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                        expand=True,
                                    ),
                                    ft.Text(
                                        age_text, size=11,
                                        weight=ft.FontWeight.W_600,
                                        color=age_color,
                                        font_family="monospace",
                                    ),
                                ],
                                spacing=8,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
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
                    ),
                    bgcolor=_BG_SECTION,
                    border_radius=6,
                    padding=ft.Padding(10, 8, 10, 8),
                    on_click=(
                        (lambda e, cid=card_id, _cb=self._on_open_card: _cb(cid))  # type: ignore[misc]
                        if self._on_open_card else None
                    ),
                    ink=bool(self._on_open_card),
                )
            )
        return ft.Column(
            rows, spacing=6,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    # ── Blockers recents ──────────────────────────────────────────────────────

    def _blockers_recents(self, cards: list[dict]) -> ft.Control:
        """Recents-style list for blocker cards."""
        if not cards:
            return ft.Text("No blockers", size=12, color=_TEXT_MUT)
        return self._recents_list(
            cards,
            age_label_fn=lambda c: (
                f"blocks {int(c.get('block_count') or 0)}",
                _RED,
            ),
        )

    # ── Aged backlog recents ──────────────────────────────────────────────────

    def _aged_backlog_badge(
        self, cards: list[dict]
    ) -> tuple[str, str] | None:
        if not cards:
            return ("0", "good")
        n = len(cards)
        band = "bad" if n >= 5 else "watch"
        return (str(n), band)

    def _aged_backlog_recents(self, cards: list[dict]) -> ft.Control:
        f"""Recents-style list for backlog cards older than {settings.AGED_BACKLOG_MULTIPLIER}× median."""
        if not cards:
            return ft.Text("No aged outliers", size=12, color=_TEXT_MUT)
        return self._recents_list(
            cards,
            age_label_fn=lambda c: (
                f"{c['days_old']:.0f}d old",
                _AMBER,
            ),
        )

    # ── Backlog aging ─────────────────────────────────────────────────────────

    def _aging_badge(self, current: dict | None) -> tuple[str, str] | None:
        if not current:
            return None
        spread = current["max_age"] - current["min_age"]
        if spread > 21 or current["max_age"] > 30:
            return (f"spread {spread}d", "bad")
        if spread > 14:
            return (f"spread {spread}d", "watch")
        return (f"spread {spread}d", "good")

    def _backlog_aging_content(
        self, series: list[dict], current: dict | None
    ) -> ft.Control:
        chart = self._chart_backlog_aging(series)
        if current is None:
            stat_row: ft.Control = ft.Text(
                "No backlog cards", size=11, color=_TEXT_MUT
            )
        else:
            def _stat(label: str, val: float, color: str) -> ft.Column:
                return ft.Column(
                    [
                        ft.Text(
                            f"{val:.0f}d", size=13, weight=ft.FontWeight.BOLD,
                            color=color, font_family="monospace",
                        ),
                        ft.Text(label, size=10, color=_TEXT_DIM),
                    ],
                    spacing=2, tight=True,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                )
            stat_row = ft.Row(
                [
                    _stat("min", current["min_age"], _CYAN),
                    ft.Container(width=16),
                    _stat("avg", current["avg_age"], _AMBER),
                    ft.Container(width=16),
                    _stat("max", current["max_age"], _RED),
                ],
                spacing=0,
            )
        return ft.Column(
            [chart, ft.Container(height=8), stat_row],
            spacing=0, tight=True,
        )

    # ── Pie panels ────────────────────────────────────────────────────────────

    def _wip_type_flag(self, comp: dict) -> tuple[str, str] | None:
        total = comp["total"]
        if total == 0:
            return None
        pct = comp["by_tag"].get("bugfix", 0) / total * 100
        if pct > 50:
            return (f"⚠ bugfix {pct:.0f}%", "bad")
        return None

    def _wip_pri_flag(self, wip_comp: dict, backlog_comp: dict) -> tuple[str, str] | None:
        """C2: priority inversion — WIP has higher med+low proportion than backlog."""
        wip_total     = wip_comp["total"]
        backlog_total = backlog_comp["total"]
        if wip_total == 0 or backlog_total == 0:
            return None
        wip_med_low = (
            wip_comp["by_priority"].get("medium", 0)
            + wip_comp["by_priority"].get("low", 0)
        ) / wip_total
        bl_med_low = (
            backlog_comp["by_priority"].get("medium", 0)
            + backlog_comp["by_priority"].get("low", 0)
        ) / backlog_total
        # Only flag when the gap is meaningful (>10pp)
        if wip_med_low > bl_med_low + 0.10:
            pct = wip_med_low * 100
            bl_pct = bl_med_low * 100
            return (f"⚠ WIP med+low {pct:.0f}% vs backlog {bl_pct:.0f}%", "watch")
        return None

    def _comp_type_flag(self, comp: dict) -> tuple[str, str] | None:
        total = comp["total"]
        if total == 0:
            return None
        pct = comp["by_tag"].get("bugfix", 0) / total * 100
        if pct > 60:
            return (f"⚠ bugfix {pct:.0f}%", "bad")
        return None

    def _pie_panel(
        self,
        title: str,
        data: dict[str, int],
        colors: dict[str, str],
        total: int,
        flag: tuple[str, str] | None,
    ) -> ft.Container:
        if flag:
            flag_color = _BAND_COLOR.get(flag[1], _AMBER)
            badge_widget: ft.Control = ft.Container(
                content=ft.Text(
                    flag[0], size=10, weight=ft.FontWeight.W_600,
                    color=flag_color,
                ),
                bgcolor=ft.Colors.with_opacity(0.15, flag_color),
                border_radius=10,
                padding=ft.Padding(8, 3, 8, 3),
            )
        else:
            badge_widget = ft.Container()

        chart  = self._chart_donut(data, colors, total)
        legend = self._pie_legend(data, colors, total)
        bs     = ft.BorderSide(1, _BORDER)

        return ft.Container(
            content=ft.Column(
                [
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Text(
                                    title.upper(), size=10,
                                    weight=ft.FontWeight.W_600,
                                    color=_TEXT_MUT,
                                ),
                                badge_widget,
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        bgcolor=_BG_SECTION,
                        padding=ft.Padding(14, 10, 14, 10),
                    ),
                    ft.Container(
                        content=ft.Column(
                            [chart, ft.Container(height=8), legend],
                            spacing=0, tight=True,
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        padding=ft.Padding(14, 12, 14, 14),
                    ),
                ],
                spacing=0, tight=True,
            ),
            bgcolor=_BG_CARD,
            border=ft.Border(bs, bs, bs, bs),
            border_radius=8,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            expand=True,
        )

    def _pie_legend(
        self, data: dict[str, int], colors: dict[str, str], total: int
    ) -> ft.Control:
        if total == 0:
            return ft.Text("No data", size=11, color=_TEXT_MUT)
        items: list[ft.Control] = []
        for key, count in data.items():
            pct   = round(count / total * 100) if count > 0 else 0
            color = colors.get(key, _TEXT_DIM)
            items.append(ft.Row(
                [
                    ft.Container(width=8, height=8, bgcolor=color, border_radius=4),
                    ft.Container(
                        content=ft.Text(
                            key, size=11, color=_TEXT_MUT,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        expand=True,
                    ),
                    ft.Text(
                        f"{pct}%", size=11, weight=ft.FontWeight.W_600,
                        color=_TEXT, font_family="monospace",
                    ),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ))
        return ft.Column(items, spacing=5, tight=True)

    # ── Throughput badge ──────────────────────────────────────────────────────

    def _tp_badge(self, series: list[dict]) -> tuple[str, str] | None:
        if not series:
            return None
        vals = [r["throughput_7d"] for r in series if r["throughput_7d"] is not None]
        if not vals:
            return ("No flow data", "watch")
        latest = vals[-1]
        band   = _band_inv(latest, 0.6, 0.4)
        return (f"Latest {latest:.2f}", band)

    def _pull_rate_badge(self, series: list[dict]) -> tuple[str, str] | None:
        if not series:
            return None
        vals = [r["pull_rate_7d"] for r in series if r["pull_rate_7d"] is not None]
        if not vals:
            return ("No flow data", "watch")
        latest = vals[-1]
        band = "good" if latest >= 1.0 else "watch" if latest >= 0.5 else "bad"
        return (f"Latest {latest:.2f}", band)

    # ── Pull Rate chart (matplotlib) ──────────────────────────────────────────

    def _chart_pull_rate(self, series: list[dict]) -> ft.Control:
        """Daily 7-day rolling pull rate (started ÷ created), matplotlib."""
        import math
        from datetime import datetime

        plottable = [r for r in series if r["pull_rate_7d"] is not None]
        if len(plottable) < 2:
            return self._no_data(180)

        dates = [datetime.strptime(r["date"], "%Y-%m-%d") for r in series]
        vals = [
            r["pull_rate_7d"] if r["pull_rate_7d"] is not None else math.nan
            for r in series
        ]

        with plt.style.context("dark_background"):
            fig, ax = plt.subplots(figsize=(7.5, 2.8))
            fig.patch.set_facecolor(_MPL_BG)
            ax.set_facecolor(_MPL_BG)

            _TEAL = "#2EA6A0"
            ax.plot(dates, vals, color=_TEAL, linewidth=1.8, zorder=3,
                    label="Pull rate (7d)")
            ax.fill_between(dates, vals, alpha=0.08, color=_TEAL)
            ax.axhline(y=1.0, color=_TEXT_MUT, linestyle="--", linewidth=1,
                       alpha=0.7, label="Balanced (1.0)")
            ax.axhline(y=settings.HEALTH_PULL_RATE_WATCH, color=_AMBER, linestyle="--",
                       linewidth=1, alpha=0.7,
                       label=f"Watch (< {settings.HEALTH_PULL_RATE_WATCH})")
            ax.axhline(y=settings.HEALTH_PULL_RATE_BAD, color=_RED, linestyle="--",
                       linewidth=1, alpha=0.7,
                       label=f"Bad (< {settings.HEALTH_PULL_RATE_BAD})")

            ax.set_ylabel("started ÷ created", fontsize=8, color=_TEXT_MUT)
            ax.tick_params(axis="y", labelsize=8, colors=_TEXT_MUT)
            finite = [v for v in vals if not math.isnan(v)]
            y_top = max([1.0, *finite]) * 1.3 if finite else 1.5
            ax.set_ylim(0, y_top)
            ax.yaxis.grid(True, color=_MPL_GRID, linewidth=0.5, zorder=0)
            ax.set_axisbelow(True)
            for spine in ax.spines.values():
                spine.set_edgecolor(_BORDER)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
            ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0))
            fig.autofmt_xdate(rotation=20, ha="right")
            ax.tick_params(axis="x", labelsize=8, colors=_TEXT_MUT)
            ax.legend(fontsize=7, loc="upper left", framealpha=0.3)
            plt.tight_layout(pad=0.4)

        return ft.Image(src=self._fig_to_b64(fig), fit=ft.BoxFit.CONTAIN,
                        border_radius=6)

    # ── Charts ────────────────────────────────────────────────────────────────

    @staticmethod
    def _fig_to_b64(fig) -> bytes:
        # Explicitly force an opaque background before saving.
        # savefig is called outside the plt.style.context("dark_background")
        # block, so rcParams may have been restored to defaults which can
        # set transparent=True for that style.  Overriding here is safest.
        fig.patch.set_facecolor(_MPL_BG)
        buf = io.BytesIO()
        fig.savefig(
            buf, format="png", dpi=96,
            bbox_inches="tight",
            facecolor=_MPL_BG,
            transparent=False,
        )
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    @staticmethod
    def _no_data(h: int = 160) -> ft.Control:
        return ft.Container(
            content=ft.Text(
                "Not enough data yet", size=12, color=_TEXT_MUT, italic=True,
            ),
            alignment=ft.Alignment(0, 0),
            height=h,
        )

    # ── Throughput + WIP trend ────────────────────────────────────────────────

    def _chart_throughput_wip(self, series: list[dict]) -> ft.Control:
        plottable = [r for r in series if r["throughput_7d"] is not None]
        if len(plottable) < 2:
            return self._no_data(180)

        import math
        from datetime import datetime

        dates   = [datetime.strptime(r["date"], "%Y-%m-%d") for r in series]
        tp_vals = [
            r["throughput_7d"] if r["throughput_7d"] is not None else math.nan
            for r in series
        ]
        wip_vals = [r["wip"] for r in series]

        with plt.style.context("dark_background"):
            fig, ax1 = plt.subplots(figsize=(7.5, 2.8))
            fig.patch.set_facecolor(_MPL_BG)
            ax1.set_facecolor(_MPL_BG)

            # ── ax1: throughput (left axis) ───────────────────────────────
            ax1.plot(dates, tp_vals, color=_CYAN, linewidth=1.8,  # type: ignore[arg-type]
                     label="Throughput (7d)", zorder=3)
            # Per-date coloured fill — each date's column is green/amber/red
            # based on that day's throughput value.
            _TP_W = settings.HEALTH_THROUGHPUT_WATCH
            _TP_B = settings.HEALTH_THROUGHPUT_BAD
            _nn   = [not math.isnan(v) for v in tp_vals]

            def _tp_color(v: float) -> str:
                if v >= _TP_W:  return _GREEN
                if v >= _TP_B:  return _AMBER
                return _RED

            # Coloured trapezoid fills that follow the line exactly.
            # For each consecutive pair of valid points draw a filled polygon;
            # when the line crosses a threshold boundary split the trapezoid
            # at that x-intercept so each sub-piece gets its own colour.
            valid = [(dates[i], tp_vals[i]) for i in range(len(dates)) if _nn[i]]
            for _k in range(len(valid) - 1):
                _x0, _y0 = valid[_k]
                _x1, _y1 = valid[_k + 1]
                # Find threshold crossings between the two y-values
                _crossings: list[tuple] = []
                for _thresh in (_TP_B, _TP_W):
                    if min(_y0, _y1) < _thresh < max(_y0, _y1):
                        _t = (_thresh - _y0) / (_y1 - _y0)
                        _crossings.append((_x0 + (_x1 - _x0) * _t, _thresh))
                _crossings.sort(key=lambda c: c[0])
                _pts = [(_x0, _y0)] + _crossings + [(_x1, _y1)]
                for _m in range(len(_pts) - 1):
                    _xa, _ya = _pts[_m]
                    _xb, _yb = _pts[_m + 1]
                    _color = _tp_color((_ya + _yb) / 2)
                    ax1.fill([_xa, _xa, _xb, _xb], [0, _ya, _yb, 0],
                             color=_color, alpha=0.35, zorder=2, lw=0)
            ax1.set_ylabel("Throughput", color=_CYAN, fontsize=8)
            ax1.tick_params(axis="y", labelcolor=_CYAN, labelsize=8)
            tp_max = max((v for v in tp_vals if not math.isnan(v)), default=1.0)
            ax1.set_ylim(0, max(tp_max * 1.4, 0.6))
            ax1.yaxis.grid(True, color=_MPL_GRID, linewidth=0.5, zorder=0)
            ax1.set_axisbelow(True)
            for spine in ax1.spines.values():
                spine.set_edgecolor(_BORDER)

            # ── ax2: WIP (right axis, transparent over ax1) ───────────────
            ax2 = ax1.twinx()
            ax2.set_facecolor("none")
            ax2.patch.set_visible(False)
            ax2.plot(dates, wip_vals, color=_AMBER, linewidth=1.8,  # type: ignore[arg-type]
                     linestyle="--", label="WIP", zorder=3)
            ax2.set_ylabel("WIP", color=_AMBER, fontsize=8)
            ax2.tick_params(axis="y", labelcolor=_AMBER, labelsize=8)
            ax2.yaxis.grid(False)
            ax2.xaxis.grid(False)
            max_wip = max(wip_vals) if wip_vals else 10
            wip_ylim = max(max_wip * 1.3, 10)
            ax2.set_ylim(0, wip_ylim)
            # Color WIP y-axis tick labels at threshold values
            wip_thresh_ticks = sorted({
                settings.HEALTH_WIP_WATCH, settings.HEALTH_WIP_BAD,
                *[t for t in ax2.get_yticks() if 0 <= t <= wip_ylim],
            })
            ax2.set_yticks(wip_thresh_ticks)
            for lbl, val in zip(ax2.get_yticklabels(), wip_thresh_ticks):
                if val >= settings.HEALTH_WIP_BAD:
                    lbl.set_color(_RED)
                elif val >= settings.HEALTH_WIP_WATCH:
                    lbl.set_color(_AMBER)
                else:
                    lbl.set_color(_AMBER)
            for spine in ax2.spines.values():
                spine.set_edgecolor(_BORDER)

            ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
            ax1.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0))
            fig.autofmt_xdate(rotation=20, ha="right")
            ax1.tick_params(axis="x", labelsize=8, colors=_TEXT_MUT)

            lines1, labels1 = ax1.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax1.legend(lines1 + lines2, labels1 + labels2,
                       fontsize=8, loc="upper left", framealpha=0.3)
            plt.tight_layout(pad=0.4)

        return ft.Image(src=self._fig_to_b64(fig), fit=ft.BoxFit.CONTAIN,
                        border_radius=6)

    # ── Throughput + WIP trend (Flet chart) ───────────────────────────────

    def _chart_throughput_wip_flet(self, series: list[dict]) -> ft.Control:
        """Throughput (7d) + WIP count as native Flet LineChart."""
        import math
        from datetime import datetime

        filtered = [r for r in series if r["throughput_7d"] is not None]
        if len(filtered) < 2:
            return self._no_data(180)

        dates = [datetime.strptime(r["date"], "%Y-%m-%d") for r in filtered]
        tp_vals = [r["throughput_7d"] for r in filtered]
        wip_vals = [r["wip"] for r in filtered]
        n = len(filtered)

        tp_max = max(tp_vals, default=1.0)
        wip_max = max(wip_vals, default=10)

        # Normalize WIP to throughput scale for dual-axis effect
        wip_scale = tp_max * 1.4 / max(wip_max * 1.3, 1)
        wip_scaled = [w * wip_scale for w in wip_vals]

        # Throughput line
        tp_line = fch.LineChartData(
            points=[
                fch.LineChartDataPoint(
                    x=float(i), y=tp_vals[i],
                    tooltip=f"{dates[i].strftime('%b %d')}\nTP: {tp_vals[i]:.2f}  WIP: {wip_vals[i]}",
                )
                for i in range(n)
            ],
            stroke_width=2.2,
            color=_CYAN,
            below_line_bgcolor=ft.Colors.with_opacity(0.08, _CYAN),
            below_line_cutoff_y=0,
            curved=False,
            point=False,
            selected_point=fch.ChartCirclePoint(radius=4, color=_CYAN),
        )

        # WIP line (scaled)
        wip_line = fch.LineChartData(
            points=[
                fch.LineChartDataPoint(
                    x=float(i), y=wip_scaled[i],
                    show_tooltip=False,
                )
                for i in range(n)
            ],
            stroke_width=2.0,
            color=_AMBER,
            dash_pattern=[6, 3],
            curved=False,
            point=False,
        )

        # X-axis labels
        step = max(1, n // 7)
        bottom_labels = [
            fch.ChartAxisLabel(
                value=float(i),
                label=ft.Text(dates[i].strftime("%b %d"), size=10, color=_TEXT_MUT),
            )
            for i in range(0, n, step)
        ]

        # Left Y-axis (throughput)
        tp_ylim = max(tp_max * 1.4, 0.6)
        tp_step = tp_ylim / 4
        left_labels = [
            fch.ChartAxisLabel(
                value=round(tp_step * i, 1),
                label=ft.Text(f"{tp_step * i:.1f}", size=9, color=_CYAN),
            )
            for i in range(5)
        ]

        # Right Y-axis (WIP, show real values)
        wip_ylim = max(wip_max * 1.3, 10)
        wip_step_val = wip_ylim / 4
        right_labels = [
            fch.ChartAxisLabel(
                value=round(wip_step_val * i * wip_scale, 1),
                label=ft.Text(str(int(wip_step_val * i)), size=9, color=_AMBER),
            )
            for i in range(5)
        ]

        return fch.LineChart(
            data_series=[tp_line, wip_line],
            tooltip=fch.LineChartTooltip(
                bgcolor=_BG_SECTION,
                border_side=ft.BorderSide(1, _BORDER),
                border_radius=6,
                padding=ft.Padding(8, 6, 8, 6),
                fit_inside_horizontally=True,
                fit_inside_vertically=True,
            ),
            bottom_axis=fch.ChartAxis(labels=bottom_labels, label_size=30),
            left_axis=fch.ChartAxis(labels=left_labels, label_size=50),
            right_axis=fch.ChartAxis(labels=right_labels, label_size=50),
            horizontal_grid_lines=fch.ChartGridLines(color=_MPL_GRID, width=0.5),
            border=ft.Border(
                bottom=ft.BorderSide(1, _BORDER),
                left=ft.BorderSide(1, _BORDER),
            ),
            bgcolor=_BG_CARD,
            min_y=0,
            max_y=float(tp_ylim),
            min_x=0,
            max_x=float(n - 1),
            height=280,
            interactive=True,
        )

    # ── Backlog Aging (Flet chart — median + IQR) ─────────────────────────

    def _chart_backlog_aging_flet(self, series: list[dict]) -> ft.Control:
        """Backlog median age with Q1–Q3 IQR band as native Flet LineChart."""
        from datetime import datetime

        filtered = [r for r in series if r.get("median_age", 0) > 0]
        if len(filtered) < 2:
            return self._no_data(180)

        dates = [datetime.strptime(r["date"], "%Y-%m-%d") for r in filtered]
        medians = [r["median_age"] for r in filtered]
        p25s = [r["p25_age"] for r in filtered]
        p75s = [r["p75_age"] for r in filtered]
        n = len(filtered)

        ymax = max(p75s, default=10.0)
        ylim = max(ymax * 1.2, 10.0)

        # IQR band (same technique as cycle time trend)
        iqr_upper = fch.LineChartData(
            points=[
                fch.LineChartDataPoint(x=float(i), y=p75s[i])
                for i in range(n)
            ],
            stroke_width=0,
            color="transparent",
            below_line_bgcolor=ft.Colors.with_opacity(0.15, _AMBER),
            below_line_cutoff_y=0,
            point=False,
        )
        iqr_lower = fch.LineChartData(
            points=[
                fch.LineChartDataPoint(x=float(i), y=p25s[i])
                for i in range(n)
            ],
            stroke_width=0,
            color="transparent",
            below_line_bgcolor=_BG_CARD,
            below_line_cutoff_y=0,
            point=False,
        )

        # Median line
        median_line = fch.LineChartData(
            points=[
                fch.LineChartDataPoint(
                    x=float(i), y=medians[i],
                    tooltip=fch.LineChartDataPointTooltip(
                        text=f"{dates[i].strftime('%b %d')}: {medians[i]:.0f}d  [{p25s[i]:.0f}, {p75s[i]:.0f}]",
                        text_style=ft.TextStyle(size=11, color=_TEXT, weight=ft.FontWeight.W_500),
                    ),
                )
                for i in range(n)
            ],
            stroke_width=2.5,
            color=_AMBER,
            curved=False,
            point=False,
            selected_point=fch.ChartCirclePoint(radius=4, color=_AMBER),
        )

        # X-axis labels
        step = max(1, n // 7)
        bottom_labels = [
            fch.ChartAxisLabel(
                value=float(i),
                label=ft.Text(dates[i].strftime("%b %d"), size=10, color=_TEXT_MUT),
            )
            for i in range(0, n, step)
        ]

        # Y-axis labels
        y_step = max(1, int(ylim) // 5)
        left_labels = [
            fch.ChartAxisLabel(
                value=float(v),
                label=ft.Text(f"{v}d", size=10, color=_TEXT_MUT),
            )
            for v in range(0, int(ylim) + 1, y_step)
        ]

        return fch.LineChart(
            data_series=[iqr_upper, iqr_lower, median_line],
            tooltip=fch.LineChartTooltip(
                bgcolor=_BG_SECTION,
                border_side=ft.BorderSide(1, _BORDER),
                border_radius=6,
                padding=ft.Padding(8, 6, 8, 6),
                fit_inside_horizontally=True,
                fit_inside_vertically=True,
            ),
            bottom_axis=fch.ChartAxis(labels=bottom_labels, label_size=30),
            left_axis=fch.ChartAxis(labels=left_labels, label_size=50),
            horizontal_grid_lines=fch.ChartGridLines(color=_MPL_GRID, width=0.5),
            border=ft.Border(
                bottom=ft.BorderSide(1, _BORDER),
                left=ft.BorderSide(1, _BORDER),
            ),
            bgcolor=_BG_CARD,
            min_y=0,
            max_y=float(ylim),
            min_x=0,
            max_x=float(n - 1),
            height=280,
            interactive=True,
        )

    # ── Cycle time histogram ──────────────────────────────────────────────────

    def _chart_ct_histogram(self, hist: dict) -> ft.Control:
        total = sum(hist.values())
        if total == 0:
            return self._no_data(140)

        labels     = ["1d", "2d", "3d", "4d", "5d", "6+d"]
        keys       = ["1",  "2",  "3",  "4",  "5",  "6+"]
        values     = [hist.get(k, 0) for k in keys]
        bar_colors = [_GREEN, _GREEN, _GREEN, _AMBER, _AMBER, _RED]
        max_val    = max(values) if values else 1

        groups = []
        for i, (label, val, color) in enumerate(zip(labels, values, bar_colors)):
            groups.append(
                fch.BarChartGroup(
                    x=i,
                    rods=[
                        fch.BarChartRod(
                            from_y=0,
                            to_y=float(val),
                            width=28,
                            color=color,
                            border_radius=ft.BorderRadius(4, 4, 0, 0),
                            tooltip=f"{label}: {val}",
                        ),
                    ],
                )
            )

        bottom_axis = fch.ChartAxis(
            labels=[
                fch.ChartAxisLabel(
                    value=float(i),
                    label=ft.Text(label, size=11, color=_TEXT_MUT),
                )
                for i, label in enumerate(labels)
            ],
            label_size=28,
        )

        left_axis = fch.ChartAxis(
            label_size=36,
            labels=[
                fch.ChartAxisLabel(
                    value=float(v),
                    label=ft.Text(str(v), size=10, color=_TEXT_MUT),
                )
                for v in range(0, int(max_val * 1.3) + 2, max(1, int(max_val * 1.3 + 1) // 6))
            ],
        )

        return fch.BarChart(
            groups=groups,
            bottom_axis=bottom_axis,
            left_axis=left_axis,
            horizontal_grid_lines=fch.ChartGridLines(
                color=_MPL_GRID, width=0.5,
            ),
            border=ft.Border(
                bottom=ft.BorderSide(1, _BORDER),
                left=ft.BorderSide(1, _BORDER),
            ),
            bgcolor=_BG_CARD,
            min_y=0,
            max_y=float(max_val * 1.3),
            group_spacing=12,
            height=280,
            interactive=True,
        )

    # ── Backlog aging mini-chart ──────────────────────────────────────────────

    def _chart_backlog_aging(self, series: list[dict]) -> ft.Control:
        if not series or all(r["max_age"] == 0 for r in series):
            return self._no_data(120)

        from datetime import datetime

        step    = max(1, len(series) // 7)
        sampled = series[::step]
        sdates  = [datetime.strptime(r["date"], "%Y-%m-%d") for r in sampled]

        with plt.style.context("dark_background"):
            fig, ax = plt.subplots(figsize=(3.0, 1.8))
            fig.patch.set_facecolor(_MPL_BG)
            ax.set_facecolor(_MPL_BG)
            ax.plot(sdates, [r["max_age"] for r in sampled],  # type: ignore[arg-type]
                    color=_RED,   linewidth=1.5, label="max")
            ax.plot(sdates, [r["avg_age"] for r in sampled],  # type: ignore[arg-type]
                    color=_AMBER, linewidth=1.5, label="avg")
            ax.plot(sdates, [r["min_age"] for r in sampled],  # type: ignore[arg-type]
                    color=_CYAN,  linewidth=1.5, label="min")
            ax.yaxis.grid(True, color=_MPL_GRID, linewidth=0.5)
            ax.set_axisbelow(True)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d"))
            ax.xaxis.set_major_locator(mticker.MaxNLocator(4, integer=True))
            ax.tick_params(axis="both", labelsize=7)
            ax.set_ylabel("days", fontsize=7)
            ax.legend(fontsize=7, loc="upper left", framealpha=0.3)
            plt.tight_layout(pad=0.3)

        return ft.Image(src=self._fig_to_b64(fig), fit=ft.BoxFit.CONTAIN,
                        border_radius=4)

    # ── Backlog aging (matplotlib, median + IQR) ─────────────────────────────

    def _chart_backlog_aging_mpl(self, series: list[dict]) -> ft.Control:
        """Backlog median age with P25–P75 IQR shaded band (matplotlib)."""
        filtered = [r for r in series if r.get("median_age", 0) > 0]
        if len(filtered) < 2:
            return self._no_data(180)

        from datetime import datetime

        dates   = [datetime.strptime(r["date"], "%Y-%m-%d") for r in filtered]
        medians = [r["median_age"] for r in filtered]
        p25s    = [r["p25_age"] for r in filtered]
        p75s    = [r["p75_age"] for r in filtered]

        with plt.style.context("dark_background"):
            fig, ax = plt.subplots(figsize=(7.5, 2.8))
            fig.patch.set_facecolor(_MPL_BG)
            ax.set_facecolor(_MPL_BG)

            # IQR shaded band
            _GRAY = "#6E7681"
            ax.fill_between(dates, p25s, p75s, alpha=0.25, color=_GRAY,
                            label="IQR (P25–P75)", zorder=2)
            # Median line
            ax.plot(dates, medians, color=_GRAY, linewidth=2.0,
                    label="Median", zorder=3)
            # A6 threshold lines
            ax.axhline(y=settings.HEALTH_BACKLOG_AGE_WATCH_DAYS, color=_AMBER,
                       linestyle="--", linewidth=1, alpha=0.6,
                       label=f"Watch (> {settings.HEALTH_BACKLOG_AGE_WATCH_DAYS:.0f}d)")
            ax.axhline(y=settings.HEALTH_BACKLOG_AGE_BAD_DAYS, color=_RED,
                       linestyle="--", linewidth=1, alpha=0.6,
                       label=f"Bad (> {settings.HEALTH_BACKLOG_AGE_BAD_DAYS:.0f}d)")

            ax.set_ylabel("Age (days)", fontsize=8, color=_TEXT_MUT)
            ax.tick_params(axis="y", labelsize=8, colors=_TEXT_MUT)
            ymax = max(p75s, default=10.0)
            ax.set_ylim(0, max(ymax * 1.2, settings.HEALTH_BACKLOG_AGE_BAD_DAYS * 1.3))
            ax.yaxis.grid(True, color=_MPL_GRID, linewidth=0.5, zorder=0)
            ax.set_axisbelow(True)
            for spine in ax.spines.values():
                spine.set_edgecolor(_BORDER)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
            ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0))
            fig.autofmt_xdate(rotation=20, ha="right")
            ax.tick_params(axis="x", labelsize=8, colors=_TEXT_MUT)
            ax.legend(fontsize=8, loc="upper left", framealpha=0.3)
            plt.tight_layout(pad=0.4)

        return ft.Image(src=self._fig_to_b64(fig), fit=ft.BoxFit.CONTAIN,
                        border_radius=6)

    # ── CFD (matplotlib) ──────────────────────────────────────────────────────

    def _chart_cfd_mpl(self, cfd: list[dict]) -> ft.Control:
        """Cumulative Flow Diagram rendered via matplotlib stackplot."""
        if len(cfd) < 2:
            return self._no_data(180)

        from datetime import datetime
        from kanbai.ui import theme as _theme

        dates = [datetime.strptime(r["date"], "%Y-%m-%d") for r in cfd]
        _STATUS = _theme.STATUS_COLORS

        with plt.style.context("dark_background"):
            fig, ax = plt.subplots(figsize=(7.5, 2.8))
            fig.patch.set_facecolor(_MPL_BG)
            ax.set_facecolor(_MPL_BG)

            ax.stackplot(
                dates,
                [r["backlog"] for r in cfd],
                [r["in_progress"] for r in cfd],
                [r["review"] for r in cfd],
                [r["done"] for r in cfd],
                labels=["Backlog", "In Progress", "Review", "Done"],
                colors=[
                    _STATUS.get("backlog", "#6E7681"),
                    _STATUS.get("in_progress", "#2EA6A0"),
                    _STATUS.get("review", "#A371F7"),
                    _STATUS.get("done", _GREEN),
                ],
                alpha=0.75,
            )

            ax.set_ylabel("Cards", fontsize=8, color=_TEXT_MUT)
            ax.tick_params(axis="y", labelsize=8, colors=_TEXT_MUT)
            ax.yaxis.grid(True, color=_MPL_GRID, linewidth=0.5, zorder=0)
            ax.set_axisbelow(True)
            for spine in ax.spines.values():
                spine.set_edgecolor(_BORDER)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
            ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0))
            fig.autofmt_xdate(rotation=20, ha="right")
            ax.tick_params(axis="x", labelsize=8, colors=_TEXT_MUT)
            ax.legend(fontsize=7, loc="upper left", framealpha=0.3)
            plt.tight_layout(pad=0.4)

        return ft.Image(src=self._fig_to_b64(fig), fit=ft.BoxFit.CONTAIN,
                        border_radius=6)

    # ── Cycle time trend (matplotlib) ─────────────────────────────────────────

    def _chart_ct_weekly_trend_mpl(self, weekly: list[dict]) -> ft.Control:
        """14-day rolling median with P25–P75 IQR band, matplotlib."""
        from datetime import datetime

        filtered = [r for r in weekly if r["median"] is not None]
        if len(filtered) < 2:
            return self._no_data(180)

        dates   = [datetime.strptime(r["date"], "%Y-%m-%d") for r in filtered]
        medians = [r["median"] for r in filtered]
        p25s    = [r["p25"] if r["p25"] is not None else r["median"] for r in filtered]
        p75s    = [r["p75"] if r["p75"] is not None else r["median"] for r in filtered]

        with plt.style.context("dark_background"):
            fig, ax = plt.subplots(figsize=(7.5, 2.8))
            fig.patch.set_facecolor(_MPL_BG)
            ax.set_facecolor(_MPL_BG)

            ax.fill_between(dates, p25s, p75s, alpha=0.2, color=_GREEN,
                            label="IQR (P25–P75)", zorder=2)
            ax.plot(dates, medians, color=_GREEN, linewidth=2.0,
                    label="Median", zorder=3)

            # Threshold lines: 3d target, A4 watch/bad
            ax.axhline(y=3.0, color=_TEXT_MUT, linestyle="--", linewidth=1,
                       alpha=0.5, label="3d target")
            ax.axhline(y=settings.HEALTH_CYCLE_TIME_WATCH_DAYS, color=_AMBER,
                       linestyle="--", linewidth=1, alpha=0.6,
                       label=f"Watch (≥{settings.HEALTH_CYCLE_TIME_WATCH_DAYS:.0f}d)")
            ax.axhline(y=settings.HEALTH_CYCLE_TIME_BAD_DAYS, color=_RED,
                       linestyle="--", linewidth=1, alpha=0.6,
                       label=f"Bad (≥{settings.HEALTH_CYCLE_TIME_BAD_DAYS:.0f}d)")

            ax.set_ylabel("Cycle time (days)", fontsize=8, color=_TEXT_MUT)
            ax.tick_params(axis="y", labelsize=8, colors=_TEXT_MUT)
            ymax = max(p75s, default=3.0)
            ax.set_ylim(0, max(ymax * 1.25, 7.5))
            ax.yaxis.grid(True, color=_MPL_GRID, linewidth=0.5, zorder=0)
            ax.set_axisbelow(True)
            for spine in ax.spines.values():
                spine.set_edgecolor(_BORDER)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
            ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0))
            fig.autofmt_xdate(rotation=20, ha="right")
            ax.tick_params(axis="x", labelsize=8, colors=_TEXT_MUT)
            ax.legend(fontsize=7, loc="upper left", framealpha=0.3)
            plt.tight_layout(pad=0.4)

        return ft.Image(src=self._fig_to_b64(fig), fit=ft.BoxFit.CONTAIN,
                        border_radius=6)

    # ── Cycle time histogram (matplotlib) ─────────────────────────────────────

    def _chart_ct_histogram_mpl(self, hist: dict) -> ft.Control:
        """Cycle time distribution as a bar chart, matplotlib."""
        total = sum(hist.values())
        if total == 0:
            return self._no_data(180)

        labels     = ["1d", "2d", "3d", "4d", "5d", "6+d"]
        keys       = ["1",  "2",  "3",  "4",  "5",  "6+"]
        values     = [hist.get(k, 0) for k in keys]
        bar_colors = [_GREEN, _GREEN, _GREEN, _AMBER, _AMBER, _RED]

        with plt.style.context("dark_background"):
            fig, ax = plt.subplots(figsize=(7.5, 2.8))
            fig.patch.set_facecolor(_MPL_BG)
            ax.set_facecolor(_MPL_BG)

            x = range(len(labels))
            bars = ax.bar(x, values, color=bar_colors, width=0.6,
                          edgecolor=_MPL_BG, linewidth=1.5, zorder=3)

            # Value labels on top of bars
            for bar, val in zip(bars, values):
                if val > 0:
                    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                            str(val), ha="center", va="bottom",
                            fontsize=8, color=_TEXT_MUT, weight="bold")

            ax.set_xticks(list(x))
            ax.set_xticklabels(labels)
            ax.set_ylabel("Cards", fontsize=8, color=_TEXT_MUT)
            ax.tick_params(axis="both", labelsize=8, colors=_TEXT_MUT)
            max_val = max(values) if values else 1
            ax.set_ylim(0, max_val * 1.3)
            ax.yaxis.grid(True, color=_MPL_GRID, linewidth=0.5, zorder=0)
            ax.set_axisbelow(True)
            for spine in ax.spines.values():
                spine.set_edgecolor(_BORDER)

            plt.tight_layout(pad=0.4)

        return ft.Image(src=self._fig_to_b64(fig), fit=ft.BoxFit.CONTAIN,
                        border_radius=6)

    # ── Donut chart ───────────────────────────────────────────────────────────

    def _chart_donut(
        self, data: dict[str, int], colors: dict[str, str], total: int
    ) -> ft.Control:
        if total == 0:
            return ft.Container(
                content=ft.Text("—", size=20, color=_TEXT_DIM),
                width=100, height=100,
                alignment=ft.Alignment(0, 0),
            )
        keys = [k for k in data if data[k] > 0]
        vals = [data[k] for k in keys]
        clrs = [colors.get(k, _TEXT_DIM) for k in keys]

        with plt.style.context("dark_background"):
            fig, ax = plt.subplots(figsize=(1.8, 1.8))
            fig.patch.set_facecolor(_MPL_BG)
            ax.set_facecolor(_MPL_BG)
            ax.pie(
                vals, colors=clrs, startangle=90, counterclock=False,
                wedgeprops={"linewidth": 1.5, "edgecolor": _MPL_BG},
            )
            ax.add_patch(
                mpatches.Circle((0, 0), 0.58, color=_MPL_BG, zorder=10)
            )
            plt.tight_layout(pad=0)

        return ft.Image(src=self._fig_to_b64(fig), width=100, height=100,
                        fit=ft.BoxFit.CONTAIN)

    # ── CFD ───────────────────────────────────────────────────────────────────

    def _chart_cfd(self, cfd: list[dict]) -> ft.Control:
        if len(cfd) < 2:
            return self._no_data(180)

        from datetime import datetime
        n = len(cfd)
        dates = [datetime.strptime(r["date"], "%Y-%m-%d") for r in cfd]

        # Pre-compute cumulative stacks (bottom to top: Done, Review, In Progress, Backlog)
        layers = [
            ("Backlog",     [r["backlog"]     for r in cfd], "#6E7681"),
            ("In Progress", [r["in_progress"] for r in cfd], "#2EA6A0"),
            ("Review",      [r["review"]      for r in cfd], "#A371F7"),
            ("Done",        [r["done"]        for r in cfd], "#56D364"),
        ]

        # Build cumulative sums
        cumulative: list[list[float]] = []
        running = [0.0] * n
        for _label, vals, _color in layers:
            running = [running[i] + vals[i] for i in range(n)]
            cumulative.append(list(running))

        max_y = max(cumulative[-1]) if cumulative[-1] else 10

        # Draw from top layer down so each fill covers layers below
        series: list[fch.LineChartData] = []
        top_idx = len(layers) - 1
        for idx in range(top_idx, -1, -1):
            label, _vals, color = layers[idx]
            cum = cumulative[idx]
            series.append(
                fch.LineChartData(
                    points=[
                        fch.LineChartDataPoint(
                            x=float(i), y=cum[i],
                            tooltip=(
                                f"{label}: {layers[idx][1][i]}\n{dates[i].strftime('%b %d')}"
                                if idx == 0
                                else f"{label}: {layers[idx][1][i]}"
                            ),
                        )
                        for i in range(n)
                    ],
                    stroke_width=1.5,
                    color=color,
                    below_line_bgcolor=ft.Colors.with_opacity(0.7, color),
                    below_line_cutoff_y=0,
                    curved=False,
                    point=fch.ChartCirclePoint(radius=0.5, color="transparent"),
                    selected_point=fch.ChartCirclePoint(radius=3, color=color),
                )
            )

        # X-axis labels
        step = max(1, n // 8)
        bottom_labels = [
            fch.ChartAxisLabel(
                value=float(i),
                label=ft.Container(
                    content=ft.Text(
                        dates[i].strftime("%b %d"), size=10, color=_TEXT_MUT,
                    ),
                    padding=ft.Padding(0, 4, 0, 0),
                ),
            )
            for i in range(0, n, step)
        ]

        # Y-axis labels
        y_step = max(1, int(max_y) // 5)
        left_labels = [
            fch.ChartAxisLabel(
                value=float(v),
                label=ft.Text(str(v), size=10, color=_TEXT_MUT),
            )
            for v in range(0, int(max_y) + y_step + 1, y_step)
        ]

        return fch.LineChart(
            data_series=series,
            tooltip=fch.LineChartTooltip(
                bgcolor=_BG_SECTION,
                border_side=ft.BorderSide(1, _BORDER),
                border_radius=6,
                padding=ft.Padding(8, 6, 8, 6),
                fit_inside_horizontally=True,
                fit_inside_vertically=True,
            ),
            bottom_axis=fch.ChartAxis(labels=bottom_labels, label_size=28),
            left_axis=fch.ChartAxis(labels=left_labels, label_size=36),
            horizontal_grid_lines=fch.ChartGridLines(color=_MPL_GRID, width=0.5),
            border=ft.Border(
                bottom=ft.BorderSide(1, _BORDER),
                left=ft.BorderSide(1, _BORDER),
            ),
            bgcolor=_BG_CARD,
            min_y=0,
            max_y=float(max_y * 1.1),
            min_x=0,
            max_x=float(n - 1),
            height=280,
            interactive=True,
        )

    # ── Cycle time rolling trend ──────────────────────────────────────────────────

    def _chart_ct_weekly_trend(self, weekly: list[dict]) -> ft.Control:
        """14-day rolling median with Q1–Q3 IQR band, daily x-axis."""
        import math
        from datetime import datetime

        # Filter to only entries with actual data
        filtered = [r for r in weekly if r["median"] is not None]
        if len(filtered) < 2:
            return self._no_data(160)

        dates   = [datetime.strptime(r["date"], "%Y-%m-%d") for r in filtered]
        medians = [r["median"] for r in filtered]
        p25s    = [r["p25"] if r["p25"] is not None else r["median"] for r in filtered]
        p75s    = [r["p75"] if r["p75"] is not None else r["median"] for r in filtered]

        ymax = max(p75s, default=3.0)
        ylim = max(ymax * 1.25, 7.5)
        n = len(filtered)

        # IQR band — upper and lower lines
        iqr_upper = fch.LineChartData(
            points=[
                fch.LineChartDataPoint(x=float(i), y=p75s[i])
                for i in range(n)
            ],
            stroke_width=0,
            color="transparent",
            below_line_bgcolor=ft.Colors.with_opacity(0.15, _CYAN),
            below_line_cutoff_y=0,
            point=False,
        )
        iqr_lower = fch.LineChartData(
            points=[
                fch.LineChartDataPoint(x=float(i), y=p25s[i])
                for i in range(n)
            ],
            stroke_width=0,
            color="transparent",
            below_line_bgcolor=_BG_CARD,
            below_line_cutoff_y=0,
            point=False,
        )

        # Median line with compact tooltips
        median_line = fch.LineChartData(
            points=[
                fch.LineChartDataPoint(
                    x=float(i), y=medians[i],
                    tooltip=fch.LineChartDataPointTooltip(
                        text=f"{dates[i].strftime('%b %d')}: {medians[i]:.1f}d  [{p25s[i]:.1f}, {p75s[i]:.1f}]",
                        text_style=ft.TextStyle(size=11, color=_TEXT, weight=ft.FontWeight.W_500),
                    ),
                )
                for i in range(n)
            ],
            stroke_width=2.5,
            color=_CYAN,
            curved=False,
            point=False,
            selected_point=fch.ChartCirclePoint(
                radius=4, color=_CYAN,
            ),
        )

        # Reference lines at 3d, 5d, 6d
        ref_lines = []
        for y_val, color in [(3.0, "#FFFFFF"), (5.0, _AMBER), (6.0, _RED)]:
            ref_lines.append(
                fch.LineChartData(
                    points=[
                        fch.LineChartDataPoint(x=0, y=y_val),
                        fch.LineChartDataPoint(x=float(n - 1), y=y_val),
                    ],
                    stroke_width=1,
                    color=ft.Colors.with_opacity(0.5, color),
                    dash_pattern=[6, 4],
                    point=False,
                )
            )

        # X-axis labels — show ~7 evenly spaced dates
        step = max(1, n // 7)
        bottom_labels = [
            fch.ChartAxisLabel(
                value=float(i),
                label=ft.Container(
                    content=ft.Text(
                        dates[i].strftime("%b %d"), size=10, color=_TEXT_MUT,
                    ),
                    padding=ft.Padding(0, 4, 0, 0),
                ),
            )
            for i in range(0, n, step)
        ]

        # Y-axis labels
        y_step = max(1, int(ylim) // 5)
        left_labels = [
            fch.ChartAxisLabel(
                value=float(v),
                label=ft.Text(str(v), size=10, color=_TEXT_MUT),
            )
            for v in range(0, int(ylim) + 1, y_step)
        ]

        # Right-side reference labels
        right_labels = []
        for y_val, color, label in [(3.0, "#FFFFFF", "3d"), (5.0, _AMBER, "5d"), (6.0, _RED, "6d")]:
            right_labels.append(
                fch.ChartAxisLabel(
                    value=y_val,
                    label=ft.Text(label, size=10, color=color, weight=ft.FontWeight.BOLD),
                )
            )

        return fch.LineChart(
            data_series=[iqr_upper, iqr_lower, median_line] + ref_lines,
            tooltip=fch.LineChartTooltip(
                bgcolor=_BG_SECTION,
                border_side=ft.BorderSide(1, _BORDER),
                border_radius=6,
                padding=ft.Padding(8, 6, 8, 6),
                fit_inside_horizontally=True,
                fit_inside_vertically=True,
            ),
            bottom_axis=fch.ChartAxis(labels=bottom_labels, label_size=28),
            left_axis=fch.ChartAxis(labels=left_labels, label_size=36),
            right_axis=fch.ChartAxis(labels=right_labels, label_size=36),
            horizontal_grid_lines=fch.ChartGridLines(color=_MPL_GRID, width=0.5),
            border=ft.Border(
                bottom=ft.BorderSide(1, _BORDER),
                left=ft.BorderSide(1, _BORDER),
            ),
            bgcolor=_BG_CARD,
            min_y=0,
            max_y=float(ylim),
            min_x=0,
            max_x=float(n - 1),
            height=280,
            interactive=True,
        )
