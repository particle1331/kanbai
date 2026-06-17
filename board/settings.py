from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

import yaml

from paths import MODELS_CONFIG_PATH, SETTINGS_PATH


@dataclass(frozen=True)
class ModelDef:
    """One entry from models.yml — a UI handle mapped to an env var."""
    handle: str       # shown in the dropdown (e.g. "nano")
    env_var: str      # env var holding the actual deployment name

    @property
    def deployment(self) -> str:
        """The Azure deployment name read from the environment."""
        return os.environ.get(self.env_var) or self.handle


def load_models() -> list[ModelDef]:
    """Load model definitions from board_data/models.yml.

    Raises RuntimeError if the file is missing, invalid, or contains no entries.
    """
    try:
        raw = yaml.safe_load(MODELS_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise RuntimeError(f"Cannot read {MODELS_CONFIG_PATH}: {exc}") from exc
    entries = raw.get("models") or []
    defs = []
    for e in entries:
        if "handle" not in e or "env_var" not in e:
            continue
        handle = str(e["handle"])
        if len(handle) >= 16:
            raise RuntimeError(
                f"{MODELS_CONFIG_PATH}: handle {handle!r} must be fewer than 16 characters."
            )
        defs.append(ModelDef(handle=handle, env_var=str(e["env_var"])))
    if not defs:
        raise RuntimeError(f"{MODELS_CONFIG_PATH} contains no valid model entries.")
    return defs


def find_model(handle: str, models: Sequence[ModelDef] | None = None) -> ModelDef:
    """Return the ModelDef for the given handle, or the first model as default."""
    pool = models if models is not None else load_models()
    for m in pool:
        if m.handle == handle:
            return m
    return pool[0]

class ReasoningEffort(str, Enum):
    """Reasoning effort levels accepted by the Azure OpenAI API.

    Each member is also a plain ``str`` equal to its API value so it can be
    passed directly to ``client.chat.completions.create(reasoning_effort=...)``,
    compared with ``==``, or stored/loaded as a JSON string without conversion.
    """
    NONE   = "none"
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"

    @property
    def label(self) -> str:
        """Short display label used in the UI pill."""
        return {"none": "None", "low": "Low", "medium": "Med", "high": "High"}[self.value]

    @property
    def tooltip(self) -> str:
        """Longer tooltip shown on hover."""
        return {
            "none":   "No reasoning (fastest)",
            "low":    "Low reasoning effort",
            "medium": "Medium reasoning effort",
            "high":   "High reasoning effort (slowest)",
        }[self.value]

    @classmethod
    def from_str(cls, value: str) -> "ReasoningEffort":
        """Parse a stored string, falling back to NONE on unknown values."""
        try:
            return cls(value)
        except ValueError:
            return cls.NONE

_DEFAULTS: dict = {
    # Display stored UTC timestamps in the local timezone (True) or as UTC (False).
    # App-wide: affects the analytics board and kanban card dates.
    "use_local_tz": True,
    # AI chat settings
    "reasoning_effort": ReasoningEffort.NONE.value,  # ReasoningEffort enum value
    "max_completion_tokens": 2048,
    "stream_response": True,
    # Max model turns (each may issue tool calls) before control returns to the user.
    "max_agent_steps": 8,
}

# Ordered list of allowed max_completion_tokens values shown in the settings UI.
# Keep this small — values above 4096 are wasteful for a Kanban chat agent.
MAX_COMPLETION_TOKEN_OPTIONS: tuple[int, ...] = (512, 1024, 2048, 4096)

# Ordered list of allowed max_agent_steps values shown in the settings UI.
MAX_AGENT_STEP_OPTIONS: tuple[int, ...] = (4, 8, 12, 20)

_cache: dict | None = None


def reload() -> None:
    """Discard the in-memory cache so the next read loads fresh from disk."""
    global _cache
    _cache = None


def _load() -> dict:
    global _cache
    if _cache is None:
        data: dict = {}
        if SETTINGS_PATH.exists():
            try:
                data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                data = {}
        _cache = {**_DEFAULTS, **data}
    return _cache


def _save() -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(_load(), indent=2), encoding="utf-8")


def use_local_tz() -> bool:
    """Whether dates/times are shown in the local timezone (vs UTC)."""
    return bool(_load()["use_local_tz"])


def set_use_local_tz(value: bool) -> None:
    _load()["use_local_tz"] = bool(value)
    _save()


def reasoning_effort() -> ReasoningEffort:
    """Reasoning effort level passed to the AI."""
    return ReasoningEffort.from_str(_load().get("reasoning_effort", ReasoningEffort.NONE.value))


def set_reasoning_effort(value: ReasoningEffort) -> None:
    _load()["reasoning_effort"] = value.value
    _save()


def max_completion_tokens() -> int:
    """Maximum tokens the AI may produce in a single response."""
    return int(_load().get("max_completion_tokens", 2048))


