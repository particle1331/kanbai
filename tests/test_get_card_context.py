from __future__ import annotations

import json
from pathlib import Path

import kanbai.paths as _paths
from kanbai.board import counter as _counter
from kanbai.board import operations
from kanbai.board import epics as _epics
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


def test_get_card_context_bundles_card_comment_link_epic_and_activity(tmp_path: Path, monkeypatch) -> None:
    project_root = _configure_board(tmp_path, monkeypatch)
    (project_root / "src").mkdir(parents=True, exist_ok=True)
    (project_root / "src" / "foo.py").write_text("print('ok')\n", encoding="utf-8")

    _epics.create_epic("kanbai-dev", "Kanbai internal workstream")
    _epics.add_epic_comment("kanbai-dev", "Epic kickoff")

    alpha = operations.create_card(
        title="Alpha",
        lane="backlog",
        epic="kanbai-dev",
        file_refs=["src/foo.py"],
    )
    beta = operations.create_card(title="Beta", lane="backlog", epic="kanbai-dev")

    assert alpha.number is not None
    assert beta.number is not None

    operations.add_comment(alpha.id, "Please keep this concise", role="instruction")
    operations.add_comment(alpha.id, "Drafted the approach", role="update")
    operations.add_comment(alpha.id, "Chose SQLite", role="decision")
    operations.add_comment(alpha.id, "Waiting on API shape", role="blocker")
    operations.add_comment(alpha.id, "General note", role="note")
    operations.add_checklist_item(alpha.id, "Write tests")
    operations.add_checklist_item(alpha.id, "Update docs")
    operations.toggle_checklist_item(alpha.id, 0)
    operations.link_cards(alpha.number, beta.number, "blocks")
    operations.move_card(alpha.id, "in_progress")

    payload = json.loads(call_tool_sync("get_card_context", {"card_id": alpha.id}))

    assert payload["card"]["title"] == "Alpha"
    assert payload["card"]["lane"] == "in_progress"
    assert payload["card"]["checklist_progress"] == "1/2"
    assert payload["card"]["file_refs"] == ["src/foo.py"]

    comments = payload["comments"]
    assert comments["instructions"][0]["text"] == "Please keep this concise"
    assert comments["updates"][0]["text"] == "Drafted the approach"
    assert comments["decisions"][0]["text"] == "Chose SQLite"
    assert comments["blockers"][0]["text"] == "Waiting on API shape"
    assert any(item["text"] == "General note" for item in comments["notes"])

    assert payload["linked_cards"] == [
        {
            "number": beta.number,
            "title": "Beta",
            "lane": "backlog",
            "epic": "kanbai-dev",
            "archived": False,
            "link_type": "blocks",
        }
    ]

    assert payload["epic"]["name"] == "kanbai-dev"
    assert payload["epic"]["description"] == "Kanbai internal workstream"
    assert payload["epic"]["recent_comments"] == [
        {"id": payload["epic"]["recent_comments"][0]["id"], "text": "Epic kickoff", "created_at": payload["epic"]["recent_comments"][0]["created_at"]}
    ]

    assert any(item["action"] == "system note" and "Moved:" in item["detail"] for item in payload["recent_activity"])
    assert any(item["action"] == "system note" and "Relationship added:" in item["detail"] for item in payload["recent_activity"])
