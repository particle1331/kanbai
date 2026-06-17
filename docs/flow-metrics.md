# Flow Metrics & Analytics Spec

> **Canonical source:** `kanban_system_spec.docx` — all thresholds, formulas, and
> layout decisions in this document originate there.  This file contextualises
> that spec to the project's concrete implementation: lane names, storage paths,
> field names, and Python modules.
>
> **See also:** [Kanban Principles & Project Health](kanban-principles.md) — the
> philosophy and design decisions behind the health model.

---

## Key Design Decisions (from spec)

| Decision | Detail |
|---|---|
| No planning sessions | Workflow is spontaneous. Metrics reflect reality, not estimates. |
| Fixed task size | 1–3 days per card. Cycle-time violations are a sizing-discipline signal, not a process failure. |
| Type dimension | `bugfix` / `feature` / `enhancement` — stored as `tags` on each card (see `Card.tags`). |
| Priority dimension | `high` / `medium` / `low` — stored as `Card.priority`. |
| Blocker rule | A card **cannot** move to `done` while it has at least one active `blocks` link. The blocker edge must be explicitly removed first. |
| Backlog age origin | `Card.created_at`. Cards created directly into an in-flight lane (`in_progress`, `review`) are **excluded** from backlog-aging metrics. |

---

## Lane Structure

The board has four lanes in order:

| Lane key | Display name | Role |
|---|---|---|
| `backlog` | Backlog | Queued, not yet started |
| `in_progress` | In Progress | Actively being worked on |
| `review` | Review | Awaiting review / sign-off |
| `done` | Done | Completed |

**WIP** (work-in-progress) in the spec means cards in *any in-flight lane* —
that is, `in_progress` **or** `review`. Done and backlog are excluded.

---

## Data Model

### Event log (lane-transition history)

| Spec field | Project equivalent | Notes |
|---|---|---|
| `lane_history [{lane, entered_at}]` | `board_data/transitions.jsonl` | Durable, append-only. One JSON object per line: `{"card_id", "from", "to", "at"}`. `from` is `null` for card creation. |
| `started_at` | Derived: latest `transitions` row where `to_col = 'in_progress'` and `at < done_at` | Not stored as a dedicated field; always recomputed from the log. |
| `completed_at` | Derived: `transitions` row where `to_col = 'done'` | Approximated by `Card.updated_at` for cards currently in the `done` lane when a transition row is unavailable. |

**Cache:** the `transitions` table in `.cache.db` mirrors the JSONL for fast
SQL aggregation. It is rebuilt from the JSONL on every `full_sync()` via
`board/storage/sqlite_cache.replace_transitions()`.  Deleting the cache never
loses history — the JSONL is the source of truth.

**Write points:** `board/operations.py` records a transition on `create_card`,
`move_card`, and `reorder_cards` (when the lane actually changes). All paths —
UI drag, card dialog, chat agent, MCP server — route through these, so
coverage is complete.

**Module:** `board/storage/event_log.py` owns append + replay.

### Card fields used by metrics

| Spec field | `Card` attribute | Notes |
|---|---|---|
| `created_at` | `Card.created_at` | ISO-8601 UTC string |
| `type` | `Card.tags` | `bugfix`, `feature`, or `enhancement` (the default tag set) |
| `priority` | `Card.priority` | `high` / `medium` / `low` |
| `is_blocked` | Derived: `any(l["type"] == "blocked_by" for l in Card.links)` | Stored as link entries, not a boolean field |
| `blocker_edges` | `[l for l in Card.links if l["type"] == "blocks"]` | Array of `{"type": "blocks", "number": N}` dicts |
| `archived_at` | `Card.archived` (bool) + `Card.updated_at` | No separate timestamp; `updated_at` is used as proxy |
| `due_date` | `Card.due_date` | Optional ISO date string |

---

## Health Page (Project Health Dashboard)

Real-time operational view. Answers: *are we moving? are we blocked? is WIP under control?*

### Layout

```
┌─────────────────────────────────────────────────────────────────────────┐
│  TOP BAR — KPI CARDS (always visible)                                   │
│  [ Completed 7d ] [ Completed 28d ] [ WIP Now ] [ Throughput 7d ]       │
│  [ Blockers ]  [ Due in 3d ]                                            │
├──────────────────────────────────────────┬──────────────────────────────┤
│  PRIMARY CHARTS (always visible)         │  SIDEBAR / PANEL             │
│                                          │                              │
│  Throughput + WIP trend                  │  Stale WIP                   │
│  (line, shared axis, 28d)                │  (days since last transition)│
│                                          │                              │
│  Cycle time distribution                 │  Backlog Aging               │
│  (histogram)                             │  (min / avg / max age lines) │
│                                          │                              │
│                                          │  Blockers Detail             │
├──────────────────────────────────────────┴──────────────────────────────┤
│  SECONDARY CHARTS (below fold / expandable)                             │
│                                                                         │
│  Cumulative Flow Diagram (CFD)  [full width]                            │
│                                                                         │
│  Created vs Completed (grouped bar, weekly)  │  Cycle time trend (line) │
└─────────────────────────────────────────────────────────────────────────┘
```

