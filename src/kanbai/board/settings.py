from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

import yaml

from kanbai.paths import MODELS_CONFIG_PATH, SETTINGS_PATH


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
    """Load model definitions from .kanbai/models.yml.

    Returns a single fallback model if the file is missing or has no entries,
    so the UI can always render (chat just won't work without valid config).
    """
    _FALLBACK = [ModelDef(handle="default", env_var="OPENAI_MODEL")]
    if not MODELS_CONFIG_PATH.exists():
        return _FALLBACK
    try:
        raw = yaml.safe_load(MODELS_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except OSError:
        return _FALLBACK
    entries = raw.get("models") or []
    defs = []
    for e in entries:
        if "handle" not in e or "env_var" not in e:
            continue
        handle = str(e["handle"])
        if len(handle) >= 16:
            continue
        defs.append(ModelDef(handle=handle, env_var=str(e["env_var"])))
    return defs if defs else _FALLBACK


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
    # Absolute filesystem path of the target project repository root.
    # Persisted in settings.json so tools/agents can resolve repo-relative refs.
    "project_root": "",
    # Display stored UTC timestamps in the local timezone (True) or as UTC (False).
    # App-wide: affects the analytics board and kanban card dates.
    "use_local_tz": True,
    # AI chat settings
    "reasoning_effort": ReasoningEffort.NONE.value,  # ReasoningEffort enum value
    "max_completion_tokens": 2048,
    "stream_response": True,
    # Default chat mode for each new app session. The live mode is session-only
    # and intentionally not persisted, so risky modes like YOLO never stick
    # across restarts.
    "chat_mode": "agent",  # "ask" | "agent" | "plan" | "yolo"
    # Max model turns (each may issue tool calls) before control returns to the user.
    "max_agent_steps": 8,
    # Cumulative token usage per model handle.
    # Shape: {"<handle>": {"prompt_tokens": int, "completion_tokens": int}}
    "token_usage": {},
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


def project_root() -> str:
    """Return the persisted absolute project root path, or an empty string."""
    return str(_load().get("project_root", "") or "")


def set_project_root(value: str) -> None:
    _load()["project_root"] = str(value or "")
    _save()


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


def chat_mode() -> str:
    """Default chat mode for a fresh app session.

    This intentionally ignores any persisted ``settings.json`` value so the chat
    panel always resets to the safe default on restart.
    """
    mode = str(_DEFAULTS.get("chat_mode", "agent")).lower()
    return mode if mode in ("ask", "agent", "plan", "yolo") else "agent"


def set_chat_mode(value: str) -> None:
    """Set the in-session chat mode.

    Kept as a compatibility shim for callers that still invoke it, but it no
    longer persists anything to disk. Chat mode is session-only.
    """
    return None


# ── Token usage tracking ───────────────────────────────────────────────────────

def get_token_usage(model_handle: str) -> dict[str, int]:
    """Return cumulative token counts for *model_handle*.

    Returns a dict with ``prompt_tokens`` and ``completion_tokens`` (both int,
    defaulting to 0 if not yet recorded).
    """
    usage: dict = _load().get("token_usage") or {}
    entry = usage.get(model_handle) or {}
    return {
        "prompt_tokens": int(entry.get("prompt_tokens", 0)),
        "completion_tokens": int(entry.get("completion_tokens", 0)),
    }


def get_all_token_usage() -> dict[str, dict[str, int]]:
    """Return cumulative token counts for all tracked models.

    Returns a dict keyed by model handle, each value having ``prompt_tokens``
    and ``completion_tokens``.
    """
    raw: dict = _load().get("token_usage") or {}
    return {
        handle: {
            "prompt_tokens": int(entry.get("prompt_tokens", 0)),
            "completion_tokens": int(entry.get("completion_tokens", 0)),
        }
        for handle, entry in raw.items()
    }


def add_token_usage(model_handle: str, prompt_tokens: int, completion_tokens: int) -> None:
    """Accumulate *prompt_tokens* and *completion_tokens* for *model_handle*.

    Both values are clamped to >= 0 before being added so a bad API response
    can never corrupt the running total.
    """
    cfg = _load()
    usage: dict = cfg.setdefault("token_usage", {})
    entry: dict = usage.setdefault(model_handle, {"prompt_tokens": 0, "completion_tokens": 0})
    entry["prompt_tokens"] = int(entry.get("prompt_tokens", 0)) + max(0, int(prompt_tokens))
    entry["completion_tokens"] = int(entry.get("completion_tokens", 0)) + max(0, int(completion_tokens))
    _save()


# Default output-token budget (in raw token count) when KANBAI_TOKEN_SPEND_LIMIT_<HANDLE>
# is not set.  1 million tokens.
_TOKEN_LIMIT_DEFAULT: int = 1_000_000


def token_spend_limit(model_handle: str) -> int:
    """Per-session output (completion) token budget for *model_handle*, in tokens.

    Configured via ``KANBAI_TOKEN_SPEND_LIMIT_<HANDLE>`` (upper-cased handle), e.g.
    ``KANBAI_TOKEN_SPEND_LIMIT_NANO=10`` for a 10 million input-token budget on
    the ``nano`` model.  The value is interpreted as **millions** (float accepted),
    so 0.5 means 500 000 tokens.  Falls back to ``_TOKEN_LIMIT_DEFAULT`` (1 M) when
    the variable is absent or invalid.
    The output (completion) budget is automatically 1/10× this value.
    """
    env_var = f"KANBAI_TOKEN_SPEND_LIMIT_{model_handle.upper()}"
    raw = os.environ.get(env_var, "")
    try:
        millions = float(raw)
        return max(1, int(millions * 1_000_000))
    except (ValueError, TypeError):
        return _TOKEN_LIMIT_DEFAULT


def reset_token_usage(model_handle: str | None = None) -> None:
    """Reset cumulative token counts.

    If *model_handle* is given, only that model's counters are cleared.
    If ``None``, all models are cleared.
    """
    cfg = _load()
    if model_handle is None:
        cfg["token_usage"] = {}
    else:
        usage: dict = cfg.get("token_usage") or {}
        usage.pop(model_handle, None)
        cfg["token_usage"] = usage
    _save()


# ── Default tags ─────────────────────────────────────────────────────────────────
# Shown as quick-pick chips in the card dialog. These map to the spec's
# "type" dimension: bugfix / feature / enhancement.
DEFAULT_TAGS: list[str] = ["bugfix", "feature", "enhancement"]

# ── Project health thresholds (A1–A8 stat cards) ─────────────────────────────
# Single source of truth consumed by board/health.py and ui/health_page.py.
#
# Each signal maps a value to one of three bands via band() / band_inverted():
#   good  – within healthy limits (green)
#   watch – approaching a threshold; worth monitoring (amber)
#   bad   – threshold exceeded; needs attention (red)

# A1: Throughput 7d (completed ÷ started) — lower is worse
HEALTH_THROUGHPUT_WATCH: float = 0.6
HEALTH_THROUGHPUT_BAD: float = 0.4

# A2: WIP Count (in_progress + review) — higher is worse
HEALTH_WIP_WATCH: int = 6
HEALTH_WIP_BAD: int = 8

# A3: WIP Median Age (days) — higher is worse
HEALTH_WIP_AGE_WATCH_DAYS: float = 5.0
HEALTH_WIP_AGE_BAD_DAYS: float = 8.0

# A4: Median Cycle Time (days, completed in past 7d) — higher is worse (>= triggers)
HEALTH_CYCLE_TIME_WATCH_DAYS: float = 5.0
HEALTH_CYCLE_TIME_BAD_DAYS: float = 6.0

# A5: Pull Rate 7d (started ÷ created) — lower is worse
HEALTH_PULL_RATE_WATCH: float = 0.35
HEALTH_PULL_RATE_BAD: float = 0.2

# A6: Median Backlog Age (days) — higher is worse
HEALTH_BACKLOG_AGE_WATCH_DAYS: float = 14.0
HEALTH_BACKLOG_AGE_BAD_DAYS: float = 21.0

# A7: Blockers (WIP cards with ≥1 blocks link) — higher is worse
HEALTH_BLOCKER_WATCH: int = 2
HEALTH_BLOCKER_BAD: int = 4

# A8: Due in 3d — two-tier:
#   watch: due_3d >= HEALTH_DUE_WATCH
#   bad:   due_3d >= HEALTH_DUE_BAD  OR  due_1d >= 1
HEALTH_DUE_WATCH: int = 3
HEALTH_DUE_BAD: int = 5

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


# ── Deadline threshold ─────────────────────────────────────────────────────────
# Number of days ahead used by the analytics "Due Soon" stat card. A card counts
# as "due soon" when its due_date falls on or before today + this many days
# (overdue cards are included). Done/archived cards are excluded.
DEADLINE_SOON_DAYS: int = 3

# Number of days ahead that counts as "urgent". Tighter window than DEADLINE_SOON
# used to escalate a card's effective priority and to power the "Due ≤1d" board
# filter. Overdue cards are included.
DEADLINE_URGENT_DAYS: int = 1

# Stale WIP: cards in WIP longer than this appear in the stale WIP list.
STALE_WIP_DAYS: int = 5

# Stale WIP badge: watch when any card > this, bad when any card > STALE_WIP_BAD_DAYS.
STALE_WIP_WATCH_DAYS: int = 7
STALE_WIP_BAD_DAYS: int = 10

# Aged backlog: cards with age > this multiplier × median backlog age are outliers.
AGED_BACKLOG_MULTIPLIER: float = 1.5

