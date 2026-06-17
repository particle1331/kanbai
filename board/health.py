"""board/health.py — signal-based project health computation.

Project health is derived from a set of monitored *signals*, each corresponding
to a metric the analytics page already warns about.  Every signal maps to one
of three bands:

  "good"  – within healthy thresholds (green)
  "watch" – approaching a threshold; worth monitoring (amber)
  "bad"   – threshold exceeded; needs attention (red)

The overall health verdict is the worst band across all signals.

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

def _ratio_low_band(value: float | None, watch: float, bad: float) -> str:
    """Band for metrics where a LOW value is bad (inverted scale).

    None (no data) → "good".
    value < bad    → "bad"
    value < watch  → "watch"
    else           → "good"
    """
    if value is None:
        return "good"
    if value < bad:
        return "bad"
    if value < watch:
        return "watch"
    return "good"


def _finish_ratio_band(value: float | None) -> str:
    """Finish ratio is healthy near 1.0 (WIP stable).

    Outside the outer window (bad range) → "bad".
    Outside the inner window (watch range) → "watch".
    Inside both → "good".
    None (no data) → "good".
    """
    if value is None:
        return "good"
    if (value < settings.HEALTH_FINISH_RATIO_BAD_LOW
            or value > settings.HEALTH_FINISH_RATIO_BAD_HIGH):
        return "bad"
    if (value < settings.HEALTH_FINISH_RATIO_WATCH_LOW
            or value > settings.HEALTH_FINISH_RATIO_WATCH_HIGH):
        return "watch"
    return "good"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_signals(epic: str | None = None) -> list[dict]:
    """Return all monitored health signals for the board (or a specific epic).

    Each signal dict contains:
      key     – stable identifier string
      label   – short human-readable name
      value   – raw numeric value (float | int | None)
      unit    – "days" | "cards" | "ratio"
      band    – "good" | "watch" | "bad"
      message – one-line human-readable description
    """
    signals: list[dict] = []

    # ── Card age signals ──────────────────────────────────────────────────────
    age_stats = sqlite_cache.open_card_age_stats(epic=epic)
    avg_age: float | None = age_stats.get("avg_age")
    max_age: float | None = age_stats.get("max_age")
    open_count: int = age_stats.get("count", 0)

    signals.append({
        "key": "avg_age",
        "label": "Average card age",
        "value": avg_age,
        "unit": "days",
        "band": settings.band(avg_age, settings.HEALTH_AVG_AGE_WATCH_DAYS, settings.HEALTH_AVG_AGE_BAD_DAYS),
        "message": (
            f"Avg open-card age {avg_age:.0f}d"
            if avg_age is not None else "No open cards"
        ),
    })

    signals.append({
        "key": "max_age",
        "label": "Oldest card age",
        "value": max_age,
        "unit": "days",
        "band": settings.band(max_age, settings.HEALTH_MAX_AGE_WATCH_DAYS, settings.HEALTH_MAX_AGE_BAD_DAYS),
        "message": (
            f"Oldest open card {max_age:.0f}d"
            if max_age is not None else "No open cards"
        ),
    })

    # ── Staleness signals (penalise only when open work exists) ───────────────
    days_since_done: int | None = sqlite_cache.days_since_last_completed(epic=epic)
    signals.append({
        "key": "days_since_done",
        "label": "Days since last completion",
        "value": days_since_done,
        "unit": "days",
        "band": settings.band(
            days_since_done if open_count > 0 else None,
            settings.HEALTH_LAST_DONE_WATCH_DAYS,
            settings.HEALTH_LAST_DONE_BAD_DAYS,
        ),
        "message": (
            "No completions yet" if days_since_done is None
            else "Last completion today" if days_since_done == 0
            else f"Last completion {days_since_done}d ago"
        ),
    })

    days_since_move: int | None = sqlite_cache.days_since_last_transition(epic=epic)
    signals.append({
        "key": "days_since_move",
        "label": "Days since last transition",
        "value": days_since_move,
        "unit": "days",
        "band": settings.band(
            days_since_move if open_count > 0 else None,
            settings.HEALTH_LAST_MOVE_WATCH_DAYS,
            settings.HEALTH_LAST_MOVE_BAD_DAYS,
        ),
        "message": (
            "No transitions recorded" if days_since_move is None
            else "Last transition today" if days_since_move == 0
            else f"No card movement in {days_since_move}d"
        ),
    })

    days_since_started: int | None = sqlite_cache.days_since_last_started(epic=epic)
    signals.append({
        "key": "days_since_started",
        "label": "Days since last started",
        "value": days_since_started,
        "unit": "days",
        "band": settings.band(
            days_since_started if open_count > 0 else None,
            settings.HEALTH_LAST_STARTED_WATCH_DAYS,
            settings.HEALTH_LAST_STARTED_BAD_DAYS,
        ),
        "message": (
            "No cards started yet" if days_since_started is None
            else "Last card started today" if days_since_started == 0
            else f"No card started in {days_since_started}d"
        ),
    })

    days_since_created: int | None = sqlite_cache.days_since_last_created(epic=epic)
    signals.append({
        "key": "days_since_created",
        "label": "Days since last created",
        "value": days_since_created,
        "unit": "days",
        "band": settings.band(
            days_since_created,
            settings.HEALTH_LAST_CREATED_WATCH_DAYS,
            settings.HEALTH_LAST_CREATED_BAD_DAYS,
        ),
        "message": (
            "No cards created yet" if days_since_created is None
            else "Last card created today" if days_since_created == 0
            else f"No card created in {days_since_created}d"
        ),
    })

    # ── Blocker count ─────────────────────────────────────────────────────────
    blocker_count = sqlite_cache.count_blocker_cards(epic=epic)
    signals.append({
        "key": "blockers",
        "label": "Active blockers",
        "value": blocker_count,
        "unit": "cards",
        "band": settings.band(
            blocker_count, settings.HEALTH_BLOCKER_WATCH, settings.HEALTH_BLOCKER_BAD
        ),
        "message": (
            f"{blocker_count} blocking card{'s' if blocker_count != 1 else ''}"
            if blocker_count else "No blockers"
        ),
    })

    # ── Due-soon count ────────────────────────────────────────────────────────
    due_soon_count = sqlite_cache.count_due_within(settings.DEADLINE_SOON_DAYS, epic=epic)
    signals.append({
        "key": "due_soon",
        "label": f"Due within {settings.DEADLINE_SOON_DAYS}d",
        "value": due_soon_count,
        "unit": "cards",
        "band": settings.band(
            due_soon_count, settings.HEALTH_DUE_SOON_WATCH, settings.HEALTH_DUE_SOON_BAD
        ),
        "message": (
            f"{due_soon_count} card{'s' if due_soon_count != 1 else ''} "
            f"due within {settings.DEADLINE_SOON_DAYS}d"
            if due_soon_count else "No upcoming deadlines"
        ),
    })

    # ── 28-day flow ratios ────────────────────────────────────────────────────
    now = dt.datetime.now(dt.timezone.utc)
    flow = sqlite_cache.transition_flow_counts(
        (now - dt.timedelta(days=28)).isoformat(), now.isoformat(), epic=epic
    )
    created = flow.get("created", 0)
    started = flow.get("started", 0)
    completed = flow.get("completed", 0)

    start_ratio: float | None = (started / created) if created > 0 else None
    signals.append({
        "key": "start_ratio",
        "label": "Start ratio (28d)",
        "value": round(start_ratio, 2) if start_ratio is not None else None,
        "unit": "ratio",
        "band": _ratio_low_band(
            start_ratio,
            settings.HEALTH_START_RATIO_WATCH,
            settings.HEALTH_START_RATIO_BAD,
        ),
        "message": (
            f"Start ratio {start_ratio:.2f} ({started} started / {created} created)"
            if start_ratio is not None else "No flow data (28d)"
        ),
    })

    finish_ratio: float | None = (completed / started) if started > 0 else None
    signals.append({
        "key": "finish_ratio",
        "label": "Finish ratio (28d)",
        "value": round(finish_ratio, 2) if finish_ratio is not None else None,
        "unit": "ratio",
        "band": _finish_ratio_band(finish_ratio),
        "message": (
            f"Finish ratio {finish_ratio:.2f} ({completed} completed / {started} started)"
            if finish_ratio is not None else "No flow data (28d)"
        ),
    })

    return signals


def project_health(epic: str | None = None) -> dict:
    """Compute overall project health from all monitored signals.

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
