"""File watcher for .kanbai/ directory.

Monitors the board directory for changes made by external processes (MCP agents,
CLI commands, direct edits) and triggers a callback so the UI can refresh.

The module keeps a **singleton observer** per watched path so that repeated
calls to :func:`start_watcher` (e.g. on every Flet client reconnect) never try
to schedule the same macOS FSEvents stream twice, which would raise::

    RuntimeError: Cannot add watch … - it is already scheduled
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


class _BoardChangeHandler(FileSystemEventHandler):
    """Debounced handler that fires a callback when .md/.json files change.

    The callback is stored as a mutable reference so callers can swap it
    (e.g. when a new Flet page session takes over) without restarting the
    underlying Observer.
    """

    def __init__(self, callback: Callable[[], None], debounce_sec: float = 1.0):
        self._callback = callback
        self._debounce_sec = debounce_sec
        self._last_fire = 0.0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    # Allow callers to update the live callback without restarting the observer.
    def set_callback(self, callback: Callable[[], None]) -> None:
        self._callback = callback

    def _schedule(self) -> None:
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_sec, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        self._last_fire = time.time()
        try:
            self._callback()
        except Exception:
            pass

    def on_any_event(self, event: FileSystemEvent) -> None:
        # Only react to relevant file types
        src = event.src_path
        if src.endswith((".md", ".json", ".jsonl")):
            # Ignore .cache.db and ui_state.json
            if ".cache.db" in src or "ui_state.json" in src or "settings.json" in src:
                return
            self._schedule()


# ── Singleton registry ────────────────────────────────────────────────────────
# Maps resolved board-directory path → (Observer, handler) so we never
# schedule the same directory twice on macOS FSEvents.
_watchers: dict[str, tuple[Observer, _BoardChangeHandler]] = {}
_watchers_lock = threading.Lock()


def start_watcher(board_dir: Path, on_change: Callable[[], None]) -> Observer:
    """Start watching *board_dir* for changes, calling *on_change* (debounced).

    If a watcher for *board_dir* is already running (e.g. from a previous Flet
    session), the existing :class:`Observer` is reused and its callback is
    **replaced** with *on_change* — avoiding the FSEvents "already scheduled"
    crash on macOS.

    Returns the Observer instance.  Call ``observer.stop()`` to shut down.
    """
    key = str(board_dir.resolve())

    with _watchers_lock:
        if key in _watchers:
            observer, handler = _watchers[key]
            if observer.is_alive():
                # Just swap the callback so the new page session gets notified.
                handler.set_callback(on_change)
                return observer
            # Observer died (e.g. after a stop()) — clean up and recreate.
            del _watchers[key]

        handler = _BoardChangeHandler(on_change, debounce_sec=0.3)
        observer = Observer()
        observer.schedule(handler, str(board_dir), recursive=True)
        observer.daemon = True
        observer.start()
        _watchers[key] = (observer, handler)
        return observer
