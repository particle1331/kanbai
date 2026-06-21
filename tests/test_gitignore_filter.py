"""Tests for GitignoreFilter and the agent's list_files / read_file tools.

All tests use a temporary directory so the real project's .gitignore is
never consulted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kanbai.agent.gitignore import GitignoreFilter


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _make_tree(root: Path, structure: dict) -> None:
    """Recursively create files/dirs from a nested dict.

    Keys ending with '/' are directories (value is another dict).
    Other keys are files (value is the file content string).
    """
    for name, content in structure.items():
        path = root / name
        if isinstance(content, dict):
            path.mkdir(parents=True, exist_ok=True)
            _make_tree(path, content)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")


def _call_tool(name: str, args: dict, project_root: Path) -> dict:
    """Invoke list_files / read_file directly, bypassing the real PROJECT_ROOT."""
    # Patch PROJECT_ROOT at import time via monkeypatch-style env trick
    import kanbai.paths as _paths
    original = _paths.PROJECT_ROOT
    _paths.PROJECT_ROOT = project_root
    # Also patch the module-level reference used inside the tool handler
    import kanbai.agent.chat as _chat
    try:
        result_str = _chat._dispatch_tool(name, args)  # type: ignore[attr-defined]
    finally:
        _paths.PROJECT_ROOT = original
    return json.loads(result_str)


# ────────────────────────────────────────────────────────────────────────────
# GitignoreFilter unit tests
# ────────────────────────────────────────────────────────────────────────────

class TestGitignoreFilter:
    def test_basic_extension_pattern(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("*.env\n")
        f = GitignoreFilter(tmp_path)
        assert f.is_ignored(tmp_path / ".env")
        assert f.is_ignored(tmp_path / "secrets.env")
        assert not f.is_ignored(tmp_path / "main.py")

    def test_directory_pattern(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text(".venv/\nbuild/\n")
        f = GitignoreFilter(tmp_path)
        (tmp_path / ".venv").mkdir()
        (tmp_path / "build").mkdir()
        assert f.is_ignored(tmp_path / ".venv")
        assert f.is_ignored(tmp_path / "build")
        assert not f.is_ignored(tmp_path / "src")

    def test_specific_file(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text(".cache.db\nui_state.json\n")
        f = GitignoreFilter(tmp_path)
        assert f.is_ignored(tmp_path / ".cache.db")
        assert f.is_ignored(tmp_path / "ui_state.json")
        assert not f.is_ignored(tmp_path / "README.md")

    def test_nested_gitignore(self, tmp_path: Path) -> None:
        """A nested .gitignore inside a subdirectory is also respected."""
        (tmp_path / ".gitignore").write_text("*.log\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / ".gitignore").write_text("secret.txt\n")
        f = GitignoreFilter(tmp_path)
        assert f.is_ignored(sub / "secret.txt")
        assert f.is_ignored(tmp_path / "debug.log")
        assert not f.is_ignored(sub / "README.md")

    def test_path_outside_repo_is_blocked(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("")
        f = GitignoreFilter(tmp_path)
        assert f.is_ignored(Path("/etc/passwd"))

    def test_empty_gitignore(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("")
        f = GitignoreFilter(tmp_path)
        assert not f.is_ignored(tmp_path / "main.py")

    def test_no_gitignore(self, tmp_path: Path) -> None:
        f = GitignoreFilter(tmp_path)
        assert not f.is_ignored(tmp_path / "anything.py")

    def test_wildcard_directory(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        f = GitignoreFilter(tmp_path)
        assert f.is_ignored(tmp_path / "src" / "__pycache__")
        assert f.is_ignored(tmp_path / "app.pyc")
        assert not f.is_ignored(tmp_path / "app.py")

    def test_reload_picks_up_changes(self, tmp_path: Path) -> None:
        gi = tmp_path / ".gitignore"
        gi.write_text("")
        f = GitignoreFilter(tmp_path)
        assert not f.is_ignored(tmp_path / "secret.key")
        gi.write_text("*.key\n")
        f.reload()
        assert f.is_ignored(tmp_path / "secret.key")

    def test_venv_gitignore_wildstar_does_not_poison_filter(self, tmp_path: Path) -> None:
        """A .venv/.gitignore containing bare '*' must NOT cause real project
        files to be reported as ignored."""
        (tmp_path / ".gitignore").write_text(".env\n")
        venv = tmp_path / ".venv"
        venv.mkdir()
        (venv / ".gitignore").write_text("*\n")  # typical virtualenv gitignore
        f = GitignoreFilter(tmp_path)
        assert not f.is_ignored(tmp_path / "README.md")
        assert not f.is_ignored(tmp_path / "src")
        assert f.is_ignored(tmp_path / ".env")   # root gitignore still works

    def test_hidden_parent_gitignore_is_skipped(self, tmp_path: Path) -> None:
        """gitignore files inside hidden directories are not loaded."""
        (tmp_path / ".gitignore").write_text("")
        hidden = tmp_path / ".pi" / "npm"
        hidden.mkdir(parents=True)
        (hidden / ".gitignore").write_text("*\n!.gitignore\n")
        f = GitignoreFilter(tmp_path)
        assert not f.is_ignored(tmp_path / "main.py")


# ────────────────────────────────────────────────────────────────────────────
# Integration tests for list_files / read_file tool handlers
# ────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """Create a minimal fake project that mirrors a real kanbai repo."""
    _make_tree(tmp_path, {
        ".gitignore": ".env\n*.log\nbuild/\n.cache.db\n",
        ".env": "SECRET=hunter2",
        "README.md": "# Hello",
        "app.log": "log data",
        "main.py": "print('hi')",
        "build": {
            "output.js": "bundled",
        },
        "src": {
            "app.py": "# app",
            "utils.py": "# utils",
        },
        ".kanbai": {
            ".cache.db": "db data",
            "backlog": {
                "card.md": "# card",
            },
        },
    })
    return tmp_path


class TestListFilesTool:
    """Tests for the list_files tool handler."""

    def _list(self, fake_repo: Path, **kwargs) -> dict:
        return _call_tool("list_files", kwargs, fake_repo)

    def test_tree_excludes_gitignored_files(self, fake_repo: Path) -> None:
        result = self._list(fake_repo)
        assert "error" not in result
        tree = result["tree"]
        assert ".env" not in tree
        assert "app.log" not in tree

    def test_tree_excludes_gitignored_dirs(self, fake_repo: Path) -> None:
        result = self._list(fake_repo)
        tree = result["tree"]
        # 'build/' should not appear as a listed directory
        assert "build/" not in tree

    def test_tree_includes_normal_files(self, fake_repo: Path) -> None:
        result = self._list(fake_repo)
        tree = result["tree"]
        assert "README.md" in tree
        assert "main.py" in tree

    def test_subpath_request_on_clean_dir(self, fake_repo: Path) -> None:
        result = self._list(fake_repo, path="src")
        assert "error" not in result
        tree = result["tree"]
        assert "app.py" in tree
        assert "utils.py" in tree

    def test_subpath_request_on_gitignored_dir_is_denied(self, fake_repo: Path) -> None:
        result = self._list(fake_repo, path="build")
        assert result.get("error") == "access_denied"

    def test_depth_limit_respected(self, fake_repo: Path) -> None:
        # depth=0: only the root directory header is emitted, no recursion
        result = self._list(fake_repo, depth=0)
        tree = result["tree"]
        # src/ contents should NOT appear (too deep at depth=0)
        assert "app.py" not in tree
        assert "utils.py" not in tree

    def test_hidden_files_still_excluded(self, fake_repo: Path) -> None:
        """Files starting with '.' that aren't gitignored should still be
        hidden (existing behaviour). The gitignore check is additive."""
        result = self._list(fake_repo)
        tree = result["tree"]
        assert ".gitignore" not in tree


class TestReadFileTool:
    """Tests for the read_file tool handler."""

    def _read(self, fake_repo: Path, path: str, **kwargs) -> dict:
        return _call_tool("read_file", {"path": path, **kwargs}, fake_repo)

    def test_read_normal_file(self, fake_repo: Path) -> None:
        result = self._read(fake_repo, "README.md")
        assert "error" not in result
        assert result["lines"][0] == "# Hello"

    def test_read_gitignored_file_is_denied(self, fake_repo: Path) -> None:
        result = self._read(fake_repo, ".env")
        assert result.get("error") == "access_denied"

    def test_read_gitignored_log_file_is_denied(self, fake_repo: Path) -> None:
        result = self._read(fake_repo, "app.log")
        assert result.get("error") == "access_denied"

    def test_read_missing_non_ignored_file(self, fake_repo: Path) -> None:
        result = self._read(fake_repo, "does_not_exist.py")
        assert result.get("error") == "file_not_found"

    def test_read_file_in_gitignored_dir(self, fake_repo: Path) -> None:
        """Files inside a gitignored directory should be blocked."""
        result = self._read(fake_repo, "build/output.js")
        assert result.get("error") == "access_denied"

    def test_max_lines_truncation(self, fake_repo: Path) -> None:
        # main.py has 1 line; ask for max 0 → truncated
        result = self._read(fake_repo, "main.py", max_lines=0)
        assert result.get("truncated") is True
        assert result["lines"] == []

    def test_path_traversal_blocked(self, fake_repo: Path) -> None:
        """../../etc/passwd style traversal should be blocked."""
        result = self._read(fake_repo, "../../etc/passwd")
        assert result.get("error") == "access_denied"