def set_max_completion_tokens(value: int) -> None:
    _load()["max_completion_tokens"] = int(value)
    _save()


def max_agent_steps() -> int:
    """Max model turns (each may issue tool calls) before control returns to the user."""
    return int(_load().get("max_agent_steps", 8))


def set_max_agent_steps(value: int) -> None:
    _load()["max_agent_steps"] = int(value)
    _save()


def stream_response() -> bool:
    """Whether AI responses are streamed token-by-token (vs delivered all at once)."""
    return bool(_load().get("stream_response", True))


def set_stream_response(value: bool) -> None:
    _load()["stream_response"] = bool(value)
    _save()


# ── Default tags ─────────────────────────────────────────────────────────────────
# Shown as quick-pick chips in the card dialog. These map to the spec's
# "type" dimension: bugfix / feature / enhancement.
DEFAULT_TAGS: list[str] = ["bugfix", "feature", "enhancement"]

# ── Project health thresholds ──────────────────────────────────────────────────
# Centralized thresholds for the analytics "Project Health" indicator.
#
# Health is the WORST band across several open-work signals. Each signal maps a
# value to one of three bands via ``band()``:
#
#   value <= watch  → "good"   (green)
#   value <= bad    → "watch"  (amber)
#   value >  bad    → "bad"    (red)
#
# For ratio metrics (pull rate, throughput), the scale is *inverted*: a lower
# ratio is worse, so band_inverted() is used.
#
# Adjust the threshold pairs below to match your team's rhythm.

# Average open-task age (days). Open = non-archived, non-done cards.
HEALTH_AVG_AGE_WATCH_DAYS: float = 14.0
HEALTH_AVG_AGE_BAD_DAYS: float = 28.0

# Oldest open-task age (days) — the single most stale card.
HEALTH_MAX_AGE_WATCH_DAYS: float = 56.0
HEALTH_MAX_AGE_BAD_DAYS: float = 84.0

# Days since the last completion (only counts when open cards exist).
HEALTH_LAST_DONE_WATCH_DAYS: float = 14.0
HEALTH_LAST_DONE_BAD_DAYS: float = 28.0

# Days since the last column transition (only counts when open cards exist).
HEALTH_LAST_MOVE_WATCH_DAYS: float = 7.0
HEALTH_LAST_MOVE_BAD_DAYS: float = 14.0

# Days since the last card was started (entered in_progress).
HEALTH_LAST_STARTED_WATCH_DAYS: float = 14.0
HEALTH_LAST_STARTED_BAD_DAYS: float = 28.0

# Days since the last card was created.
HEALTH_LAST_CREATED_WATCH_DAYS: float = 56.0
HEALTH_LAST_CREATED_BAD_DAYS: float = 84.0

# Pull rate (started ÷ created). Lower is worse (backlog growing).
# Inverted scale: value < watch → watch, value < bad → bad.
HEALTH_PULL_RATE_WATCH: float = 0.7
HEALTH_PULL_RATE_BAD: float = 0.4

# Throughput (completed ÷ started). Healthy near 1.0 (WIP stable).
# Inverted scale: value < watch → watch, value < bad → bad.
HEALTH_THROUGHPUT_WATCH: float = 0.7
HEALTH_THROUGHPUT_BAD: float = 0.4

# Avg cycle time in days (start → done). Higher = worse.
# Spec target: every card finishes in 1–3 days. Watch triggers at > 3d,
# bad at > 5d — consistent with the fixed task-size convention.
HEALTH_CYCLE_TIME_WATCH_DAYS: float = 3.0
HEALTH_CYCLE_TIME_BAD_DAYS: float = 5.0

# Active blocker count. Any blocker (>0) is immediately "bad"; there is no
# watch state for blockers because a blocked card needs attention right away.
HEALTH_BLOCKER_WATCH: int = 0   # 0 → skip watch; any blocker goes straight to bad
HEALTH_BLOCKER_BAD: int = 0     # 1+ blockers → bad

# Cards due within DEADLINE_SOON_DAYS. A handful is a watch; many is bad.
HEALTH_DUE_SOON_WATCH: int = 0  # 1+ due-soon cards → at least watch
HEALTH_DUE_SOON_BAD: int = 5    # 5+ → bad

# 28-day start ratio (started ÷ created). Low means the backlog is growing
# faster than work is being pulled. Inverted scale: lower is worse.
HEALTH_START_RATIO_WATCH: float = 1.0   # < 1.0 → watch (not draining backlog)
HEALTH_START_RATIO_BAD: float = 0.5     # < 0.5 → bad

