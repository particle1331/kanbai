from __future__ import annotations

from pathlib import Path

import pytest

import kanbai.paths as _paths
from kanbai.board.validation import validate_file_ref


class TestValidateFileRef:
    def test_accepts_existing_repo_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_paths, "PROJECT_ROOT", tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "board.py").write_text("print('ok')\n", encoding="utf-8")

        validate_file_ref("src/board.py", 0)

    def test_accepts_existing_repo_file_with_line_range(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_paths, "PROJECT_ROOT", tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "board.py").write_text("print('ok')\n", encoding="utf-8")

        validate_file_ref("src/board.py:10-30", 0)

    def test_rejects_missing_repo_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_paths, "PROJECT_ROOT", tmp_path)

        with pytest.raises(ValueError, match="file not found in target repository"):
            validate_file_ref("src/missing.py", 0)

    def test_rejects_directory_target(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_paths, "PROJECT_ROOT", tmp_path)
        (tmp_path / "src").mkdir()

        with pytest.raises(ValueError, match="target is not a file"):
            validate_file_ref("src", 0)

    def test_uses_target_repo_not_other_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target_repo = tmp_path / "target"
        kanbai_repo = tmp_path / "kanbai"
        target_repo.mkdir()
        (kanbai_repo / "src").mkdir(parents=True)
        (kanbai_repo / "src" / "board.py").write_text("print('wrong root')\n", encoding="utf-8")
        monkeypatch.setattr(_paths, "PROJECT_ROOT", target_repo)

        with pytest.raises(ValueError, match="file not found in target repository"):
            validate_file_ref("src/board.py", 0)
