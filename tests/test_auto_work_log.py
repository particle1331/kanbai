from __future__ import annotations

from pathlib import Path

import kanbai.paths as _paths
from kanbai.board import counter as _counter
from kanbai.board import operations
from kanbai.board import settings
from kanbai.board import validation
from kanbai.board.models import Card
from kanbai.board.storage import activity_log, event_log, markdown_store, sqlite_cache


def _configure_board(tmp_path: Path, monkeypatch, *, auto_work_log: bool = True) -> Path:
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
    monkeypatch.setattr(settings, "_cache", {"auto_work_log": auto_work_log})

    sqlite_cache.init_db()
    return project_root


def _make_card(title: str, lane: str = "backlog") -> Card:
    card = operations.create_card(title=title, lane=lane)
    assert card.number is not None
    return card


def test_auto_work_log_records_concise_system_comments(tmp_path: Path, monkeypatch) -> None:
    project_root = _configure_board(tmp_path, monkeypatch, auto_work_log=True)
    (project_root / "src").mkdir(parents=True, exist_ok=True)
    (project_root / "src" / "foo.py").write_text("print('ok')\n", encoding="utf-8")

    alpha = _make_card("Alpha", lane="backlog")
    beta = _make_card("Beta", lane="backlog")
    assert alpha.number is not None
    assert beta.number is not None
    alpha_num = alpha.number
    beta_num = beta.number

    operations.reorder_cards("backlog", [beta.id, alpha.id])
    assert operations.add_comment(alpha.id, "Working note") is not None
    operations.update_card(
        alpha.id,
        title="Alpha v2",
        priority="high",
        tags=["feature"],
        due_date="2026-07-01",
    )
    operations.move_card(alpha.id, "in_progress")
    assert operations.add_file_ref(alpha.id, "src/foo.py") is None
    assert operations.add_checklist_item(alpha.id, "Write tests") is not None
    operations.toggle_checklist_item(alpha.id, 0)
    assert operations.add_checklist_item(alpha.id, "Remove me") is not None
    operations.remove_checklist_item(alpha.id, 1)
    assert operations.add_attachment_bytes(alpha.id, "spec.txt", b"hello") is None
    assert operations.remove_attachment(alpha.id, "spec.txt") is None
    assert operations.link_cards(alpha_num, beta_num, "blocks") is None
    operations.archive_card(alpha.id)
    operations.unarchive_card(alpha.id)

    card = operations.get_card(alpha.id)
    assert card is not None
    system_comments = [c for c in card.comments if c.get("role") == "system"]
    texts = [c.get("text") for c in system_comments]

    assert "Title: \"Alpha\" → \"Alpha v2\"" in texts
    assert "Priority: medium → high" in texts
    assert "Tag added: feature" in texts
    assert "Due date added: 2026-07-01" in texts
    assert "Moved: backlog → in_progress" in texts
    assert "File ref added: src/foo.py" in texts
    assert "Checklist added: Write tests" in texts
    assert "Checklist done: Write tests" in texts
    assert "Checklist removed: Remove me" in texts
    assert "Attachment added: spec.txt" in texts
    assert "Attachment removed: spec.txt" in texts
    assert f"Relationship added: #{beta_num} (blocks)" in texts
    assert "Archived" in texts
    assert "Restored" in texts

    activity_events = activity_log.read_all()
    assert any(e.get("type") == "card_created" and e.get("card_id") == alpha.id for e in activity_events)
    assert any(e.get("type") == "comment_added" and e.get("card_id") == alpha.id and e.get("text") == "Working note" for e in activity_events)
    assert any(e.get("type") == "checklist_item_added" and e.get("card_id") == alpha.id and e.get("item_text") == "Write tests" for e in activity_events)
    assert any(e.get("type") == "card_reordered" and e.get("card_id") == alpha.id for e in activity_events)
    assert any(
        e.get("type") == "checklist_item_deleted"
        and e.get("card_id") == alpha.id
        and e.get("item_text") == "Remove me"
        for e in activity_events
    )
    assert any(
        e.get("type") == "attachment_added"
        and e.get("card_id") == alpha.id
        and e.get("filename") == "spec.txt"
        for e in activity_events
    )
    assert any(
        e.get("type") == "attachment_removed"
        and e.get("card_id") == alpha.id
        and e.get("filename") == "spec.txt"
        for e in activity_events
    )

    raw_card = (project_root / ".kanbai" / "in_progress" / f"{alpha.id}.md").read_text(encoding="utf-8")
    assert "role: system" in raw_card

    linked = operations.get_card(beta.id)
    assert linked is not None
    linked_texts = [c.get("text") for c in linked.comments if c.get("role") == "system"]
    assert f"Relationship added: #{alpha_num} (blocked_by)" in linked_texts


def test_auto_work_log_can_be_disabled_globally(tmp_path: Path, monkeypatch) -> None:
    _configure_board(tmp_path, monkeypatch, auto_work_log=False)

    card = _make_card("Alpha", lane="backlog")
    operations.update_card(card.id, priority="high", tags=["feature"])
    operations.move_card(card.id, "in_progress")

    refreshed = operations.get_card(card.id)
    assert refreshed is not None
    assert refreshed.comments == []


def test_card_deletion_is_recorded_in_global_activity_log(tmp_path: Path, monkeypatch) -> None:
    _configure_board(tmp_path, monkeypatch, auto_work_log=False)

    card = _make_card("Delete me", lane="backlog")
    assert operations.delete_card(card.id) is True

    events = activity_log.read_all()
    deleted = [e for e in events if e.get("type") == "card_deleted" and e.get("card_id") == card.id]
    assert deleted, events
    assert deleted[-1].get("card_title") == "Delete me"
    assert deleted[-1].get("card_lane") == "backlog"