### KPI Cards — Top Bar

| KPI | Definition | Window | Warning ⚠ | Bad ✗ |
|---|---|---|---|---|
| **Completed 7d** | Cards moved to `done` in last 7 days | 7d | < 3 | < 1 |
| **Completed 28d** | Cards moved to `done` in last 28 days | 28d | < 10 | < 5 |
| **WIP Now** | Cards currently in `in_progress` or `review` | Current | > 5 | > 8 |
| **Throughput 7d** | `completed ÷ started`, last 7 days | 7d | < 0.6 | < 0.4 |
| **Blockers** | Cards with at least one active `blocks` link | Current | ≥ 2 | ≥ 4 |
| **Due in 3d** | Cards with `due_date` within 3 calendar days (overdue included; `done`/archived excluded) | Current | ≥ 3 | ≥ 5 |

> **Note on null ratios:** Throughput is `None` (displayed as "—") when no
> cards were started in the window (denominator = 0). A null ratio is treated
> as "good" — inactivity in the denominator is caught by the staleness signals,
> not by the ratio itself.

### Primary Charts

| Chart | Type | Definition | Warning ⚠ | Bad ✗ |
|---|---|---|---|---|
| Throughput + WIP trend | Line (shared axis) | 7-day sliding window throughput ratio overlaid with daily WIP count (`in_progress` + `review`). | Throughput trending down 2+ consecutive weeks | Throughput below 0.5 for 2+ weeks |
| WIP trend (overlay) | Line (shared axis) | Daily WIP count on same chart as throughput. Rising WIP with flat throughput is the key early-warning pattern. | WIP rising while throughput is flat | WIP rising while throughput is falling |
| Cycle time histogram | Histogram | Distribution of days from card `started_at` (first `in_progress` transition) to `completed_at` (`done` transition). Target band: 1–3 days. | > 30% of cards beyond 3 days | > 50% of cards beyond 3 days |

### Sidebar / Actionable Lists

| Panel | Definition | Warning ⚠ | Bad ✗ |
|---|---|---|---|
| **Stale WIP** | Per-card days elapsed since the card last moved between lanes (`MAX(transitions.at)` for cards in `in_progress` or `review`) | > 3 days | > 5 days |
| **Backlog Aging** | Min / avg / max age of cards currently in `backlog` lane, measured from `Card.created_at`. Plot three lines. Healthy: all trending down and close together. Warning: max rising while min stays low (cherry-picking). | Max age > 14d | Max age > 21d |
| **Blockers Detail** | List of cards with active `blocks` links. A card cannot move to `done` while blocked. | — | any blocker |

> ⚑ Backlog aging tracks `backlog` lane only. Cards created directly into
> `in_progress` or `review` are excluded from age calculations.

Backlog aging thresholds in full:

| Signal | Warning ⚠ | Bad ✗ |
|---|---|---|
| Max backlog age | > 14 days | > 21 days |
| Avg backlog age | > 7 days | > 14 days |
| Spread (max − min age) | > 14 days | > 21 days or max > 30 days |

### Secondary Charts (Below Fold)

| Chart | Type | Definition | Warning ⚠ | Bad ✗ |
|---|---|---|---|---|
| Cumulative Flow Diagram (CFD) | Stacked area | Card counts per lane stacked over time. Band widths show WIP per lane; slope shows throughput. This is the single most information-dense kanban chart. | Any lane band widening consistently over 2+ weeks | Backlog band growing while Done band is flat |
| Created vs Completed | Grouped bar (weekly) | Weekly bars: cards created vs cards completed. Answers: are we keeping up with demand? | Completed < Created for 2 consecutive weeks | Completed < Created for 3+ consecutive weeks |
| Cycle time trend | Line (7d avg) | 7-day rolling average of cycle time over 28 days. Companion to the histogram — shows if delivery speed is improving or degrading. | Avg cycle time trending above 3 days | Avg cycle time consistently above 5 days |

