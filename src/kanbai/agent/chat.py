from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI, OpenAI

from kanbai.board import epics as _epics
from kanbai.board import operations
from kanbai.board.agent_rules import SHARED_RULES
from kanbai.board.models import _UNSET
from kanbai.board.settings import ModelDef, ReasoningEffort

from kanbai.mcp_server.server import _TIER_RANK, _TOOL_REGISTRY, call_tool_sync, get_openai_tools

# Chat-only tools (no MCP equivalent).
# All board tools (create_card, get_card, add_card_comment, toggle_checklist_item, etc.)
# are sourced automatically from the MCP registry via get_openai_tools().
_CHAT_ONLY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_card_by_number",
            "description": "Look up a card by its task number (e.g. 26 for #26). Returns full card details including card_id, comments, and checklist.",
            "parameters": {
                "type": "object",
                "properties": {"number": {"type": "integer", "description": "The task number, e.g. 26"}},
                "required": ["number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_timestamps_to_local",
            "description": (
                "Convert one or more UTC ISO-8601 timestamps (from card created_at / updated_at fields) "
                "to the user's local date and time. "
                "Use this whenever you need to determine the local date a card event occurred on — "
                "for example when the user asks 'what did I complete today?', 'what was updated this week?', "
                "or any other date-relative question. "
                "Returns local_date (YYYY-MM-DD), local_datetime, timezone name, and UTC offset for each input."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "timestamps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of UTC ISO-8601 strings to convert, e.g. ['2026-06-11T01:30:00+00:00'].",
                    }
                },
                "required": ["timestamps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commit_resolution",
            "description": (
                "Stage specific files and create a git commit recording the resolution "
                "of a card. .kanbai/ is always staged automatically. "
                "MUST be called after moving a card to done whenever source files or "
                "config were changed as part of the work. "
                "Do NOT call this for pure board-housekeeping moves (e.g. reprioritising "
                "or renaming a card with no code changes)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "card_number": {
                        "type": "integer",
                        "description": "The task number of the card being resolved (e.g. 10 for #10).",
                    },
                    "message": {
                        "type": "string",
                        "description": (
                            "Conventional-commit subject line, e.g. "
                            "'feat: agent infers card defaults from context'. "
                            "A 'Resolves #N — <title>' trailer is appended automatically."
                        ),
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Repo-relative paths of every file you modified or created "
                            "as part of this card's work. "
                            ".kanbai/ is always included automatically — do NOT list it here. "
                            "If omitted, ALL uncommitted changes in the repo are staged."
                        ),
                    },
                },
                "required": ["card_number", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "Return the file tree of the project repository. "
                "Use this to understand the codebase structure before creating or "
                "updating cards — it helps derive accurate tags, epics, and descriptions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to list (default: repo root)."},
                    "depth": {"type": "integer", "description": "Max directory depth to recurse (default: 3)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file in the project repository. "
                "Use this to understand code context when creating cards about "
                "bugs, features, or improvements in specific files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Repo-relative path to the file to read."},
                    "max_lines": {"type": "integer", "description": "Maximum number of lines to return (default: 200)."},
                },
                "required": ["path"],
            },
        },
    },
]

# mode -> max MCP tier (ask/plan = read-only, agent = write, yolo = all including delete)
_MODE_TIER = {"ask": "read", "plan": "read", "agent": "write", "yolo": "yolo"}
_CHAT_ONLY_WRITE_TOOLS = {"commit_resolution"}

