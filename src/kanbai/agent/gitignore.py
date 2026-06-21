"""Gitignore-aware path filtering for the agent's file-access tools.

Collects all ``.gitignore`` files from the repo root down to the directory
being queried and uses :mod:`pathspec` to decide whether a given path should
be hidden from the agent.

Design decisions
~~~~~~~~~~~~~~~~
* Only the **project-root** ``.gitignore`` is required to exist; deeper
  ``.gitignore`` files are picked up automatically when traversing.
* The check is *relative* to the repo root so patterns like ``*.env`` and
  ``/build`` are evaluated correctly.
* This module is intentionally side-effect-free — callers create a
  :class:`GitignoreFilter` once and reuse it across many path checks.
"""

from __future__ import annotations

import os
from pathlib import Path

import pathspec


class GitignoreFilter:
    """Loads all ``.gitignore`` files under *root* and exposes
    :meth:`is_ignored` to check individual paths.

    Parameters
    ----------
    root:
        Absolute path to the project root (directory that contains
        ``.gitignore`` and ``.kanbai/``).
    """

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._spec = self._load(self._root)

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    # Directories we never descend into when collecting .gitignore files.
    # These often contain their own .gitignore files with very broad patterns
    # (e.g. `.venv/.gitignore` contains bare `*`) that would incorrectly
    # shadow the entire project.
    _SKIP_DIRS: frozenset[str] = frozenset({
        ".venv", "venv", ".env", "env",
        "node_modules", "__pycache__",
        ".git", ".hg", ".svn",
        ".tox", ".nox",
        "build", "dist", ".eggs",
    })

    @classmethod
    def _load(cls, root: Path) -> pathspec.PathSpec:
        """Walk the tree and collect ``.gitignore`` patterns.

        Hidden directories (name starts with ``'.'``) and well-known noisy
        directories (e.g. ``.venv``, ``node_modules``) are skipped so that
        package-internal ``.gitignore`` files with broad patterns like ``*``
        cannot accidentally match real project files.
        """
        lines: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            cur = Path(dirpath)
            # Prune hidden dirs and known noisy dirs in-place so os.walk
            # never descends into them.
            dirnames[:] = [
                d for d in dirnames
                if not d.startswith(".")
                and d not in cls._SKIP_DIRS
            ]
            if ".gitignore" in filenames:
                try:
                    lines.extend(
                        (cur / ".gitignore")
                        .read_text(encoding="utf-8", errors="replace")
                        .splitlines()
                    )
                except OSError:
                    pass
        return pathspec.PathSpec.from_lines("gitignore", lines)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def is_ignored(self, path: Path) -> bool:
        """Return ``True`` when *path* matches any loaded gitignore pattern.

        *path* may be absolute or relative; it is always converted to a
        ``root``-relative POSIX string before matching.
        """
        path = path.resolve()
        try:
            rel = path.relative_to(self._root)
        except ValueError:
            # Path is outside the repo — block it.
            return True
        return self._spec.match_file(rel.as_posix()) or self._spec.match_file(rel.as_posix() + "/")

    def reload(self) -> None:
        """Re-read all ``.gitignore`` files (useful in long-running servers)."""
        self._spec = self._load(self._root)
