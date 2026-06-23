from __future__ import annotations

import json
from pathlib import Path

from kanbai import paths as _paths
from kanbai.board import settings as _settings
from kanbai.mcp_server.server import call_tool_sync, get_project_root


def test_get_project_root_returns_persisted_board_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(_settings, "_cache", {"project_root": str(tmp_path)})

    result = get_project_root()

    assert result == {"root": str(tmp_path)}


def test_get_project_root_falls_back_to_runtime_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(_settings, "_cache", {"project_root": ""})
    monkeypatch.setattr(_paths, "PROJECT_ROOT", tmp_path)

    payload = json.loads(call_tool_sync("get_project_root", {}))

    assert payload == {"root": str(tmp_path)}