def _build_base_prompt() -> str:
    """Build the base system prompt. Uses live settings so health thresholds
    always reflect the current configuration."""
    from kanbai.board import settings as _s
    return (
        "You are Kanbot, an AI assistant embedded in a personal Kanban board. "
        "Your primary role is board guardianship: keep the board healthy, cards accurate, "
        "and surface important board state to the user proactively. "
        "You also have full read access to the project codebase via list_files and read_file "
        "\u2014 use this to write better card descriptions, derive accurate tags, and understand "
        "the context of bugs or features before creating or updating cards.\n"
        "Lanes: backlog, in_progress, review, done. "
        "When the user refers to a card by number (e.g. #26), always call "
        "get_card_by_number to resolve it before acting. "
        "Mode capabilities: ASK=read-only; AGENT=read+write (no deletion); "
        "PLAN=read-only+planning; YOLO=read+write+permanent deletion.\n\n"
        "BOARD GUARDIAN BEHAVIOUR:\n"
        "- After answering any user question, check if the board state warrants a "
        "proactive note (e.g. high WIP, stale backlog, bad throughput). "
        "If so, mention it briefly without being intrusive.\n"
        "- Before creating any card, call get_board_summary. "
        "If A1 throughput or A2 WIP is bad, recommend unblocking over adding new work.\n"
        "- Use list_files / read_file when creating cards about code issues to write "
        "precise descriptions and tags grounded in the actual codebase.\n\n"
        "KANBAN PRINCIPLES (mandatory):\n"
        f"P1 \u2014 Fixed task size: 1\u2013{_s.HEALTH_CYCLE_TIME_BAD_DAYS:.0f} days per card (target ~3). No story points.\n"
        "P2 \u2014 Decompose complex work into child cards; use 'blocks'/'blocked_by' for sequencing.\n"
        f"P3 \u2014 No backlog rot: complete or prune within {_s.HEALTH_BACKLOG_AGE_BAD_DAYS:.0f} days.\n"
        "P4 \u2014 Stable requirements: archive and recreate if scope changes drastically.\n"
        "P5 \u2014 Spillover is normal: cards started before the 7-day window count toward throughput.\n"
        "Health model: call health_model for all A/B/C/E signals and thresholds.\n"
        f"Key defaults \u2014 A1 Throughput bad < {_s.HEALTH_THROUGHPUT_BAD}; "
        f"A2 WIP bad > {_s.HEALTH_WIP_BAD}; "
        f"A5 Pull Rate bad < {_s.HEALTH_PULL_RATE_BAD}.\n\n"
        + SHARED_RULES
        + "\nGITOPS RULE \u2014 commit when moving a card to review:\n"
        "Whenever you move a card to review AND source files changed, call commit_resolution. "
        "Pass the 'paths' list of every file you modified (.kanbai/ is added automatically). "
        "Use a conventional-commit subject (feat/fix/refactor/chore/docs). "
        "Skip only for pure board housekeeping with no file changes outside .kanbai/.\n\n"
        "TIMEZONE RULES:\n"
        "1. All card timestamps are stored in UTC ISO-8601.\n"
        "2. For any date-relative query ('today', 'this week', etc.), call "
        "convert_timestamps_to_local first \u2014 never do manual UTC offset arithmetic."
    )
_BASE_PROMPT = _build_base_prompt()


def _local_tz_context() -> str:
    """Return a short string describing the user's local date/time for injection into the system prompt."""
    now_local = datetime.now(timezone.utc).astimezone()
    offset_str = now_local.strftime("%z")  # e.g. +0530 or -0500
    # Format as UTC±HH:MM
    offset_fmt = f"UTC{offset_str[:3]}:{offset_str[3:]}" if len(offset_str) == 5 else offset_str
    tz_name = now_local.strftime("%Z")  # e.g. CDT, CST
    local_date = now_local.strftime("%Y-%m-%d")
    local_time = now_local.strftime("%H:%M")
    return (
        f"USER TIMEZONE CONTEXT: The user's current local date is {local_date}, "
        f"local time is {local_time} ({tz_name}, {offset_fmt}). "
        f"Apply offset {offset_fmt} when converting any UTC card timestamp to local "
        f"date for comparison. Today (local) = {local_date}."
    )

_ASK_PROMPT = _build_base_prompt() + """
You are in ASK mode (read-only). You may read the board to
answer questions, but you must NOT create, update, move, or archive cards. If the
user asks you to change the board, tell them to switch to the appropriate mode. Be concise."""

_AGENT_PROMPT = _build_base_prompt() + """
You are in AGENT mode. You can read and modify the board: create, update, move, and
archive cards. Take direct action with the tools to fulfil the user's requests, then
briefly confirm what you changed. Be concise and action-oriented.

IMPORTANT: You do NOT have the ability to permanently delete cards in this mode.
If the user asks you to delete a card, archive it instead and explicitly tell them:
"I can't permanently delete cards in Agent mode — I archived it instead. Switch to
Yolo mode if you want permanent deletion.\""""

_PLAN_PROMPT = _build_base_prompt() + """
You are in PLAN mode (read-only). Your job is to probe with clarifying questions and
synthesize a clear, structured plan of board changes — but you must NOT modify anything.
Read the board for context as needed. Once the plan is solid and the user is ready to
execute, explicitly recommend they switch to Agent mode to carry it out."""

_YOLO_PROMPT = _build_base_prompt() + """
You are in YOLO mode. You have full agent capabilities AND can permanently delete cards
using delete_card. The user has explicitly authorised irreversible deletions. Take direct
action, then briefly confirm what you changed. Be concise and action-oriented."""

_PROMPTS = {"ask": _ASK_PROMPT, "agent": _AGENT_PROMPT, "plan": _PLAN_PROMPT, "yolo": _YOLO_PROMPT}