> **CFD implementation note:** requires a daily snapshot of card counts per
> lane. Store as `{date, lane, count}`. Compute by replaying `transitions.jsonl`
> up to each date, or snapshot nightly. The current `cfd_data()` approximation
> uses each card's *current* lane projected backward — accurate enough for
> trend reading but not suitable for a precise audit trail.

### Pie Charts

Placed alongside secondary charts or in a summary strip. Show **WIP
composition only** (cards in `in_progress` or `review`) — not total backlog.

| Chart | What it shows | Flag if… |
|---|---|---|
| WIP by Type | `bugfix` / `feature` / `enhancement` split of current WIP (from `Card.tags`) | Bugfix slice > 50% of WIP |
| WIP by Priority | `high` / `medium` / `low` split of current WIP (from `Card.priority`) | Medium or Low priority > 40% of WIP — team may not be pulling highest-value work first |
| Completed by Type | Type split of cards completed this month | Bugfix > 60% of completed work — firefighting signal |

---

## Reports Page (Monthly)

Planning-quality review — not a health check. Answers: *are we working on the
right things? is our pace sustainable? are there patterns worth addressing?*

Review cadence: once per month. The **Δ column is the most important field** —
absolute numbers provide context, trends are the signal.

### Layout

```
┌─────────────────────────────────────────────────────────────────────────┐
│  REPORTS PAGE — single scrollable page, tables stacked vertically       │
│                                                                         │
│  Table 1 — Volume & Completion                                          │
│  Table 2 — Cycle Time Summary                                           │
│  Table 3 — Throughput & Flow                                            │
│  Table 4a — Work Composition Matrix (Created this month)               │
│  Table 4b — Work Composition Matrix (Completed this month)             │
│  Table 5 — Work Pattern Review                                          │
│                                                                         │
│  [ Notable patterns this month — free text field ]                      │
└─────────────────────────────────────────────────────────────────────────┘
```

### Table 1 — Volume & Completion

| Metric | Warning ⚠ | Bad ✗ |
|---|---|---|
| Tasks created | — | — |
| Tasks completed | — | — |
| Tasks archived / deleted | > 20% of created | > 35% of created |
| Archive rate (%) | > 20% | > 35% |
| Net output (completed − created) | Negative | Negative 2+ months running |

### Table 2 — Cycle Time Summary

> ⚑ Use **median** rather than average as your primary signal — a single
> 10-day blocked card will skew the mean significantly.

| Metric | Warning ⚠ | Bad ✗ |
|---|---|---|
| Avg cycle time (days) | > 3 days | > 5 days |
| Median cycle time (days) | > 3 days | > 4 days |
| % completed within 3 days | < 70% | < 50% |
| % that exceeded 3 days | > 30% | > 50% |

Cycle time = `completed_at − started_at` in calendar days, per card
(`done` transition timestamp − last `in_progress` transition timestamp).

### Table 3 — Throughput & Flow

| Metric | Warning ⚠ | Bad ✗ |
|---|---|---|
| Avg weekly throughput | < 3 / week | < 1 / week |
| Avg daily WIP | > 5 | > 8 |
| Pull rate avg (started ÷ created) | < 0.5 | < 0.3 |
| Peak WIP (single day) | > 8 | > 12 |
| Weeks with throughput drop > 30% | ≥ 1 week | ≥ 2 weeks |

### Table 4 — Work Composition Matrix

Complete the matrix **twice**: once for tasks Created and once for tasks
Completed. The gap between the two reveals planning-vs-execution reality.

|  | High | Medium | Low | Total |
|---|---|---|---|---|
| Bugfix | | | | |
| Feature | | | | |
| Enhancement | | | | |
| Total | | | | |

> ⚑ Flag if bugfix > 50% of high-priority completed work — indicates the
> team is primarily firefighting rather than shipping value.

### Table 5 — Work Pattern Review

| Metric | Warning ⚠ | Bad ✗ |
|---|---|---|
| Bug vs feature ratio (completed work) | > 50% bugs | > 70% bugs |
| High-priority completion % | < 70% | < 50% |
| Cycle time violations (cards exceeding 3d) | > 30% of completed | > 50% of completed |
| Weeks with throughput drop > 30% | ≥ 1 week | ≥ 2 weeks |
| Backlog aging violations (cards > 14d old) | ≥ 3 cards | ≥ 5 cards |

### Notable Patterns

Free text. One paragraph max. Flag anything the numbers don't capture —
extended firefighting period, team member out, product pivot, infrastructure
work that inflated WIP, etc.

---

## Metric Formulas

