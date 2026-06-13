# Kanban Principles & Project Health

Kanbai is an opinionated Kanban board built around **flow-centric project management**. This document describes the principles the board enforces and the health model it uses to help teams and agents make better decisions.

## Core Philosophy

### 1. Limit WIP, Maximize Flow

The board is designed around a linear flow: **backlog → in_progress → review → done**. The health model explicitly tracks how cards move through this pipeline:

- **Pull rate** (`started ÷ created`) — Are we committing to work as fast as it arrives? Healthy when ≥ 1.0 (backlog is draining).
- **Throughput** (`completed ÷ started`) — Are we completing what we commit to? Healthy near 1.0 (WIP stable); < 1.0 signals in-progress/review pileup (the real bottleneck).

When WIP piles up (throughput drops), the board health turns amber/red as a signal to **stop starting new work and finish what's in flight**.

### 2. No Estimates, No Points — Card Counts as Metrics

Kanbai deliberately avoids story points or effort estimates. Why?

- Points are gamed and fragile: teams estimate inconsistently, and the unit is meaningless outside a single team.
- Card **count** is honest: either a card is done or it isn't; either it moved or it didn't.
- Splitting large work is the discipline: if a card is genuinely too big to complete in a sprint, **split it into smaller cards**. This forces clarity and reduces risk.

Because there are no points, the team adopts **one shared convention: every card is the same "weight."** A card is sized so it can be completed in **at most 3 days**. This is what makes raw card counts comparable — one card is genuinely one unit of progress.

The trade-off: you lose per-card granularity but gain:
- Easier adoption by agents (no estimation overhead).
- Velocity is consistent (one card = one unit of progress).
- Discussions center on **flow health** (are we completing?), not estimates (how big is it?).

### 3. Time Matters More Than Volume

The board tracks **when** cards move, not just that they move:

- **Oldest card age** (max age of open tasks) — If the oldest card is > 56 days, something is stuck. If > 90 days, it's a blocker.
- **Days since last completion** — If no cards moved to done in 14+ days (and open work exists), the pipeline is stalled.
- **Days since last transition** — If no card changed lanes in 7+ days, work is stalled.

These are **staleness signals**. They tell you "we're not making progress **now**", independent of how many cards exist.

### 4. Health is a Holistic View

The header health indicator (top-right of Analytics) is the **worst band** across four signals:

1. Average open-task age ≤ 14d (watch) / ≤ 28d (bad)
2. Oldest open-task age ≤ 56d (watch) / ≤ 90d (bad)
3. Days since last completion ≤ 14d (watch) / ≤ 28d (bad) — only counts if open work exists
4. Days since last transition ≤ 7d (watch) / ≤ 14d (bad) — only counts if open work exists

An empty board (no open cards) is "all good" because there's no work to age. Health only alarms when you have open work that's stalling.

## The Flow Pipeline

Each card's lifecycle follows this path:

```
backlog → in_progress → review → done
```

- **Backlog**: work identified but not yet started. Can grow unbounded (ok for long-term initiatives).
- **In Progress**: active work. Should be limited (WIP cap); represents your team's focus.
- **Review**: work done but awaiting sign-off (code review, QA, approval). A hidden bottleneck—if many cards pile up here, pull rate stays healthy but throughput tanks.
- **Done**: completed work. The team's throughput metric.

**Health rule**: If throughput < 1.0 (more started than completed), cards are aging in in_progress/review. The board will show this in the second stat-card row and flag it amber/red.

## The 3-Day Rule

Every card carries the **same weight**: it must be completable in **at most 3 days**. This is the single sizing convention that makes card counts a fair metric, and it shapes how the board signals deadlines and how work is structured.

### Priority and urgency are separate axes

A card has two independent dimensions (the classic Eisenhower split):

- **Priority** (low / medium / high) is the card's **intrinsic importance** — how much the outcome matters. *You* set it, and it never changes on its own. A card's priority value and tile colour are driven by this and nothing else.
- **Urgency** is **how soon** the card must happen. It comes entirely from the **due date**, not from the priority.

The board keeps these separate on purpose: a trivial chore due tomorrow is *urgent but not important*; a big refactor with no due date is *important but not urgent*. Collapsing them would hide that distinction.

Instead of mutating priority, the board lets you **combine the two axes through filters**. The toolbar has independent priority pills (High / Medium / Low) and deadline pills (**Due ≤3d**, **Due ≤1d**); selecting more than one intersects them:

- **High** + **Due ≤1d** → the important *and* urgent cards (do these now).
- **High** alone → important work, regardless of when it's due.
- **Due ≤3d** alone → everything coming up soon, regardless of importance.

