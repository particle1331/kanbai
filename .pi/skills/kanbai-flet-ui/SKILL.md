---
name: kanbai-flet-ui
description: Modify Kanbai’s Flet UI safely and performantly. Use this skill for changes to Kanban board lanes, card tiles, drag-and-drop, filtering/visibility toggles, and the chat overlay/modal interactions that require correct Flet control updates.
compatibility:
  required: [flet]
---

# Goal
Keep UI changes consistent with Kanbai’s established Flet architecture: **targeted updates**, **stable control keys**, and **non-blocking event handling**.

# Scope
Use this skill when the requested work is primarily:
- Kanban UI behavior (lanes, cards, filters, pills/toggles)
- Drag-and-drop correctness (tile ordering/moves)
- Page UI wiring (table/graph/health/chat overlay)
- Dialog lifecycle (modal open/close)

Don’t use it for:
- Pure backend/data-layer refactors
- Storage/model changes that don’t require UI adjustments

# Repo mental model (where the relevant code lives)
- `src/kanbai/app.py`: navigation + lazy-loading of pages
- `src/kanbai/ui/kanban_page.py`: board header + epic dropdown + board orchestration
- `src/kanbai/ui/board_view.py`: lane/list view + card tiles + drag/drop wiring
- `src/kanbai/ui/table_page.py`, `graph_page.py`, `health_page.py`: secondary views
- `src/kanbai/ui/chat_panel.py`: AI chat overlay + streaming + long-running tool calls

# Non-negotiable conventions (follow these)

## 1) Prefer targeted updates over full rebuilds
When you can, update only what changed:
- **Visibility/filter changes** → use `apply_filter()` (toggling `visible` on existing tiles via stable `key`)
- **Lane content updates (cards moved/created/reordered)** → use `refresh_lane(...)` for the affected lane(s)
- **Multiple-lane batch updates** → call `refresh_lane(..., update=False, ...)` repeatedly, then call `page.update()` once
- **Entire board redraw (last resort)** → `KanbanPage.refresh_board()` only when truly needed

Avoid rebuilding the whole board tree for filter/toggle changes.

## 2) Preserve card tile keys + drag/drop wiring
Card filtering and drag/drop depend on the exact control wiring:
- Cards must be wrapped in `ft.Draggable`
- The draggable must have `draggable.key = str(number)` (or an empty string when the number is missing)
- Ensure `group="cards"` matches between:
  - `ft.Draggable(group="cards", ...)`
  - and the corresponding `ft.DragTarget(group="cards", ...)`
- Keep `data=(card_id, lane)` on the draggable so `on_accept` can move/reorder correctly

If you change card tile rendering, **keep these invariants**.

## 3) Use shared UI components and theme helpers
Do not hardcode new palettes/styles.
- Reuse `src/kanbai/ui/theme.py` helpers/constants
- For epic dropdowns, use `theme.epic_dropdown(...)`
- For archive/eye toggles, use `theme.eye_toggle(...)` and `theme.archive_icon_button(...)`

## 4) Dialogs: use the repo’s correct lifecycle (and don’t mix APIs)
This repo has **two** correct dialog patterns. Pick one and stick to it.

### A) Transient dialog stack (use `show_dialog` + `pop_dialog`)
Use this when the dialog should be managed by the dialog stack:
- create `ft.AlertDialog(...)`
- open with `page.show_dialog(dlg)`
- close with `page.pop_dialog()`

(See usage patterns in `src/kanbai/ui/graph_page.py`, `src/kanbai/ui/search_overlay.py`, `src/kanbai/ui/card_dialog.py`.)

### B) Overlay-attached pinned dialogs (use `overlay.append` + `dlg.open`)
Use this when code explicitly appends the dialog and toggles `open`:
- create `ft.AlertDialog(...)`
- `page.overlay.append(dlg)`
- set `dlg.open = True/False`
- close by setting `dlg.open = False` and calling `page.update()`

**Anti-pattern:** don’t close a stack dialog with `dlg.open=False`, and don’t close an overlay-pinned dialog with `page.pop_dialog()`.

