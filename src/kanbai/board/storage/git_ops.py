"""Git operations for .kanbai/ directory synchronisation.

Provides dirty-state detection and a commit+push helper used by the UI's
git sync badge.  All operations are scoped to the ``.kanbai/`` directory so
that Kanbai never accidentally stages unrelated project files.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from kanbai.paths import BOARD_DIR, REPO_ROOT


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    """Run a git sub-command and return the CompletedProcess (never raises)."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        # Return a fake failed result so callers never have to handle exceptions.
        result = subprocess.CompletedProcess(args=["git", *args], returncode=1)
        result.stdout = ""
        result.stderr = str(exc)
        return result


def _board_rel() -> str:
    """Return board_data path relative to repo root (for git commands)."""
    return str(BOARD_DIR.relative_to(REPO_ROOT))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_git_repo() -> bool:
    """Return True if REPO_ROOT is inside a git work-tree."""
    r = _run("rev-parse", "--is-inside-work-tree")
    return r.returncode == 0


def is_dirty() -> bool:
    """Return True if board_data has uncommitted changes (staged or unstaged)."""
    r = _run("status", "--porcelain", _board_rel())
    return bool(r.stdout.strip())


def sync_push() -> tuple[bool, str]:
    """Stage board_data changes, commit, and push.

    Returns ``(success, message)``.  The message is human-readable and shown
    in the UI tooltip on failure.

    Behaviour notes
    ---------------
    * If there is nothing to commit, we still attempt ``git push`` in case
      there are unpushed commits from a previous session.
    * If no remote is configured the push is skipped and we report success so
      the badge doesn't stay red on local-only repos.
    """
    if not is_git_repo():
        return False, "Not a git repository"

    rel = _board_rel()
    dirty = is_dirty()

    if dirty:
        add = _run("add", rel)
        if add.returncode != 0:
            return False, add.stderr.strip() or "git add failed"

        commit = _run("commit", "-m", "chore: sync board data [kanbai]")
        if commit.returncode != 0:
            # Nothing staged after add is not an error we want to surface.
            if "nothing to commit" not in commit.stdout and "nothing to commit" not in commit.stderr:
                return False, commit.stderr.strip() or "git commit failed"

    # Skip push when no remote is configured.
    remote_check = _run("remote")
    if not remote_check.stdout.strip():
        return True, "Committed (no remote configured)"

    push = _run("push")
    if push.returncode != 0:
        return False, push.stderr.strip() or "git push failed"

    return True, "Synced"
