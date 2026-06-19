from __future__ import annotations

import shutil
from pathlib import Path

import frontmatter

from board.models import Card
from paths import BOARD_DIR


def _card_path(card: Card) -> Path:
    return BOARD_DIR / card.lane / f"{card.id}.md"


def _all_md_files() -> list[Path]:
    return [p for p in BOARD_DIR.rglob("*.md") if p.parent.name != "assets"]


def write_card(card: Card) -> None:
    dest = _card_path(card)
    dest.parent.mkdir(parents=True, exist_ok=True)
    post = frontmatter.Post(card.description, **card.to_frontmatter())
    dest.write_text(frontmatter.dumps(post), encoding="utf-8")


def read_card(card_id: str) -> Card | None:
    # Fast path: look up the lane from the SQLite cache to build the path directly.
    # Import here to avoid a circular dependency at module load time.
    from board.storage import sqlite_cache
    cached = sqlite_cache.get_card_by_id(card_id)
    if cached:
        direct = BOARD_DIR / cached["lane"] / f"{card_id}.md"
        if direct.exists():
            post = frontmatter.load(str(direct))
            return Card.from_frontmatter(post.metadata, post.content)
    # Fallback: scan all files (handles cards not yet in the cache).
    for path in _all_md_files():
        if path.stem == card_id:
            post = frontmatter.load(str(path))
            return Card.from_frontmatter(post.metadata, post.content)
    return None


def move_card_file(card: Card, old_lane: str) -> None:
    old_path = BOARD_DIR / old_lane / f"{card.id}.md"
    new_path = _card_path(card)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    if old_path.exists():
        shutil.move(str(old_path), str(new_path))
    # Always rewrite so the frontmatter lane field stays in sync with the file location.
    write_card(card)


def load_all_cards() -> list[Card]:
    cards: list[Card] = []
    for path in _all_md_files():
        try:
            post = frontmatter.load(str(path))
            cards.append(Card.from_frontmatter(post.metadata, post.content))
        except Exception:
            pass
    return cards
