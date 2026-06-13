# Flow Metrics & Event Logging

> **See also:** [Kanban Principles & Project Health](kanban-principles.md) — the philosophy and design decisions behind the health model.

## Shipped: lane-transition log

A durable, append-only log of card lane changes powers the flow-ratio
metrics on the Analytics page.

- **Source of truth:** `board_data/transitions.jsonl` — one JSON object per
  line: `{"card_id", "from", "to", "at"}`. `from` is `null` for the creation
  event (a card entering the board).
- **Cache:** the `transitions` table in `.cache.db`, rebuilt from the JSONL on
  every `full_sync()` (so deleting the cache never loses history).
- **Write points:** `board/operations.py` records on `create_card` (→ board),
  `move_card`, and `reorder_cards` (when the lane actually changes). All
  lane changes — UI drag, card dialog, chat agent, MCP server — route through
  these, so coverage is complete.
- **Module:** `board/storage/event_log.py` owns append + replay; query lives in
  `sqlite_cache.transition_flow_counts(since, until, epic=None)`.

### Metrics surfaced (Analytics → "Flow Ratios", trailing 28 days)

> **Terminology:** these metrics are called **pull rate** (started ÷ created) and **throughput** (completed ÷ started). Standard Kanban metrics like cycle time and lead time are intentionally omitted — see [Kanban Principles §6](kanban-principles.md#6-no-cycle-time-or-lead-time) for the rationale.

- **Pull rate** = `started ÷ created` — backlog draining when ≥ 1.0.
- **Throughput** = `completed ÷ started` — WIP stable near 1.0; < 1.0 means
  in-progress/review is piling up.

> Note: these only accumulate going forward — historical moves predating this
> feature cannot be backfilled (the markdown only stores a card's *current*
> lane).

## Shipped: age & staleness analytics

The Analytics page now displays two rows of health metrics:

### Second stat-card row (Row 1b)

- **Pull rate (28d)** — 28-day ratio with all-time as subtitle. Healthy ≥ 1.0.
- **Throughput (28d)** — 28-day ratio with all-time as subtitle. Healthy ≈ 1.0.
- **Oldest card age** — Max age of open cards, color-coded by health band. Includes avg age and newest age as subtitle.
- **Days since last transition** — Days since any card moved lanes, color-coded. Only counts when open cards exist.

### Health thresholds (from `board/settings.py`)

| Signal | Watch | Bad |
|---|---|---|
| Avg open-task age | 14d | 28d |
| Oldest open-task age | 56d | 90d |
| Days since completion | 14d | 28d |
| Days since transition | 7d | 14d |

The header health indicator (top-right) combines all signals into a single verdict: the **worst band** determines the overall status (green → amber → red).

### Charts: Age trend with threshold guides

The "Open Task Age Over Time" chart (90-day history) now includes dashed horizontal lines at each signal's watch/bad thresholds. This helps visualize when age trends cross into warning zones:

- Dashed lines at average-age watch (14d) and bad (28d)
- Dashed lines at oldest-age watch (56d) and bad (90d)

### Charts: Weekly flow ratios (new)

Two new charts show "Pull Rate by Week" and "Throughput by Week" over the trailing 12 weeks:

- Each point is a full week (Monday–Sunday).
- Denominator-zero weeks render as gaps (NaN) instead of infinity — you can't have a ratio with no input.
- Dashed reference line at 1.0 (the balanced-flow mark).

These charts surface trends: is your team consistently under-starting (ratio >> 1.0), over-committing (ratio << 1.0), or balanced?

## Query APIs

New `board/storage/sqlite_cache.py` functions:

- `open_card_age_stats(epic=None)` → `{count, min_age, avg_age, max_age}` for all open cards.
- `days_since_last_transition(epic=None)` → days since the most recent lane move.
- `transition_flow_weekly(weeks=12, epic=None)` → list of `{week_start, created, started, completed}` dicts, one per week, pre-filled with zeros for missing weeks.

## Settings API

New `board/settings.py` functions and thresholds:

- `band(value, watch, bad)` → `'good' | 'watch' | 'bad'` classification.
- `band_color(band_id)` → color string for a band.
- `overall_health(avg_age, max_age, days_since_done, days_since_move, open_count)` → `(label, color)` tuple combining all signals (the worst band wins).

Health thresholds are module constants; adjust them to match your team's rhythm.

> Note: these only accumulate going forward — historical moves predating this
> feature cannot be backfilled (the markdown only stores a card's *current*
> lane).

## Deferred (not yet implemented)

These need the same event-log foundation but more event types / fields. Listed
in rough priority order.

1. **Per-transition cycle & lead time.** With entry/exit timestamps per lane
   already in the log, compute:
   - lead time = `done.at − created.at`
   - cycle time = `done.at − first in_progress.at`
   Report as 50th/85th percentiles, not averages (long-tailed distributions).
   Requires joining a card's transition sequence rather than just counting.

2. **Time-in-lane / aging WIP.** Time each card has sat in its current lane;
   highlight stale in-progress/review cards on the board. Derivable from the
   existing log once we read per-card transition sequences.

3. **Rework signal.** Count backward transitions (e.g. `review → in_progress`,
   `done → in_progress`). Already capturable from `from`/`to`, just not surfaced.

4. **Full field-level audit log.** Currently only *lane* changes are logged.
   A richer event log would capture title/description/priority/epic/assignee/
   due-date edits, comments, links, and archive/unarchive — each as a typed
   event `{card_id, field, old, new, at, actor}`. This needs:
   - an `actor` concept (UI user vs. chat agent vs. MCP client),
   - hooks in every `operations.py` mutator (not just lane moves),
   - a viewer (per-card history timeline in the card dialog).

5. **Required-vs-actual pace (deadline overlay).** Compare rolling throughput
   against `open cards with due_date ÷ weeks_remaining` to flag deadline risk.
   Uses existing `due_date` + the new throughput data; no new logging needed —
   purely an Analytics addition.

6. **Configurable window.** The flow-ratio window is hard-coded to 28 days; make
   it user-selectable (7 / 28 / 90 days) like the heatmap year picker.
