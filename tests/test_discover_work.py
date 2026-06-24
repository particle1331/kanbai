from __future__ import annotations

import json
from pathlib import Path

import kanbai.paths as _paths
from kanbai.board import counter as _counter
from kanbai.board import epics as _epics
from kanbai.board import operations
from kanbai.board.storage import activity_log, event_log, markdown_store, sqlite_cache
from kanbai.mcp_server.server import call_tool_sync


def _configure_board(tmp_path: Path, monkeypatch) -> Path:
    project_root = tmp_path / "project"
    board_dir = project_root / ".kanbai"
    attachments_dir = board_dir / "attachments"
    for lane in ["backlog", "in_progress", "review", "done"]:
        (board_dir / lane).mkdir(parents=True, exist_ok=True)
    attachments_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(_paths, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(_paths, "BOARD_DIR", board_dir)
    monkeypatch.setattr(_paths, "ATTACHMENTS_DIR", attachments_dir)
    monkeypatch.setattr(_paths, "COUNTER_PATH", board_dir / "counter.json")
    monkeypatch.setattr(_paths, "EPICS_PATH", board_dir / "epics.json")
    monkeypatch.setattr(_paths, "SETTINGS_PATH", board_dir / "settings.json")
    monkeypatch.setattr(_paths, "TRANSITIONS_LOG", board_dir / "transitions.jsonl")
    monkeypatch.setattr(_paths, "ACTIVITY_LOG", board_dir / "activity.jsonl")
    monkeypatch.setattr(_paths, "CACHE_DB", board_dir / ".cache.db")
    monkeypatch.setattr(_paths, "UI_STATE_PATH", board_dir / "ui_state.json")

    monkeypatch.setattr(markdown_store, "BOARD_DIR", board_dir)
    monkeypatch.setattr(operations, "BOARD_DIR", board_dir)
    monkeypatch.setattr(operations, "ATTACHMENTS_DIR", attachments_dir)
    monkeypatch.setattr(sqlite_cache, "DB_PATH", board_dir / ".cache.db")
    monkeypatch.setattr(_counter, "COUNTER_PATH", board_dir / "counter.json")
    monkeypatch.setattr(event_log, "TRANSITIONS_LOG", board_dir / "transitions.jsonl")
    monkeypatch.setattr(activity_log, "ACTIVITY_LOG", board_dir / "activity.jsonl")
    monkeypatch.setattr(_epics, "EPICS_PATH", board_dir / "epics.json")

    sqlite_cache.init_db()
    return project_root


def test_discover_work_creates_linked_backlog_card_with_discovered_tag(tmp_path: Path, monkeypatch) -> None:
    _configure_board(tmp_path, monkeypatch)
    _epics.create_epic("kanbai-dev", "Kanbai internal workstream")

    source = operations.create_card(title="Source", lane="in_progress", epic="kanbai-dev")
    assert source.number is not None

    payload = json.loads(
        call_tool_sync(
            "discover_work",
            {
                "from_card_id": source.id,
                "title": "Follow-up bug",
                "description": "Fix the adjacent issue",
                "priority": "high",
                "tags": ["bug"],
            },
        )
    )

    assert payload["title"] == "Follow-up bug"
    assert payload["lane"] == "backlog"
    assert payload["epic"] == "kanbai-dev"
    assert payload["link_type"] == "relates_to"

    discovered = operations.get_card(payload["id"])
    assert discovered is not None
    assert discovered.tags[0] == "discovered"
    assert "bug" in discovered.tags
    assert any(link["type"] == "relates_to" and link["number"] == source.number for link in discovered.links)

    refreshed_source = operations.get_card(source.id)
    assert refreshed_source is not None
    assert any(
        comment.get("text") == f"Discovered related work: #{payload['number']} — Follow-up bug"
        and comment.get("role") == "note"
        for comment in refreshed_source.comments
    )
    assert any(
        comment.get("text") == f"Discovered while working on #{source.number}"
        and comment.get("role") == "system"
        for comment in discovered.comments
    )
