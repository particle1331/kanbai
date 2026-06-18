"""board/health.py — signal-based project health computation.

Project health is derived from the 8 A-series stat cards shown on the
analytics Health page. Every signal maps to one of three bands:

  "good"  – within healthy thresholds (green)
  "watch" – approaching a threshold; worth monitoring (amber)
  "bad"   – threshold exceeded; needs attention (red)

The overall health verdict is the worst band across all 8 signals.

Public API
----------
compute_signals(epic=None)  → list[dict]
    Return the full list of health signals (one dict per signal).

project_health(epic=None)   → dict
    Return the overall verdict plus all signals and a filtered list of warnings.
"""

from __future__ import annotations

import datetime as dt

from board import settings
from board.storage import sqlite_cache


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _due_band(due_3d: int, due_1d: int) -> str:
    """A8 two-tier banding: bad if due_3d >= DUE_BAD OR due_1d >= 1."""
    if due_3d >= settings.HEALTH_DUE_BAD or due_1d >= 1:
        return "bad"
    if due_3d >= settings.HEALTH_DUE_WATCH:
        return "watch"
    return "good"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_signals(epic: str | None = None) -> list[dict]:
    """Return all 8 monitored health signals (A1–A8) for the board.

    Each signal dict contains:
      key     – stable identifier string
      label   – short human-readable name
      value   – raw numeric value (float | int | None)
      unit    – "days" | "cards" | "ratio"
      band    – "good" | "watch" | "bad"
      message – one-line human-readable description
    """
    signals: list[dict] = []

    now = dt.datetime.now(dt.timezone.utc)

    # ── A1: Throughput 7d (completed ÷ started) ───────────────────────────────
    flow_7 = sqlite_cache.transition_flow_counts(
        (now - dt.timedelta(days=7)).isoformat(), now.isoformat(), epic=epic
    )
    started_7  = flow_7.get("started", 0)
    completed_7 = flow_7.get("completed", 0)
    tp7: float | None = (completed_7 / started_7) if started_7 > 0 else None

    signals.append({
        "key": "throughput_7d",
        "label": "Throughput 7d",
        "value": round(tp7, 2) if tp7 is not None else None,
        "unit": "ratio",
        "band": settings.band_inverted(tp7, settings.HEALTH_THROUGHPUT_WATCH, settings.HEALTH_THROUGHPUT_BAD),
        "message": (
            f"Throughput {tp7:.2f} ({completed_7} completed / {started_7} started)"
            if tp7 is not None else "No starts in 7d (throughput undefined)"
        ),
    })

    # ── A2: WIP Count (in_progress + review) ─────────────────────────────────
    by_lane = sqlite_cache.count_cards_by_lane(epic=epic)
    wip_count = by_lane.get("in_progress", 0) + by_lane.get("review", 0)

    signals.append({
        "key": "wip_count",
        "label": "WIP Count",
        "value": wip_count,
        "unit": "cards",
        "band": settings.band(wip_count, settings.HEALTH_WIP_WATCH, settings.HEALTH_WIP_BAD),
        "message": (
            f"WIP {wip_count} cards "
            f"({by_lane.get('in_progress', 0)} in progress + {by_lane.get('review', 0)} review)"
        ),
    })

    # ── A3: WIP Median Age (days) ─────────────────────────────────────────────
    wip_age = sqlite_cache.wip_age_stats(epic=epic)
    wip_median_age: float | None = wip_age.get("median_age")

    signals.append({
        "key": "wip_median_age",
        "label": "WIP Median Age",
        "value": wip_median_age,
        "unit": "days",
        "band": settings.band(
            wip_median_age,
            settings.HEALTH_WIP_AGE_WATCH_DAYS,
            settings.HEALTH_WIP_AGE_BAD_DAYS,
        ),
        "message": (
            f"WIP median age {wip_median_age:.0f}d"
            if wip_median_age is not None else "No WIP cards"
        ),
    })

    # ── A4: Median Cycle Time 7d (days) ──────────────────────────────────────
    ct_7d = sqlite_cache.median_cycle_time_days(7, epic=epic)
    median_ct: float | None = ct_7d.get("median")

    # >= triggers for cycle time (plan says ">= 5d watch, >= 6d bad")
    if median_ct is None:
        ct_band = "good"
    elif median_ct >= settings.HEALTH_CYCLE_TIME_BAD_DAYS:
        ct_band = "bad"
    elif median_ct >= settings.HEALTH_CYCLE_TIME_WATCH_DAYS:
        ct_band = "watch"
    else:
        ct_band = "good"

    signals.append({
        "key": "median_cycle_time",
        "label": "Median Cycle Time",
        "value": median_ct,
        "unit": "days",
        "band": ct_band,
        "message": (
            f"Median cycle time {median_ct:.0f}d (7d window)"
            if median_ct is not None else "No completions in 7d"
        ),
    })

    # ── A5: Pull Rate 7d (started ÷ created) ──────────────────────────────────
    created_7 = flow_7.get("created", 0)
    pull_7: float | None = (started_7 / created_7) if created_7 > 0 else None

    signals.append({
        "key": "pull_rate_7d",
        "label": "Pull Rate 7d",
        "value": round(pull_7, 2) if pull_7 is not None else None,
        "unit": "ratio",
        "band": settings.band_inverted(pull_7, settings.HEALTH_PULL_RATE_WATCH, settings.HEALTH_PULL_RATE_BAD),
        "message": (
            f"Pull rate {pull_7:.2f} ({started_7} started / {created_7} created)"
            if pull_7 is not None else "No cards created in 7d"
        ),
    })

    # ── A6: Median Backlog Age (days) ─────────────────────────────────────────
    backlog_age = sqlite_cache.backlog_age_stats(epic=epic)
    backlog_median_age: float | None = backlog_age.get("median_age")

    signals.append({
        "key": "backlog_median_age",
        "label": "Median Backlog Age",
        "value": backlog_median_age,
        "unit": "days",
        "band": settings.band(
            backlog_median_age,
            settings.HEALTH_BACKLOG_AGE_WATCH_DAYS,
            settings.HEALTH_BACKLOG_AGE_BAD_DAYS,
        ),
        "message": (
            f"Backlog median age {backlog_median_age:.0f}d"
            if backlog_median_age is not None else "No backlog cards"
        ),
    })

    # ── A7: Blockers (WIP cards with ≥1 blocks link) ──────────────────────────
    blocker_count = sqlite_cache.count_blocker_cards(epic=epic)

    signals.append({
        "key": "blockers",
        "label": "Blockers",
        "value": blocker_count,
        "unit": "cards",
        "band": settings.band(
            blocker_count,
            settings.HEALTH_BLOCKER_WATCH,
            settings.HEALTH_BLOCKER_BAD,
        ),
        "message": (
            f"{blocker_count} WIP card{'s' if blocker_count != 1 else ''} blocking progress"
            if blocker_count else "No blockers"
        ),
    })

    # ── A8: Due in 3d ─────────────────────────────────────────────────────────
    due_3d = sqlite_cache.count_due_within(settings.DEADLINE_SOON_DAYS, epic=epic)
    due_1d = sqlite_cache.count_due_within(settings.DEADLINE_URGENT_DAYS, epic=epic)

    signals.append({
        "key": "due_3d",
        "label": f"Due in {settings.DEADLINE_SOON_DAYS}d",
        "value": due_3d,
        "unit": "cards",
        "band": _due_band(due_3d, due_1d),
        "message": (
            f"{due_3d} card{'s' if due_3d != 1 else ''} due within {settings.DEADLINE_SOON_DAYS}d"
            f"{f' ({due_1d} due within {settings.DEADLINE_URGENT_DAYS}d)' if due_1d else ''}"
            if due_3d else "No upcoming deadlines"
        ),
    })

    return signals


def project_health(epic: str | None = None) -> dict:
    """Compute overall project health from all 8 A-series signals.

    Returns a dict with:
      status   – "good" | "watch" | "bad"
      label    – human-readable verdict (e.g. "Watch closely")
      color    – hex display color
      signals  – full list of signal dicts (see compute_signals)
      warnings – subset of signals where band != "good"
    """
    signals = compute_signals(epic=epic)
    worst = max(
        (s["band"] for s in signals),
        key=lambda b: settings._BAND_RANK[b],
        default="good",
    )
    return {
        "status": worst,
        "label": settings._HEALTH_LABELS[worst],
        "color": settings._HEALTH_COLORS[worst],
        "signals": signals,
        "warnings": [s for s in signals if s["band"] != "good"],
    }