# Approximate context window (tokens). Override with KANBAI_CONTEXT_WINDOW.
_CONTEXT_WINDOW = int(os.environ.get("KANBAI_CONTEXT_WINDOW", "128000"))

# Azure OpenAI API version. Override with AZURE_OPENAI_API_VERSION.
_AZURE_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")


def _tools_for_mode(mode: str) -> list[dict]:
    """Return the OpenAI tool list for the given mode.

    Board tools come from the MCP registry — automatically includes every
    @tool-decorated function in server.py, filtered by the mode's tier:
      ask / plan -> read-only
      agent      -> read + write
      yolo       -> read + write + delete (full access)
    Chat-only tools (commit_resolution, list_files, etc.) are appended.
    """
    return get_openai_tools(_MODE_TIER.get(mode, "write")) + _CHAT_ONLY_TOOLS


def _resolve_deployment(model: ModelDef) -> str:
    """Resolve an Azure deployment name from the model definition."""
    dep = os.environ.get(model.env_var)
    if not dep:
        raise RuntimeError(
            f"{model.env_var} is not set. Check board_data/models.yml and your .env — "
            f"the env var for the '{model.handle}' model must point to an Azure deployment."
        )
    return dep



def _dispatch_tool(name: str, args: dict) -> str:
    # Board tools: single source of truth in the MCP registry.
    if name in _TOOL_REGISTRY:
        return call_tool_sync(name, args)

    # Chat-only tools ────────────────────────────────────────────────────────
    if name == "get_card_by_number":
        from kanbai.board.storage import sqlite_cache as _sc
        row = _sc.get_card_by_number(args["number"])
        if not row:
            return json.dumps({"error": f"Card #{args['number']} not found"})
        card = operations.get_card(row["id"])
        if not card:
            return json.dumps({"error": "not_found"})
        checklist = card.checklist or []
        done = sum(1 for it in checklist if it.get("done"))
        return json.dumps({
            "id": card.id, "number": card.number, "title": card.title, "lane": card.lane,
            "description": card.description, "priority": card.priority,
            "tags": card.tags, "archived": card.archived, "epic": card.epic,
            "links": card.links, "comments": card.comments, "file_refs": card.file_refs,
            "checklist": [{"index": i, "text": it["text"], "done": it.get("done", False)} for i, it in enumerate(checklist)],
            "checklist_completion": f"{done}/{len(checklist)}",
        })

    if name == "convert_timestamps_to_local":
        now_local = datetime.now(timezone.utc).astimezone()
        local_tz = now_local.tzinfo
        results = []
        for ts_str in args.get("timestamps", []):
            try:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                local = ts.astimezone(local_tz)
                results.append({
                    "utc": ts_str,
                    "local_date": local.strftime("%Y-%m-%d"),
                    "local_datetime": local.strftime("%Y-%m-%d %H:%M"),
                    "timezone": local.strftime("%Z"),
                    "utc_offset": local.strftime("%z"),
                })
            except ValueError as exc:
                results.append({"utc": ts_str, "error": str(exc)})
        return json.dumps(results)

    if name == "commit_resolution":
        from kanbai.board.storage import git_ops, sqlite_cache as _sc
        row = _sc.get_card_by_number(args["card_number"])
        card_title = row["title"] if row else f"card #{args['card_number']}"
        ok, info = git_ops.commit_resolution(
            card_number=args["card_number"],
            card_title=card_title,
            message=args["message"],
            paths=args.get("paths"),
        )
        return json.dumps({"ok": ok, "info": info})

    if name == "list_files":
        from kanbai.paths import PROJECT_ROOT
        from kanbai.agent.gitignore import GitignoreFilter
        import os as _os
        gi_filter = GitignoreFilter(PROJECT_ROOT)
        root = PROJECT_ROOT
        rel = args.get("path", "")
        if rel:
            target = (PROJECT_ROOT / rel).resolve()
            if gi_filter.is_ignored(target):
                return json.dumps({"error": "access_denied", "path": rel,
                                   "reason": "path is gitignored or outside the repo"})
            root = target
        max_depth = int(args.get("depth", 3))
        lines: list[str] = []
        for dirpath, dirnames, filenames in _os.walk(root):
            cur_dir = Path(dirpath)
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith(".")
                and d not in ("__pycache__", "node_modules", ".venv", "venv")
                and not gi_filter.is_ignored(cur_dir / d)
            )
            depth = len(cur_dir.relative_to(root).parts)
            if depth >= max_depth:
                dirnames.clear()
            indent = "  " * depth
            lines.append(f"{indent}{cur_dir.name}/")
            for fname in sorted(filenames):
                if not fname.startswith(".") and not gi_filter.is_ignored(cur_dir / fname):
                    lines.append(f"{indent}  {fname}")
        return json.dumps({"tree": "\n".join(lines)})

    if name == "read_file":
        from kanbai.paths import PROJECT_ROOT
        from kanbai.agent.gitignore import GitignoreFilter
        gi_filter = GitignoreFilter(PROJECT_ROOT)
        path = (PROJECT_ROOT / args["path"]).resolve()
        if gi_filter.is_ignored(path):
            return json.dumps({"error": "access_denied", "path": args["path"],
                               "reason": "file is gitignored or outside the repo"})
        if not path.exists():
            return json.dumps({"error": "file_not_found", "path": args["path"]})
        max_lines = int(args.get("max_lines", 200))
        try:
            content_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            truncated = len(content_lines) > max_lines
            return json.dumps({
                "path": args["path"],
                "lines": content_lines[:max_lines],
                "truncated": truncated,
                "total_lines": len(content_lines),
            })
        except Exception as exc:
            return json.dumps({"error": str(exc), "path": args["path"]})

    return json.dumps({"error": "unknown_tool", "name": name})

