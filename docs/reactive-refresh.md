# Deferred: Event-Bus Refactor (Reactive Refresh)

> **Status:** Deferred idea, not yet implemented. Captured so it isn't lost.
> **Motivation:** Remove the manual per-component refresh fan-out in
> [main.py](../main.py) without a full declarative rewrite.

---

## The problem today

Every mutation callback in [main.py](../main.py) hard-codes which views to
refresh. The mutation site must *know* about every view:

```python
def on_updated(cid, col):
    kanban.refresh_board()
    table_pg.refresh()
    analytics_pg.refresh()   # forget one → stale UI bug

def on_moved(cid, old_col, new_col):
    kanban.refresh_board()
    table_pg.refresh()
    analytics_pg.refresh()

def on_archived(cid, col):
    kanban.refresh_board()
    table_pg.refresh()
    analytics_pg.refresh()
    _go_back()
```

Adding a new view (or a new mutation path) means remembering to wire every
refresh, everywhere. That coupling — *publisher must know every subscriber* —
is the real bug factory.

---

## The pattern: a tiny event bus (observer pattern)

The "bus" is just a dict of `event name → list of functions to call`.

```python
# board/events.py
class Bus:
    def __init__(self):
        self._subs = {}                       # event_name -> [callbacks]

    def on(self, event, fn):                  # subscribe
        self._subs.setdefault(event, []).append(fn)

    def emit(self, event):                    # publish
        for fn in self._subs.get(event, []):
            fn()
```

### Trivial runnable demo

```python
bus = Bus()

# Each "view" subscribes once to the events it cares about.
bus.on("card_changed", lambda: print("Kanban: refreshing board"))
bus.on("card_changed", lambda: print("Table:  refreshing rows"))

# A mutation happens somewhere deep in the app. It just announces it.
def update_card():
    print("(card saved to disk)")
    bus.emit("card_changed")        # <- the ONLY refresh wiring

update_card()
```

Output:

```
(card saved to disk)
Kanban: refreshing board
Table:  refreshing rows
```

---

## How it maps onto Omega

**Mutations announce, they don't dispatch.** A mutation site says only:

```python
bus.emit("card_changed")
```

**Views wire themselves once at startup:**

```python
bus.on("card_changed", lambda **_: kanban.refresh_board())
bus.on("card_changed", lambda **_: table_pg.refresh())
bus.on("card_changed", lambda **_: analytics_pg.refresh())
```

Add a 4th view later? Add one `bus.on(...)` line **in that view** — you never
touch the mutation code or [main.py](../main.py).

---

## Why NOT a full declarative rewrite

- Flet 0.85 is **not** React: no virtual DOM, no diffing, no auto-rerender from
  observable state. "Declarative" here would mean hand-rolling reactivity.
- Omega already invests in **targeted, batched refreshes**
  (`refresh_board()` swaps only ListView tile children in place and batches all
  4 columns into ONE `page.update`). A naive "state changed → rebuild the tree"
  model would throw that away and likely make the Flet render side (the known
  bottleneck) *slower*.
- The event bus keeps those fast refresh methods intact — it only replaces the
  *wiring*, not the rendering.

---

## Tradeoffs (be honest)

| | |
|---|---|
| **Win** | No manual per-component wiring; fewer "forgot to refresh X" bugs; publisher decoupled from subscribers. |
| **Cost** | Moderate refactor — thread the bus through `operations` and the views; discipline that mutations emit events. |
| **Risk** | Over-emitting → redundant refreshes. Mitigate with granular event names (`card_moved` vs `epic_changed`) so views refresh only on what they care about. |
| **Not solved** | In-dialog local state (e.g. the show_dialog/pop_dialog fix) stays imperative — and that's fine. |

---

## Suggested scope when implemented

1. New module `board/events.py` with the `Bus` class + a module-level singleton.
2. `board/operations.py` mutations (`update_card`, `move_card`, `archive_card`,
   `unarchive_card`, `delete_card`, `link_cards`/`unlink_cards`) emit granular
   events.
3. Each view subscribes in its `build()`; delete the fan-out callbacks in
   [main.py](../main.py).
4. Keep `_go_back()` / nav-stack logic imperative — it's dialog-local, not
   global state.
