"""File watcher for .kanbai/ directory.

Monitors the board directory for changes made by external processes (MCP agents,
CLI commands, direct edits) and triggers a callback so the UI can refresh.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer


class _BoardChangeHandler(FileSystemEventHandler):
    """Debounced handler that fires a callback when .md/.json files change."""

    def __init__(self, callback: Callable[[], None], debounce_sec: float = 1.0):
        self._callback = callback
        self._debounce_sec = debounce_sec
        self._last_fire = 0.0
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

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
            if ".cache.db" in src or "ui_state.json" in src:
                return
            self._schedule()


def start_watcher(board_dir: Path, on_change: Callable[[], None]) -> Observer:
    """Start watching *board_dir* for changes, calling *on_change* (debounced).

    Returns the Observer instance. Call ``observer.stop()`` to shut down.
    """
    handler = _BoardChangeHandler(on_change, debounce_sec=1.0)
    observer = Observer()
    observer.schedule(handler, str(board_dir), recursive=True)
    observer.daemon = True
    observer.start()
    return observer