Deadline thresholds live in `board/settings.py` as `DEADLINE_URGENT_DAYS` (1) and `DEADLINE_SOON_DAYS` (3).

### Deadlines warn early, while still actionable

A deadline is only useful if you can still act on it. Because a card takes at most 3 days, the board raises a deadline warning **3 days before** the due date — the moment the work *must* start to land on time:

- **Grey** calendar chip — a due date exists but is comfortably ahead.
- **Amber** warning chip — due within `DEADLINE_SOON_DAYS` (default **3**) days. *Start now.*
- **Red** warning chip — due today or overdue. A missed (or about-to-miss) commitment.

The same window drives the Analytics **"Due Soon"** stat card, and the board toolbar offers **Due ≤3d** and **Due ≤1d** filter pills to focus on just the cards inside each window (their blockers stay visible). These deadline warnings are independent of a card's priority — they reflect urgency only. The threshold lives in `board/settings.py` as `DEADLINE_SOON_DAYS`; raise it if your cards are habitually larger than 3 days (but the right fix is usually to split the card).

> **Missing a 3-day deadline is a signal, not a footnote.** It means the card was mis-sized (too big) or stalled (blocked). Either way, break it down or unblock it — don't just push the date.

### Break big work down with parent / child hierarchies

If a task can't fit in 3 days, it isn't one card — it's a **group** of cards. Use relationships to express that structure:

- **`parent` / `child`** — decompose an initiative into a hierarchy of ≤3-day cards. The parent is the umbrella outcome; children are the concrete, individually-completable steps.
- **`blocks` / `blocked_by`** — express a **sequence**: child B can't start until child A is done.
- Keep each leaf card within the 3-day rule. If a child is still too big, split it again.

This keeps velocity honest (each leaf is one unit) and keeps deadlines meaningful (each leaf can actually be finished in its window).

### The board mirrors the hierarchy

The **Graph** view arranges cards left-to-right by status (Backlog → Done) and stacks them top-to-bottom by their **parent/child hierarchy** — a parent is placed immediately above its children, and a whole task tree clusters into adjacent vertical bands across the lanes. The result reads as a clear hierarchy/sequence rather than a tangle of crossing edges, reminding everyone how the small cards roll up into the larger goal.


## Health Signals Explained

### Pull Rate < 1.0
**Meaning**: You're creating work faster than you're committing to it. Backlog is growing.

**Action**: 
- If intentional (long-term roadmap), acceptable—adjust your expectations.
- If unintentional, prioritize backlog grooming or move less impactful items to deferred.
- Agents should **avoid creating new work** when this is low; focus on clearing backlog.

### Throughput < 1.0
**Meaning**: Cards are piling up in in_progress/review. WIP is unstable; the pipeline has a bottleneck.

**Action**:
- Immediately stop starting new work (set pull rate to 0 if possible).
- Unblock cards in review (merge, deploy, approve).
- Reduce scope of in-progress cards if they're blocked.
- Agents should **prioritize unblocking** over creating new cards.

### Oldest card age > 56d (watch) / > 90d (bad)
**Meaning**: There's a stale card that's been sitting open for weeks/months. Either it's blocked, deprioritized, or forgotten.

**Action**:
- Find the oldest card and investigate (is it blocked? reassign? delete?).
- Move it, close it, or escalate it. Leaving it stale signals poor visibility.
- Agents should **detect and raise** cards older than 56d as blockers.

### Days since last completion > 14d (watch) / > 28d (bad)
**Meaning**: Nothing has shipped in 14+ days. Either WIP is piled up or the team is stalled.

**Action**:
- Check throughput and WIP backlog (review lane especially).
- If WIP is high and throughput is low, unblock. If empty, perhaps nothing is ready to start.
- Agents should flag this and ask: "Why hasn't anything completed recently?"

### Days since last transition > 7d (watch) / > 14d (bad)
**Meaning**: No card has moved lanes (started, completed, or moved to review) in a week. The board is frozen.

**Action**:
- This is the most urgent signal — a frozen board usually means dependency/blocker issues.
- Immediately triage the in_progress lane.
- Agents should escalate this as a critical workflow blocker.

## Weekly Flow Charts

The Analytics page shows **Pull Rate** and **Throughput by week**. These charts:

- Highlight weeks where inflow/outflow were imbalanced (pull rate far from 1.0).
- Show throughput near 1.0 as the healthy steady state.
- Have a dashed reference line at 1.0 for quick assessment.

If a week's ratio is missing (denominator was zero), it's rendered as a gap rather than infinity — you can't have a meaningful ratio with no input.

## Design Decisions Agents Should Know