# 28-day finish ratio (completed ÷ started). Healthy near 1.0 (WIP stable).
# Band uses a window: outside watch range → watch; outside bad range → bad.
HEALTH_FINISH_RATIO_WATCH_LOW: float = 0.8
HEALTH_FINISH_RATIO_WATCH_HIGH: float = 1.25
HEALTH_FINISH_RATIO_BAD_LOW: float = 0.5
HEALTH_FINISH_RATIO_BAD_HIGH: float = 2.5

# Back-compat aliases (older callers referenced the average-age bands directly).
HEALTH_AGE_GOOD_DAYS: float = HEALTH_AVG_AGE_WATCH_DAYS
HEALTH_AGE_WATCH_DAYS: float = HEALTH_AVG_AGE_BAD_DAYS

# Band identifiers → display label + color.
_HEALTH_LABELS: dict[str, str] = {
    "good": "All good!",
    "watch": "Watch closely",
    "bad": "Needs attention",
}
_HEALTH_COLORS: dict[str, str] = {
    "good": "#56D364",
    "watch": "#F0A742",
    "bad": "#F85149",
}
_BAND_RANK: dict[str, int] = {"good": 0, "watch": 1, "bad": 2}

# Status label + display color for each band, keyed by a stable identifier.
HEALTH_GOOD = (_HEALTH_LABELS["good"], _HEALTH_COLORS["good"])
HEALTH_WATCH = (_HEALTH_LABELS["watch"], _HEALTH_COLORS["watch"])
HEALTH_BAD = (_HEALTH_LABELS["bad"], _HEALTH_COLORS["bad"])


def band(value: float | None, watch: float, bad: float) -> str:
    """Classify ``value`` against ascending thresholds → 'good' | 'watch' | 'bad'.

    ``None`` (no data) is treated as 'good' so missing signals never raise an
    alarm on their own.
    """
    if value is None:
        return "good"
    if value > bad:
        return "bad"
    if value > watch:
        return "watch"
    return "good"


def band_inverted(value: float | None, watch: float, bad: float) -> str:
    """Classify ``value`` on an *inverted* scale (lower is worse).

    Used for ratio metrics like pull rate and throughput where
    value < bad → 'bad', value < watch → 'watch', else 'good'.
    ``None`` (no data / denominator was zero) is treated as 'good'.
    """
    if value is None:
        return "good"
    if value < bad:
        return "bad"
    if value < watch:
        return "watch"
    return "good"


def band_color(band_id: str) -> str:
    """Display color for a band identifier returned by ``band()``."""
    return _HEALTH_COLORS.get(band_id, _HEALTH_COLORS["good"])


def health_status(avg_age: float) -> tuple[str, str]:
    """Classify project health from the average open-task age.

    Returns a ``(label, color)`` tuple using the average-age bands above.
    Kept for back-compat; prefer ``overall_health()`` for the holistic view.
    """
    b = band(avg_age, HEALTH_AVG_AGE_WATCH_DAYS, HEALTH_AVG_AGE_BAD_DAYS)
    return (_HEALTH_LABELS[b], _HEALTH_COLORS[b])


def overall_health(
    avg_age: float | None,
    max_age: float | None,
    days_since_done: float | None,
    days_since_move: float | None,
    open_count: int,
) -> tuple[str, str]:
    """Combine open-work signals into a single ``(label, color)`` health verdict.

    The result is the worst band across:
      • average open-task age
      • oldest open-task age
      • staleness of completions (days since last done) — only when open work exists
      • staleness of movement (days since last transition) — only when open work exists

    The two staleness signals are ignored when there are no open cards, since a
    finished/empty board legitimately has no recent activity.
    """
    bands = [
        band(avg_age, HEALTH_AVG_AGE_WATCH_DAYS, HEALTH_AVG_AGE_BAD_DAYS),
        band(max_age, HEALTH_MAX_AGE_WATCH_DAYS, HEALTH_MAX_AGE_BAD_DAYS),
    ]
    if open_count > 0:
        bands.append(band(days_since_done, HEALTH_LAST_DONE_WATCH_DAYS, HEALTH_LAST_DONE_BAD_DAYS))
        bands.append(band(days_since_move, HEALTH_LAST_MOVE_WATCH_DAYS, HEALTH_LAST_MOVE_BAD_DAYS))
    worst = max(bands, key=lambda b: _BAND_RANK[b])
    return (_HEALTH_LABELS[worst], _HEALTH_COLORS[worst])


# ── Deadline threshold ─────────────────────────────────────────────────────────
# Number of days ahead used by the analytics "Due Soon" stat card. A card counts
# as "due soon" when its due_date falls on or before today + this many days
# (overdue cards are included). Done/archived cards are excluded.
DEADLINE_SOON_DAYS: int = 3

# Number of days ahead that counts as "urgent". Tighter window than DEADLINE_SOON
# used to escalate a card's effective priority and to power the "Due ≤1d" board
# filter. Overdue cards are included.
DEADLINE_URGENT_DAYS: int = 1

