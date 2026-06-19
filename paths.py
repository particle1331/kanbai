"""Central filesystem anchors for Kanbai.

All data paths are resolved relative to the **repository root** (the directory
containing this file), not the process working directory. This lets the app and
the MCP server run correctly from any ``cwd`` — e.g. when an MCP client in a
different repo launches ``main.py --mcp``.
"""

from __future__ import annotations

from pathlib import Path

# This file lives at the repo root, so its parent is the repo root.
REPO_ROOT = Path(__file__).resolve().parent

BOARD_DIR = REPO_ROOT / "board_data"
ASSETS_DIR = BOARD_DIR / "assets"
ATTACHMENTS_DIR = ASSETS_DIR / "attachments"
COUNTER_PATH = BOARD_DIR / "counter.json"
EPICS_PATH = BOARD_DIR / "epics.json"
SETTINGS_PATH = BOARD_DIR / "settings.json"
TRANSITIONS_LOG = BOARD_DIR / "transitions.jsonl"

CACHE_DB = REPO_ROOT / ".cache.db"
ENV_FILE = REPO_ROOT / ".env"
MODELS_CONFIG_PATH = BOARD_DIR / "models.yml"
UI_STATE_PATH = BOARD_DIR / "ui_state.json"
