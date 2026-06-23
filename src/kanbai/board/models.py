from __future__ import annotations

from dataclasses import dataclass, field


class _Unset:
    """Sentinel – distinguishes 'leave unchanged' from an explicit None."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<UNSET>"


_UNSET = _Unset()

LANES = ["backlog", "in_progress", "review", "done"]

LANE_LABELS = {
    "backlog": "Backlog",
    "in_progress": "In Progress",
    "review": "Review",
    "done": "Done",
}


@dataclass
class Card:
    id: str
    title: str
    lane: str
    priority: str = "medium"
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    archived: bool = False
    description: str = ""
    attachments: list[str] = field(default_factory=list)
    epic: str | None = None
    number: int | None = None
    links: list[dict] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)
    assigned_to: list[str] = field(default_factory=list)
    position: float = 0.0
    due_date: str | None = None
    checklist: list[dict] = field(default_factory=list)
    file_refs: list[str] = field(default_factory=list)

    def to_frontmatter(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "lane": self.lane,
            "priority": self.priority,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived": self.archived,
            "attachments": self.attachments,
            "epic": self.epic,
            "number": self.number,
            "links": self.links,
            "comments": self.comments,
            "assigned_to": self.assigned_to,
            "position": self.position,
            "due_date": self.due_date,
            "checklist": self.checklist,
            "file_refs": self.file_refs,
        }

    @classmethod
    def from_frontmatter(cls, meta: dict, body: str = "") -> Card:
        from kanbai.board.validation import coerce_stored_file_ref

        return cls(
            id=str(meta["id"]),
            title=str(meta["title"]),
            lane=str(meta.get("lane") or meta.get("column")),
            priority=str(meta.get("priority", "medium")),
            tags=list(meta.get("tags") or []),
            created_at=str(meta.get("created_at", "")),
            updated_at=str(meta.get("updated_at", "")),
            archived=bool(meta.get("archived", False)),
            description=body.strip(),
            attachments=list(meta.get("attachments") or []),
            epic=meta.get("epic") or None,
            number=int(meta["number"]) if meta.get("number") is not None else None,
            links=list(meta.get("links") or []),
            comments=list(meta.get("comments") or []),
            assigned_to=list(meta.get("assigned_to") or []),
            position=float(meta.get("position") or 0.0),
            due_date=meta.get("due_date") or None,
            checklist=list(meta.get("checklist") or []),
            file_refs=[coerce_stored_file_ref(ref) for ref in list(meta.get("file_refs") or [])],
        )
