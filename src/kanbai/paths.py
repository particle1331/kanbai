"""Central filesystem anchors for Kanbai.

Two roots are tracked:

``INSTALL_ROOT``
    The directory containing this package — i.e. where kanbai itself is
    installed.  Used for package-relative things like ``docs/``.

``PROJECT_ROOT``
    The target project repo whose Kanban board we are managing.  Determined by:
    1. The ``KANBAI_PROJECT_PATH`` environment variable (set by cli.py), or
    2. Walking up from cwd looking for ``.kanbai/``, or
    3. Falling back to cwd (for ``kanbai init`` scenarios).
"""

from __future__ import annotations

import os
from pathlib import Path

# ── Kanbai install location ─────────────────────────────────────────────────
INSTALL_ROOT: Path = Path(__file__).resolve().parent

# Static web assets for the Flet UI (fonts, icons, etc.).
ASSETS_DIR = INSTALL_ROOT / "assets"

# ── Target project root ──────────────────────────────────────────────────────
_env_project = os.environ.get("KANBAI_PROJECT_PATH", "").strip()
if _env_project:
    PROJECT_ROOT: Path = Path(_env_project).resolve()
else:
    # Walk up from cwd to find .kanbai/
    _cwd = Path.cwd().resolve()
    _found: Path | None = None
    _current = _cwd
    while True:
        if (_current / ".kanbai").is_dir():
            _found = _current
            break
        _parent = _current.parent
        if _parent == _current:
            break
        _current = _parent
    PROJECT_ROOT = _found if _found else _cwd

# Legacy alias
REPO_ROOT: Path = PROJECT_ROOT

# ── Board data (inside the target project) ──────────────────────────────────
BOARD_DIR = PROJECT_ROOT / ".kanbai"
ATTACHMENTS_DIR = BOARD_DIR / "attachments"
COUNTER_PATH = BOARD_DIR / "counter.json"
EPICS_PATH = BOARD_DIR / "epics.json"
SETTINGS_PATH = BOARD_DIR / "settings.json"
TRANSITIONS_LOG = BOARD_DIR / "transitions.jsonl"
ACTIVITY_LOG = BOARD_DIR / "activity.jsonl"

# ── Runtime files ───────────────────────────────────────────────────────────
CACHE_DB = BOARD_DIR / ".cache.db"
ENV_FILE = PROJECT_ROOT / ".env"
MODELS_CONFIG_PATH = BOARD_DIR / "models.yml"
UI_STATE_PATH = BOARD_DIR / "ui_state.json"