| Metric | Formula | Project mapping |
|---|---|---|
| Throughput | `completed_count ÷ started_count` (same window) | `transitions` rows: `to_col='done'` ÷ `to_col='in_progress'` |
| Pull rate | `started_count ÷ created_count` (same window) | `to_col='in_progress'` ÷ `from_col IS NULL` |
| Cycle time (per card) | `completed_at − started_at` (calendar days) | Latest `in_progress` transition `at` → `done` transition `at` |
| Backlog age | `today − created_at` (backlog lane cards only) | `Card.created_at`; exclude cards not currently in `backlog` |
| Stale WIP | `today − MAX(transitions.at)` (per WIP card) | Cards in `in_progress` or `review`; `MAX(at)` from `transitions` |
| Spread (backlog aging) | `max_age − min_age` (current backlog cards) | Derived from `backlog_aging_timeseries()` |
| Archive rate | `archived_count ÷ created_count × 100` (same window) | `Card.archived = True`; `updated_at` used as archive-date proxy |
| Net output | `completed_count − created_count` (same window) | From `transitions` table counts |

---

## Query API (`board/storage/sqlite_cache.py`)

| Function | Returns |
|---|---|
| `transition_flow_counts(since, until, epic=None)` | `{created, started, completed}` event counts in window |
| `transition_flow_weekly(weeks=12, epic=None)` | `[{week_start, created, started, completed}]` — one per ISO week |
| `avg_cycle_time_days(days, epic=None)` | Average cycle time (float, days) for completions in window; `None` if no data |
| `cycle_time_histogram(days=28, epic=None)` | `{"1": n, "2": n, "3": n, "4": n, "5": n, "6+": n}` distribution |
| `stale_wip_cards(n=10, epic=None)` | WIP cards sorted by `days_stale` desc: `{id, title, tags, priority, days_stale}` |
| `backlog_aging_timeseries(days=28, epic=None)` | `[{date, min_age, avg_age, max_age}]` for `backlog` lane cards |
| `open_card_age_stats(epic=None)` | `{count, min_age, avg_age, max_age}` for all open (non-done) cards |
| `days_since_last_transition(epic=None)` | Days since the most recent lane move |
| `days_since_last_started(epic=None)` | Days since a card last entered `in_progress` |
| `days_since_last_created(epic=None)` | Days since the most recent card creation event |
| `count_blocker_cards(epic=None)` | Count of cards with at least one active `blocks` link |
| `count_due_within(days, epic=None)` | Count of active cards with `due_date ≤ today + days` |
| `count_completed_within(days, epic=None)` | Count of `done` cards updated within trailing `days` window |
| `count_started_within(days, epic=None)` | Count of distinct cards that entered `in_progress` within window |

---

## Health Signals (`board/health.py`)

`compute_signals(epic=None)` returns one dict per signal with keys:
`key`, `label`, `value`, `unit`, `band` (`"good"` | `"watch"` | `"bad"`), `message`.

`project_health(epic=None)` returns the worst band across all signals plus the
full signal list and a filtered `warnings` list.

The header health indicator (top-right of the app) combines all signals into a
single verdict: **the worst band wins** (green → amber → red).

---

## Threshold Summary (`board/settings.py`)

> ⚑ All thresholds are starting points. Calibrate after 2–3 months of your
> own data — team size and cadence affect what "normal" looks like for your
> context. Constants live in `board/settings.py`.

| Signal | Watch ⚠ | Bad ✗ | Direction | Constant(s) |
|---|---|---|---|---|
| Completed 7d | < 3 | < 1 | lower is worse | *(deferred)* |
| Completed 28d | < 10 | < 5 | lower is worse | *(deferred)* |
| WIP Now | > 5 | > 8 | higher is worse | *(deferred)* |
| Throughput 7d | < 0.6 | < 0.4 | lower is worse | `HEALTH_THROUGHPUT_WATCH/BAD` |
| Blockers | ≥ 2 | ≥ 4 | higher is worse | `HEALTH_BLOCKER_WATCH/BAD` |
| Due in 3d | ≥ 3 | ≥ 5 | higher is worse | `HEALTH_DUE_SOON_WATCH/BAD` |
| Stale WIP (per card) | > 3d | > 5d | higher is worse | *(deferred)* |
| Backlog max age | > 14d | > 21d | higher is worse | *(deferred — current `HEALTH_MAX_AGE` covers all open cards)* |
| Backlog avg age | > 7d | > 14d | higher is worse | *(deferred)* |
| Backlog spread | > 14d | > 21d or max > 30d | higher is worse | *(deferred)* |
| Avg cycle time | > 3d | > 5d | higher is worse | `HEALTH_CYCLE_TIME_WATCH/BAD_DAYS` |
| Days since last move | > 7d | > 14d | higher is worse | `HEALTH_LAST_MOVE_WATCH/BAD_DAYS` |
| Days since last started | > 14d | > 28d | higher is worse | `HEALTH_LAST_STARTED_WATCH/BAD_DAYS` |
| Pull rate (28d) | < 1.0 | < 0.5 | lower is worse | `HEALTH_START_RATIO_WATCH/BAD` |