_client_cache: OpenAI | None = None
_client_cache_key: tuple[str, str, str] | None = None


def _make_client() -> OpenAI:
    global _client_cache, _client_cache_key
    endpoint = os.environ.get("AZURE_OPENAI_BASE_URL") or ""
    api_key = os.environ.get("AZURE_OPENAI_API_KEY") or ""
    if not endpoint or not api_key:
        raise RuntimeError(
            "Azure OpenAI not configured. Set AZURE_OPENAI_BASE_URL and "
            "AZURE_OPENAI_API_KEY in your .env file."
        )
    cache_key = (endpoint, api_key, _AZURE_API_VERSION)
    if _client_cache is None or _client_cache_key != cache_key:
        _client_cache = OpenAI(api_key=api_key, base_url=endpoint)
        _client_cache_key = cache_key
    return _client_cache


def warm_up() -> None:
    """Pre-create the Azure OpenAI client so the first message has no setup delay.
    Safe to call from a background thread at startup."""
    try:
        _make_client()
    except Exception:
        pass  # misconfigured env — will surface properly on first real send


def chat(
    messages: list[dict],
    mode: str = "agent",
    model: ModelDef | None = None,
    on_token=None,
    on_tool_call=None,
    reasoning_effort: ReasoningEffort | None = None,
    max_completion_tokens: int = 2048,
    stream: bool = True,
    max_steps: int = 8,
) -> tuple[str, list[dict], dict]:
    """
    Send a conversation and return (assistant_reply, updated_messages, meta).
    messages: list of {role, content} dicts (user + assistant turns only, no system).
    mode: "ask" (read-only), "agent" (read+write), "plan" (read-only, planning),
          or "yolo" (agent + permanent delete).
    model: ModelDef loaded from board_data/models.yml — resolved to an Azure deployment.
           Defaults to the first model in models.yml (nano) if None.
    on_token: optional callable(text: str) invoked for each streamed text token.
    on_tool_call: optional callable(name: str, args: dict) invoked before each tool executes.
    reasoning_effort: ReasoningEffort enum — None or NONE skips the parameter.
    max_completion_tokens: cap on response tokens (includes reasoning tokens for o-series).
    max_steps: maximum number of model turns (each turn may issue tool calls) before
               control is forcibly returned to the user. On the final allowed turn the
               model is asked to respond in text only (no further tools). Must be >= 1.
    stream: when True, tokens are delivered via on_token as they arrive; when False,
            the full response is returned at once with accurate reasoning_tokens.
    meta: {"total_tokens": int, "context_window": int, "percent": float, "deployment": str}.
    """
    client = _make_client()
    if model is None:
        from kanbai.board.settings import load_models
        model = load_models()[0]
    deployment = _resolve_deployment(model)
    system_prompt = _PROMPTS.get(mode, _AGENT_PROMPT) + "\n\n" + _local_tz_context()
    tools = _tools_for_mode(mode)

    history: list[dict] = [{"role": "system", "content": system_prompt}] + messages
    total_tokens = 0
    board_changed = False

    step = 0
    final_step = False  # True on the last allowed turn: tools are disabled.
    while True:
        step += 1
        # On the final allowed turn, disable tools so the model must reply in text
        # and control returns to the user.
        final_step = step >= max(1, max_steps)
        if final_step and history and history[-1].get("role") == "tool":
            # Out of tool budget but mid-task: tell the model to summarise progress
            # so far and what remains, rather than starting new actions.
            history.append({
                "role": "system",
                "content": (
                    "You have reached the maximum number of tool-calling steps for this "
                    "turn. Do not request any more tools. Summarise concisely what you "
                    "accomplished and clearly state anything still incomplete so the user "
                    "can continue."
                ),
            })
        create_kwargs: dict = dict(
            model=deployment,
            messages=history,
            tools=tools,
            tool_choice="none" if final_step else "auto",
            max_completion_tokens=max_completion_tokens,
        )
        if reasoning_effort and reasoning_effort is not ReasoningEffort.NONE:
            create_kwargs["reasoning_effort"] = reasoning_effort.value

        if not stream:
            # ── Non-streaming path: full response + accurate reasoning_tokens ──
            response = client.chat.completions.create(**create_kwargs, stream=False)
            choice = response.choices[0]
            finish_reason = choice.finish_reason
            full_content = choice.message.content or ""
            usage = response.usage
            total_tokens = usage.total_tokens or 0 if usage else 0
            prompt_tokens = usage.prompt_tokens or 0 if usage else 0
            completion_tokens = usage.completion_tokens or 0 if usage else 0
            details = getattr(usage, "completion_tokens_details", None)
            reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0

            raw_tool_calls = choice.message.tool_calls or []
            tool_calls_acc: dict[int, dict] = {}
            for i, tc in enumerate(raw_tool_calls):
                fn = getattr(tc, "function", None)
                tool_calls_acc[i] = {
                    "id": tc.id,
                    "name": fn.name if fn else "",
                    "arguments": fn.arguments if fn else "{}",
                }
        else:
            # ── Streaming path: on_token called per chunk ──
            create_kwargs["stream"] = True
            create_kwargs["stream_options"] = {"include_usage": True}
            streamed = client.chat.completions.create(**create_kwargs)

            full_content = ""
            tool_calls_acc = {}
            finish_reason = None
            prompt_tokens = 0
            completion_tokens = 0
            reasoning_tokens = 0
            for chunk in streamed:
                if chunk.usage:
                    total_tokens = chunk.usage.total_tokens
                    prompt_tokens = chunk.usage.prompt_tokens or 0
                    completion_tokens = chunk.usage.completion_tokens or 0
                    details = getattr(chunk.usage, "completion_tokens_details", None)
                    reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0
                if not chunk.choices:
                    continue
                ch = chunk.choices[0]
                if ch.finish_reason:
                    finish_reason = ch.finish_reason
                delta = ch.delta
                if delta.content:
                    full_content += delta.content
                    if on_token:
                        try:
                            on_token(delta.content)
                        except Exception:
                            pass
                if delta.tool_calls:
                    for tc_delta in delta.tool_calls:
                        idx = tc_delta.index
                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {"id": "", "name": "", "arguments": ""}
                        if tc_delta.id:
                            tool_calls_acc[idx]["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                tool_calls_acc[idx]["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments

        if not tool_calls_acc:
            # Pure text reply — done
            reply = full_content
            updated = messages + [{"role": "assistant", "content": reply}]
            meta = {
                "total_tokens": total_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "reasoning_tokens": reasoning_tokens,
                "context_window": _CONTEXT_WINDOW,
                "percent": round(total_tokens / _CONTEXT_WINDOW * 100, 1),
                "board_changed": board_changed,
                "finish_reason": finish_reason,
                "deployment": deployment,
                "steps": step,
                "hit_step_limit": final_step,
            }
            return reply, updated, meta

        # Build serialisable assistant message with tool calls
        tool_calls_list = [
            {
                "id": tool_calls_acc[i]["id"],
                "type": "function",
                "function": {
                    "name": tool_calls_acc[i]["name"],
                    "arguments": tool_calls_acc[i]["arguments"],
                },
            }
            for i in sorted(tool_calls_acc)
        ]
        history.append({
            "role": "assistant",
            "content": full_content or None,
            "tool_calls": tool_calls_list,
        })

        for tc in tool_calls_list:
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"])
            reg = _TOOL_REGISTRY.get(name)
            if (reg and _TIER_RANK[reg["tier"]] >= _TIER_RANK["write"]) or name in _CHAT_ONLY_WRITE_TOOLS:
                board_changed = True
            if on_tool_call:
                try:
                    on_tool_call(name, args)
                except Exception:
                    pass
            try:
                result = _dispatch_tool(name, args)
            except (operations.EpicArchivedError, ValueError) as ex:
                result = json.dumps({"error": str(ex)})
            history.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })
