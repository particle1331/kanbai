from __future__ import annotations

from pathlib import Path

import kanbai.paths as _paths
from kanbai.board import operations
from kanbai.board.models import Card
from kanbai.board.storage import markdown_store
from kanbai.board.validation import display_file_ref, normalize_file_ref


def test_normalize_and_display_file_refs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(_paths, "PROJECT_ROOT", tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("print('ok')\n", encoding="utf-8")

    stored = normalize_file_ref("src/foo.py:10-30")
    assert stored == f"{tmp_path / 'src' / 'foo.py'}:10-30"
    assert display_file_ref(stored) == "src/foo.py:10-30"


def test_add_and_remove_file_ref_store_absolute_paths(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path
    board_dir = project_root / ".kanbai"
    (board_dir / "backlog").mkdir(parents=True)
    (project_root / "src").mkdir()
    (project_root / "src" / "foo.py").write_text("print('ok')\n", encoding="utf-8")

    monkeypatch.setattr(_paths, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(markdown_store, "BOARD_DIR", board_dir)
    monkeypatch.setattr(operations, "BOARD_DIR", board_dir)
    monkeypatch.setattr(operations.sqlite_cache, "get_card_by_id", lambda _cid: {"lane": "backlog"})
    monkeypatch.setattr(operations.sqlite_cache, "upsert_card", lambda _card: None)

    card = Card(
        id="card-1",
        title="test",
        lane="backlog",
        created_at="now",
        updated_at="now",
    )
    markdown_store.write_card(card)

    err = operations.add_file_ref("card-1", "src/foo.py")
    assert err is None

    loaded = operations.get_card("card-1")
    assert loaded is not None
    assert loaded.file_refs == [str(project_root / "src" / "foo.py")]

    err = operations.remove_file_ref("card-1", "src/foo.py")
    assert err is None
    loaded = operations.get_card("card-1")
    assert loaded is not None
    assert loaded.file_refs == []