---

## What's Shipped

- **Lane-transition log** — `board_data/transitions.jsonl` + `transitions` table in `.cache.db`.
  All card movements (create, move, reorder) are logged and queryable.
- **Flow counts** — `transition_flow_counts()` and `transition_flow_weekly()` for created /
  started / completed event volumes.
- **Cycle time** — `avg_cycle_time_days()` and `cycle_time_histogram()` (`in_progress → done`).
- **Stale WIP** — `stale_wip_cards()` with `days_stale` per card.
- **Backlog aging** — `backlog_aging_timeseries()` (backlog lane only, min/avg/max).
- **CFD** — `cfd_data()` (approximation using current lane, projected backward).
- **Health signals** — `board/health.py` covers avg/max age, staleness, pull/finish ratios,
  blocker count, due-soon count; `project_health()` gives worst-band verdict.
- **Throughput + WIP trend chart**, **Created vs Completed bar chart**, **Cycle time histogram**,
  **Backlog aging chart** — all rendered in the Analytics/Health page.
- **Pie charts** — WIP by type and by priority, completed by type.

---

## Deferred (Not Yet Implemented)

Listed in spec priority order:

1. **KPI top bar aligned to spec** — current stat cards differ from the spec layout.
   Target: `Completed 7d`, `Completed 28d`, `WIP Now`, `Throughput 7d`, `Blockers`, `Due in 3d`.

2. **WIP Now health signal** — cards in `in_progress` + `review`. Spec thresholds: watch > 5, bad > 8.
   Add `HEALTH_WIP_WATCH = 5` and `HEALTH_WIP_BAD = 8` to `board/settings.py`.

3. **Completed 7d / 28d health bands** — counts exist (`count_completed_within()`);
   missing: explicit health banding. Spec: 7d watch < 3, bad < 1; 28d watch < 10, bad < 5.

4. **Blocker threshold calibration** — current setting fires "bad" on any single blocker.
   Spec: watch ≥ 2, bad ≥ 4. Change `HEALTH_BLOCKER_WATCH = 2`, `HEALTH_BLOCKER_BAD = 4`.

5. **Backlog-only age health bands** — current `HEALTH_MAX_AGE` / `HEALTH_AVG_AGE` cover
   all open cards (backlog + WIP). Spec scopes backlog aging to the `backlog` lane only
   with tighter thresholds: max age watch > 14d bad > 21d; avg age watch > 7d bad > 14d;
   spread watch > 14d bad > 21d (or max > 30d).

6. **Per-transition cycle time histogram + trend** — histogram is shipped. Deferred:
   a 7-day rolling average cycle time trend line (companion chart) and median reporting
   for the monthly Reports page.

7. **Throughput + WIP trend chart** — primary chart (shared-axis line) is deferred;
   the current analytics page shows weekly bar charts instead.

8. **CFD from daily snapshots** — current `cfd_data()` uses a per-card current-lane
   projection. Spec calls for a true daily snapshot table `{date, lane, count}` rebuilt
   by replaying `transitions.jsonl`. Required for a reliable audit-quality CFD.

9. **Reports page (monthly)** — all five tables (Volume & Completion, Cycle Time
   Summary, Throughput & Flow, Work Composition Matrix ×2, Work Pattern Review) plus
   the free-text "Notable Patterns" field. Requires the `type` field (currently `tags`)
   to be treated as a first-class dimension in queries.

10. **Work Composition Matrix** — requires querying cards by `tags` (type dimension) ×
    `priority`. The `count_cards_by_tag()` query exists; what's missing is the 3×3
    matrix view split by created vs completed within a calendar month.

11. **Rework signal** — backward transitions (`review → in_progress`, `done → in_progress`)
    are already capturable from `from_col` / `to_col` in the transitions table;
    not yet surfaced in any metric or chart.

12. **Required-vs-actual pace (deadline overlay)** — compare rolling throughput
    against `open cards with due_date ÷ weeks_remaining` to flag deadline risk.

13. **Configurable metric window** — windows are hard-coded to 7 / 28 days.
    Spec suggests making them user-selectable (7 / 28 / 90 days).