## 5) Don’t block the Flet event loop
For anything slow (tool calls, DB/cache rebuilds, git operations, heavy chart rendering):
- run in `threading.Thread(..., daemon=True).start()`
- keep the UI update small + call `.update()` only after changing the relevant controls
- wrap UI updates in `try/except Exception: pass` (this repo uses this guard)

## 6) Avoid re-entrant/double actions
When wiring `on_click` / `on_submit` / streaming events:
- guard with locks or explicit “busy” flags
- follow the patterns used in `src/kanbai/ui/chat_panel.py`

## 7) Lazy-load views and keep navigation consistent
If you add a new top-level view:
- follow the pattern in `src/kanbai/app.py` (`NAV_ITEMS`, `_views_cache`, `_ensure_view()`)

# Implementation checklist (use while editing code)
Before finalizing a UI change, confirm:
- [ ] Did we preserve drag-drop `group` and `data` payload?
- [ ] Did we preserve card tile `draggable.key` semantics so filtering still works?
- [ ] Did we avoid rebuilding the entire board when a targeted update was sufficient?
- [ ] Did we move slow work off the event loop and update UI only after state changes?

# How to decide what to change
Map the request to the repo mechanism:
- “Hide/show cards” → `apply_filter()`
- “Card moved lanes; update instantly” → `refresh_lane(from_lane, to_lane)`
- “New epic created; dropdown not updating” → `KanbanPage.refresh_epics()`
- “Add AI chat action calling tools” → background thread + update only chat controls like `ChatPanel._do_send`

# Required output format when proposing code changes
When you suggest code edits, ALWAYS include:
1) **Files modified** (paths)
2) **What UI elements are being updated and why**
   - e.g., “toggled `visible` on existing tiles; relied on `draggable.key` for stable filtering”
3) **Risk check list**
   - preserved drag-drop group/data/key?
   - avoided rebuilding the whole board?
   - guarded background thread UI updates?

# Examples (copy these patterns to avoid regressions)

## 1) Filter / visibility mistakes
### Correct
- Add a priority/deadline filter pill → compute visible card numbers and call:
  - `apply_filter(page, lane_listviews, visible_numbers)`
- “Clear filters” → call `apply_filter(..., visible_numbers=None)`

### Common mistake
- Rebuilding the board (lanes + DragTargets) just to hide tiles.

## 2) Lane updates mistakes
### Correct
- A card moved lanes → call **two targeted refreshes**:
  - `refresh_lane(page, lane_listviews, from_lane)`
  - `refresh_lane(page, lane_listviews, to_lane)`
- A card reordered within the same lane → call only `refresh_lane(..., that_lane)`

### Common mistake
- Calling `refresh_board()` (full rebuild) for a one-lane change.

## 3) Drag-and-drop mistakes
### Correct invariants (must preserve)
- `ft.Draggable(group="cards", data=(card_id, lane), content=tile)`
- `ft.DragTarget(group="cards", on_accept=...)`
- `draggable.key = str(number)` (or `""` when number is missing)

### Common mistake
- Removing/changing `draggable.key` → fast filtering and drag accept logic break.

## 4) Epic dropdown sync mistakes
### Correct
- After creating an epic → call `KanbanPage.refresh_epics()`
- When toggling “Archived” → rebuild dropdown options in-place, then call `KanbanPage.refresh_board()`

## 5) “Flet old API” dialog mistakes (anti-pattern)
### Correct templates
**Transient dialog stack:**
```py
dlg = ft.AlertDialog(modal=False, title=ft.Text("Title"), content=ft.Text("..."))
page.show_dialog(dlg)

# later (in a handler)
page.pop_dialog()
```

**Overlay-pinned dialog:**
```py
dlg = ft.AlertDialog(title=ft.Text("Title"), content=ft.Text("..."))
page.overlay.append(dlg)
dlg.open = True

# later (in a handler)
dlg.open = False
page.update()
```

**Rule:** don’t mix these close/open mechanisms.

## 6) Chat / streaming mistakes
### Correct
- During token streaming: update only the typing indicator + msg list (`.update()` on the relevant controls)
- Before/after tool calls: use the existing busy/lock pattern from `ChatPanel` to avoid duplicate sends

### Common mistake
- Recreating the entire chat panel/container during streaming (UI stutters + duplicated bubbles).
