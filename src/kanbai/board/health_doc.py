"""board/health_doc.py - Health model documentation as a Markdown string.

Generates a human-readable Markdown document describing the project health
model: all 8 A-series stat card signals, chart thresholds, badge rules,
pie chart flags, and operational parameters. Values are read live from
``board/settings.py`` so the doc always reflects the current configuration.

Public API
----------
health_model_doc() -> str
    Return the full Markdown document string.
"""

from __future__ import annotations

from kanbai.board import settings


def health_model_doc() -> str:
    """Return a Markdown document describing the health model and thresholds."""
    return f"""\
## Kanban Principles

These principles govern how work flows through the board.

**P1 — Fixed task size.** Every card should be completable within 1–{settings.HEALTH_CYCLE_TIME_BAD_DAYS:.0f} days.
If a task is too large, break it into smaller cards before starting.
This keeps cycle times predictable and flow smooth.

**P2 — Decompose complex work.** Since tasks are fixed-size, complex work
is broken into elementary cards. Use relationships (e.g. `blocks`,
`is_blocked_by`, `relates_to`) to capture dependencies between the parts
so the structure remains visible and the agent can reason about ordering.

**P3 — No backlog rot.** Once a task enters the backlog, it should be
completed or consciously removed within {settings.HEALTH_BACKLOG_AGE_BAD_DAYS:.0f} days. Stale items
clutter prioritisation and signal uncommitted scope. Prune regularly.

**P4 — Stable requirements.** If a card’s requirements change drastically
after it enters the board, archive it and notify stakeholders. Create a
fresh card with a clean lead time so metrics stay honest. Minor edits are
fine — use your best judgement.

**P5 — Spillover is normal.** The 7-day measurement window is fixed and
aligned to the natural work week. Cards started before the window that
complete within it count toward throughput — this is expected and healthy.

---

## Overview

Project health is derived from **8 monitored signals** (A1–A8) corresponding
to the stat cards on the Health page. Each signal maps a live metric to one of
three bands:

| Band | Meaning | Colour |
|------|---------|--------|
| **good** | Within healthy limits | 🟢 Green |
| **watch** | Approaching a threshold | 🟡 Amber |
| **bad** | Threshold exceeded | 🔴 Red |

The **overall health verdict** is the worst band across all 8 signals.

---

## A. Stat Card Signals (A1–A8)

These are the primary health indicators.

### A1 — Throughput 7d

| Field | Value |
|---|---|
| **Definition** | `completed ÷ started` over the past 7 days |
| **Direction** | Lower is worse |
| **Watch** | < {settings.HEALTH_THROUGHPUT_WATCH} |
| **Bad** | < {settings.HEALTH_THROUGHPUT_BAD} |
| **Goal** | Keep completions proportional to starts. Values near 1.0 mean WIP is stable. |

### A2 — WIP Count

| Field | Value |
|---|---|
| **Definition** | Count of cards in `in_progress` + `review` (current snapshot) |
| **Direction** | Higher is worse |
| **Watch** | > {settings.HEALTH_WIP_WATCH} |
| **Bad** | > {settings.HEALTH_WIP_BAD} |
| **Goal** | Limit WIP to maintain flow. Excessive WIP causes context-switching and hides bottlenecks. |

### A3 — WIP Median Age

| Field | Value |
|---|---|
| **Definition** | Median of `(now − started_at)` for WIP cards |
| **Direction** | Higher is worse |
| **Watch** | > {settings.HEALTH_WIP_AGE_WATCH_DAYS}d |
| **Bad** | > {settings.HEALTH_WIP_AGE_BAD_DAYS}d |
| **Goal** | Cards should flow through WIP quickly. Rising median age signals cards are stuck or too large. |

### A4 — Median Cycle Time

| Field | Value |
|---|---|
| **Definition** | Median of `(done_at − created_at)` for cards completed in the past 7d |
| **Direction** | Higher is worse |
| **Watch** | ≥ {settings.HEALTH_CYCLE_TIME_WATCH_DAYS}d |
| **Bad** | ≥ {settings.HEALTH_CYCLE_TIME_BAD_DAYS}d |
| **Goal** | Every card should be completable within ~3 days. Rising cycle time means cards are too large or blocked. |

### A5 — Pull Rate 7d

| Field | Value |
|---|---|
| **Definition** | `started ÷ created` over the past 7 days |
| **Direction** | Lower is worse |
| **Watch** | < {settings.HEALTH_PULL_RATE_WATCH} |
| **Bad** | < {settings.HEALTH_PULL_RATE_BAD} |
| **Goal** | Pull work at least as fast as it’s created. Values below watch mean the backlog is growing. |

### A6 — Median Backlog Age

| Field | Value |
|---|---|
| **Definition** | Median of `(now − created_at)` for cards in `backlog` |
| **Direction** | Higher is worse |
| **Watch** | > {settings.HEALTH_BACKLOG_AGE_WATCH_DAYS}d |
| **Bad** | > {settings.HEALTH_BACKLOG_AGE_BAD_DAYS}d |
| **Goal** | Backlog items should be prioritised or pruned. Rising median means items are ageing without attention. |

### A7 — Blockers

| Field | Value |
|---|---|
| **Definition** | Count of WIP cards with ≥1 `blocks` link |
| **Direction** | Higher is worse |
| **Watch** | ≥ {settings.HEALTH_BLOCKER_WATCH} |
| **Bad** | ≥ {settings.HEALTH_BLOCKER_BAD} |
| **Goal** | Blocked cards stall flow. Resolve blockers immediately. |

### A8 — Due in 3d

| Field | Value |
|---|---|
| **Definition** | Non-done cards with `due_date ≤ today + {settings.DEADLINE_SOON_DAYS}` |
| **Direction** | Two-tier |
| **Watch** | due_3d ≥ {settings.HEALTH_DUE_WATCH} |
| **Bad** | due_3d ≥ {settings.HEALTH_DUE_BAD} OR due_1d ≥ 1 |
| **Goal** | Any card due within {settings.DEADLINE_URGENT_DAYS}d is immediately critical. Multiple cards due soon signals overcommitment. |

---

## B. Chart Thresholds & Badges

Charts overlay threshold lines and show badges to contextualise trends.

### Threshold Lines on Charts

| Chart | Lines from | Reading guide |
|-------|-----------|---------------|
| Pull Rate (7d) | A5 ({settings.HEALTH_PULL_RATE_WATCH} / {settings.HEALTH_PULL_RATE_BAD}) | Sustained values below {settings.HEALTH_PULL_RATE_WATCH} mean backlog is growing faster than work is pulled. |
| Throughput & WIP | A1 ({settings.HEALTH_THROUGHPUT_WATCH} / {settings.HEALTH_THROUGHPUT_BAD}) for throughput; A2 ({settings.HEALTH_WIP_WATCH} / {settings.HEALTH_WIP_BAD}) for WIP | Rising WIP with flat throughput signals accumulation. Watch for divergence. |
| Cycle Time Trend | A4 ({settings.HEALTH_CYCLE_TIME_WATCH_DAYS}d / {settings.HEALTH_CYCLE_TIME_BAD_DAYS}d) | Rising median signals slower delivery. Wide IQR means inconsistent sizing. |
| Backlog Age | A6 ({settings.HEALTH_BACKLOG_AGE_WATCH_DAYS}d / {settings.HEALTH_BACKLOG_AGE_BAD_DAYS}d) | Rising median means backlog is aging. Prune or prioritise stale items. |
| CFD | Shape-based (no lines) | Widening bands = growing WIP. Parallel bands = stable flow. Flat done line = stalled delivery. |

### Badge Thresholds

| # | Badge | Definition | Watch | Bad |
|---|-------|-----------|-------|-----|
| B1 | CT Distribution % | % of completed cards (28d) with cycle_time > 3d | > 50% | > 70% |
| B5 | Stale WIP | Cards started > {settings.STALE_WIP_DAYS}d ago | any > {settings.STALE_WIP_WATCH_DAYS}d | any > {settings.STALE_WIP_BAD_DAYS}d |
| B6 | Aged Backlog | Backlog cards with age > {settings.AGED_BACKLOG_MULTIPLIER}× median | any | ≥ 5 |

---

## C. Pie Chart Flags

Composition warnings on the WIP and Completed pie charts.

| # | Flag | Definition | Threshold |
|---|------|-----------|-----------|
| C1 | WIP bugfix % | `bugfix_count ÷ wip_total × 100` | watch > 50% (no bad) |
| C2 | WIP priority inversion | WIP has higher med+low proportion than backlog by >10pp | watch only |
| C3 | Completed bugfix % | `bugfix_count ÷ completed_total × 100` (28d) | watch > 50% (no bad) |

**C2 motivation:** If WIP contains proportionally more medium+low priority work
than the backlog, it suggests high-priority items are being skipped. The flag
only triggers when the gap exceeds 10 percentage points to avoid noise on
near-equal distributions.

---

## E. Operational Parameters

| # | Parameter | Value | Purpose |
|---|-----------|-------|---------|
| E1 | `DEADLINE_SOON_DAYS` | {settings.DEADLINE_SOON_DAYS} | Cards due within this window are "due soon" |
| E2 | `DEADLINE_URGENT_DAYS` | {settings.DEADLINE_URGENT_DAYS} | Tighter window for urgent escalation (red badge on board) |
| E3 | `STALE_WIP_DAYS` | {settings.STALE_WIP_DAYS} | Cards in WIP longer than this appear in the stale list |
| E4 | `AGED_BACKLOG_MULTIPLIER` | {settings.AGED_BACKLOG_MULTIPLIER}× | Cards older than this × median backlog age are outliers |

---

## Flow Event Definitions

`flow_Nd` refers to transition event counts over the past N days:

| Field | Condition | Meaning |
|-------|-----------|---------|
| `created` | `from_col IS NULL` | Card first entered the board |
| `started` | `to_col = 'in_progress'` | Card moved into `in_progress` |
| `completed` | `to_col = 'done'` | Card moved into `done` |

**Nuances:**
- These are **event counts**, not distinct card counts. A card moved back and
  re-started counts as 2 `started` events.
- `completed` counts any transition into `done` in the window, regardless of
  when the card was started.
- Throughput (A1) = `completed ÷ started` - values > 1.0 are possible from
  spillover cards started before the measurement window.
- Pull Rate (A5) = `started ÷ created` - values > 1.0 mean the backlog is
  being drained faster than new work arrives.

---

## Design Principles

1. **Worst-band aggregation** - overall health is the worst band across all 8
   signals. One bad signal makes the whole project "bad".

2. **No data = good** - when a metric is undefined (e.g. no completions in the
   window), it defaults to "good" so missing data never raises a false alarm.

3. **Actionable signals only** - every signal maps to a concrete action:
   - High WIP → stop starting, finish something
   - Low throughput → unblock or reduce WIP
   - Stale cards → break down or reprioritise
   - Blockers → resolve immediately
   - Approaching deadline → escalate or rescope
"""