### 1. Cards are Atomic Units
A card represents one concrete outcome sized to finish in **at most 3 days**. If it's bigger, **split it** into a `parent` and its `child` cards. Agents should suggest splits when creating large cards, and a card with a due date warns 3 days out so it stays actionable.

### 2. No Weights → Velocity is Volatile
Without estimates, 10 cards of different sizes is harder to forecast than "10 story points." Compensation: **use percentiles on flow** (e.g., "last 4 weeks: 5–12 cards/week") rather than averages.

### 3. Backlog Isn't a Constraint
The backlog can grow unbounded without hurting health (as long as throughput and staleness signals are ok). It's a parking lot for future work. Long-term roadmap items can live there indefinitely.

### 4. Epic Filtering Applies to Health
All health signals (including ratios and age stats) can be scoped to a single epic. Use this to monitor sub-projects independently.

### 5. Local Time vs UTC
The board can display dates in local or UTC time (configurable). Flow events are always stored in UTC so dates stay consistent across timezones.

### 6. No Cycle Time or Lead Time
Standard Kanban metrics include **cycle time** (time from start to done) and **lead time** (time from creation to done). Kanbai omits both because they only make sense when tasks vary in size — a 2-day card and a 2-week card have wildly different durations, making averages misleading.

Instead, tasks are sized to complete in **3–7 days** (target: 3 days; some slack allowed). With consistent sizing, raw **card count per week** is the cleaner signal: one card ≈ one unit, so count serves as throughput without duration noise.

## Agent Guidance

When an agent receives this board as an MCP resource or external tool:

1. **Check health first** — If the indicator is red/amber, the board is signaling a problem. Ask why before creating new work.
2. **Respect WIP limits** — If throughput < 1.0, suggest unblocking instead of starting new cards.
3. **Flag staleness** — If oldest card > 56d or days-since-done > 14d, escalate.
4. **Split big tasks** — Enforce the 3-day rule: if a card can't be finished in ≤3 days, split it into `child` cards (and `blocks`/`blocked_by` them into a sequence) instead of creating one oversized card.
5. **Ask for context** — Flow metrics tell *what* is happening; you need domain knowledge to know *why*. Ask the user when health is poor.
6. **Use epics for focus** — Large initiatives can be tagged with an epic; filter metrics to that epic to see if that workstream is healthy.

## Threshold Reference

All thresholds are configurable in `board/settings.py`:

| Signal | Watch | Bad | Meaning |
|---|---|---|---|
| Avg open-task age | 14d | 28d | Tasks aging too slowly → inefficiency |
| Oldest open-task age | 56d | 90d | Stale task → blocker or abandonment |
| Days since completion | 14d | 28d | No delivery → pipeline stalled |
| Days since transition | 7d | 14d | No movement → team frozen |
| Pull rate | N/A | N/A | Healthy ≥ 1.0; < 1.0 = backlog growing |
| Throughput | N/A | N/A | Healthy ≈ 1.0; < 1.0 = WIP piling up |
| Deadline warning | 3d | 0d | Due-soon (amber) ≤ 3d out; due/overdue (red) at 0d — the 3-day rule |
| Due-date filters | Due ≤3d | Due ≤1d | Urgency pills that combine with priority pills; priority itself is never altered |

Adjust these based on your team's rhythm (e.g., if you deploy monthly instead of weekly, push thresholds out accordingly).

---

### What are the key principles of the Kanban method?

The Kanban method is built on core principles designed to improve workflow and efficiency. Here's a breakdown:

- **Start with what you do now**: Beginning with your existing workflow makes it easier to implement Kanban without disrupting operations.
- **Agree to pursue incremental, evolutionary change**: Opt for small, incremental adjustments that evolve over time to reduce the risks associated with drastic changes.
- **Respect the current process, roles, responsibilities, and title**: When introducing improvements, respect the company's current roles and processes.
- **Encourage acts of leadership at all levels in your company**: Empower everyone to take initiative and contribute to process improvements.

> **Note — are principles 3 and 4 contradictory? (acts of leadership necessarily require power)**
>
> They can sound contradictory, but they don't have to be.
>
> - "Leadership at all levels" doesn't mean everyone has formal authority over decisions or resources. It means everyone can initiate improvements within their scope — e.g., raising risks, proposing changes, clarifying policies, helping unblock work, improving flow — while still using the organization's established decision paths.
> - "Respect current roles and processes" means improvements should work with the governance already in place, not bypass it.
>
> You do need whatever level of decision rights the organization requires — but Kanban frames leadership as increasing the quality and visibility of options, not necessarily taking over decision-making.
>
> In practice: **leadership = initiative + contribution**, with process/governance constraints respected.
