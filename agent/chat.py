from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from openai import AzureOpenAI

from board import epics as _epics
from board import operations
from board.models import _UNSET
from board.settings import ModelDef, ReasoningEffort
from vault.vault import VaultLockedException, list_secret_names

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_card",
            "description": "Create a new card on the Kanban board",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "lane": {
                        "type": "string",
                        "enum": ["backlog", "in_progress", "review", "done"],
                    },
                    "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "epic": {"type": "string", "description": "Epic name to assign. Omit for Unassigned."},
                },
                "required": ["title", "lane"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_cards",
            "description": "List cards from the board, optionally filtered by lane, tags, or text",
            "parameters": {
                "type": "object",
                "properties": {
                    "lane": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "archived": {"type": "boolean"},
                    "query": {"type": "string"},
                    "epic": {"type": "string", "description": "Filter by epic. Omit for no filter. 'Unassigned' for cards with no epic."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_card",
            "description": "Get full details of a card by its ID",
            "parameters": {
                "type": "object",
                "properties": {"card_id": {"type": "string"}},
                "required": ["card_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_card_by_number",
            "description": "Look up a card by its task number (e.g. 26 for #26). Returns full card details including card_id.",
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
            "name": "update_card",
            "description": "Update a card's title, description, tags, or priority",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "priority": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                    },
                    "epic": {"type": ["string", "null"], "description": "Set epic name, or null for Unassigned. Omit to leave unchanged."},
                },
                "required": ["card_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_card",
            "description": "Move a card to a different lane",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {"type": "string"},
                    "target_lane": {
                        "type": "string",
                        "enum": ["backlog", "in_progress", "review", "done"],
                    },
                },
                "required": ["card_id", "target_lane"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_card",
            "description": "Archive (soft-delete) a card from the board",
            "parameters": {
                "type": "object",
                "properties": {"card_id": {"type": "string"}},
                "required": ["card_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unarchive_card",
            "description": "Restore an archived card back to the board",
            "parameters": {
                "type": "object",
                "properties": {"card_id": {"type": "string"}},
                "required": ["card_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_card",
            "description": "Permanently delete a card. The card MUST be archived first. Irreversible.",
            "parameters": {
                "type": "object",
                "properties": {"card_id": {"type": "string"}},
                "required": ["card_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_epic",
            "description": "Create a new epic (a grouping label for cards). Use this when the user asks to create, add, or define an epic — do NOT create a card for this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The epic name"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_epics",
            "description": "List epic names on the board",
            "parameters": {
                "type": "object",
                "properties": {
                    "include_archived": {"type": "boolean", "description": "Include archived epics. Default false."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_card_epic",
            "description": "Assign an epic to a card by its task number",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_number": {"type": "integer", "description": "Task number (e.g. 42 for Task#42)"},
                    "epic": {"type": ["string", "null"], "description": "Epic name, or null for Unassigned"},
                },
                "required": ["card_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_epic",
            "description": "Archive an epic and all its active cards",
            "parameters": {
                "type": "object",
                "properties": {"epic_name": {"type": "string"}},
                "required": ["epic_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unarchive_epic",
            "description": "Restore an archived epic and its snapshotted cards",
            "parameters": {
                "type": "object",
                "properties": {"epic_name": {"type": "string"}},
                "required": ["epic_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "link_cards",
            "description": "Create a typed link between two cards. link_type: blocks, blocked_by, parent_of, child_of, duplicate, relates_to",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_number": {"type": "integer"},
                    "to_number": {"type": "integer"},
                    "link_type": {"type": "string", "enum": ["blocks", "blocked_by", "parent_of", "child_of", "duplicate", "relates_to"]},
                },
                "required": ["from_number", "to_number", "link_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unlink_cards",
            "description": "Remove a typed link between two cards (also removes inverse link)",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_number": {"type": "integer"},
                    "to_number": {"type": "integer"},
                    "link_type": {"type": "string", "enum": ["blocks", "blocked_by", "parent_of", "child_of", "duplicate", "relates_to"]},
                },
                "required": ["from_number", "to_number", "link_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_secret_names",
            "description": "List names of secrets stored in the vault (values are never exposed)",
            "parameters": {"type": "object", "properties": {}},
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
            "name": "get_board_summary",
            "description": (
                "Return aggregated analytics data for the board: card counts by lane and priority, "
                "completion percentage, average age of OPEN cards (done/archived cards are excluded), top tags, "
                "cumulative flow data for the last 90 days, and completion heatmap counts (cards moved to done) for the current year. "
                "The result includes a '_metric_definitions' map explaining exactly what each metric means "
                "\u2014 consult it before interpreting the numbers. "
                "Use this to answer questions about board health, trends, throughput, or epic progress. "
                "Optionally filter by epic name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "epic": {
                        "type": "string",
                        "description": "Filter all data to this epic. Omit for board-wide summary.",
                    }
                },
            },
        },
    },
]

_BASE_PROMPT = (
    "You are Kanbai, an AI assistant embedded in a personal Kanban board. "
    "Lanes available: backlog, in_progress, review, done. "
    "When the user refers to a card by task number (e.g. #26 or 'card 26'), "
    "always use get_card_by_number to resolve it before taking action. "
    "Mode capabilities: ASK=read-only; AGENT=read+write (no deletion); "
    "PLAN=read-only+planning; YOLO=read+write+permanent deletion. "
    "Only Yolo mode can permanently delete cards.\n\n"
    "KANBAN PRINCIPLES (mandatory — adhere to these at all times):\n"
    "1. LIMIT WIP. Flow: backlog → in_progress → review → done. "
    "   Pull rate = started ÷ created (healthy ≥ 1.0). "
    "   Throughput = completed ÷ started (healthy ≈ 1.0). "
    "   If throughput < 1.0, stop creating new cards and unblock what's in flight.\n"
    "2. NO ESTIMATES. Every card is one unit. Cards must be completable in 3–7 days "
    "   (target 3 days). If a task is larger, split it into parent_of/child_of cards. "
    "   Do not use story points or effort estimates.\n"
    "3. HEALTH SIGNALS. Raise a concern if: oldest open card > 56 days; "
    "   no completions in > 14 days (with open work); no transitions in > 7 days.\n"
    "4. BEFORE CREATING CARDS, check board health with get_board_summary. "
    "   If throughput is low or WIP is high, prefer unblocking over adding new cards.\n"
    "5. SPLIT BIG TASKS. Use 'parent_of'/'child_of' links for hierarchical decomposition "
    "   and 'blocks'/'blocked_by' to express sequencing within a task tree.\n"
    "Full principles and flow-metric definitions are available via get_board_summary "
    "and the MCP doc tools (list_docs / get_doc).\n\n"
    "TIMEZONE RULES:\n"
    "1. All card timestamps (created_at, updated_at) are stored in UTC ISO-8601.\n"
    "2. Never treat the raw UTC date portion of a timestamp as the user's local date.\n"
    "3. When the user asks about dates ('today', 'this week', 'yesterday', etc.), "
    "   call convert_timestamps_to_local with the relevant timestamps before comparing. "
    "   Use the returned local_date values to determine which cards match.\n"
    "4. Always use convert_timestamps_to_local for date-relative queries — do not "
    "   attempt manual UTC offset arithmetic."
)


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

_ASK_PROMPT = _BASE_PROMPT + """
You are in ASK mode (read-only). You may read the board and list secret names to
answer questions, but you must NOT create, update, move, or archive cards. If the
user asks you to change the board, tell them to switch to the appropriate mode. Be concise."""

_AGENT_PROMPT = _BASE_PROMPT + """
You are in AGENT mode. You can read and modify the board: create, update, move, and
archive cards. Take direct action with the tools to fulfil the user's requests, then
briefly confirm what you changed. Be concise and action-oriented.

IMPORTANT: You do NOT have the ability to permanently delete cards in this mode.
If the user asks you to delete a card, archive it instead and explicitly tell them:
"I can't permanently delete cards in Agent mode — I archived it instead. Switch to
Yolo mode if you want permanent deletion.\""""

_PLAN_PROMPT = _BASE_PROMPT + """
You are in PLAN mode (read-only). Your job is to probe with clarifying questions and
synthesize a clear, structured plan of board changes — but you must NOT modify anything.
Read the board for context as needed. Once the plan is solid and the user is ready to
execute, explicitly recommend they switch to Agent mode to carry it out."""

_YOLO_PROMPT = _BASE_PROMPT + """
You are in YOLO mode. You have full agent capabilities AND can permanently delete cards
using delete_card. The user has explicitly authorised irreversible deletions. Take direct
action, then briefly confirm what you changed. Be concise and action-oriented."""

_PROMPTS = {"ask": _ASK_PROMPT, "agent": _AGENT_PROMPT, "plan": _PLAN_PROMPT, "yolo": _YOLO_PROMPT}

# Tools that only read state — available in every mode.
_READ_TOOL_NAMES = {"list_cards", "get_card", "get_card_by_number", "list_secret_names", "list_epics", "convert_timestamps_to_local", "get_board_summary"}

_WRITE_TOOL_NAMES = {
    "create_card", "update_card", "move_card", "archive_card",
    "unarchive_card", "set_card_epic",
    "create_epic", "archive_epic", "unarchive_epic", "link_cards", "unlink_cards",
}

_YOLO_TOOL_NAMES = {"delete_card"}

# Approximate context window (tokens). Override with KANBAI_CONTEXT_WINDOW.
_CONTEXT_WINDOW = int(os.environ.get("KANBAI_CONTEXT_WINDOW", "128000"))

# Azure OpenAI API version. Override with AZURE_OPENAI_API_VERSION.
_AZURE_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")


def _tools_for_mode(mode: str) -> list[dict]:
    """Return the tool list for a given mode."""
    if mode == "yolo":
        return _TOOLS  # all tools including delete_card
    if mode == "agent":
        allowed = _READ_TOOL_NAMES | _WRITE_TOOL_NAMES
        return [t for t in _TOOLS if t["function"]["name"] in allowed]
    return [t for t in _TOOLS if t["function"]["name"] in _READ_TOOL_NAMES]


def _resolve_deployment(model: ChatModel) -> str:
    """Resolve an Azure deployment name from the model definition."""
    dep = os.environ.get(model.env_var)
    if not dep:
        raise RuntimeError(
            f"{model.env_var} is not set. Check board_data/models.yml and your .env — "
            f"the env var for the '{model.handle}' model must point to an Azure deployment."
        )
    return dep



def _dispatch_tool(name: str, args: dict) -> str:
    if name == "create_card":
        card = operations.create_card(**args)
        return json.dumps({"id": card.id, "number": card.number, "title": card.title, "lane": card.lane, "epic": card.epic})

    if name == "list_cards":
        op_args = dict(args)
        if "epic" in op_args:
            raw = op_args.pop("epic")
            epic_val = None if raw in (None, "", "Unassigned") else raw
            return json.dumps(operations.list_cards(**op_args, epic=epic_val))
        return json.dumps(operations.list_cards(**op_args))

    if name == "get_card":
        card = operations.get_card(args["card_id"])
        if not card:
            return json.dumps({"error": "not_found"})
        return json.dumps({
            "id": card.id, "number": card.number, "title": card.title, "lane": card.lane,
            "description": card.description, "priority": card.priority,
            "tags": card.tags, "archived": card.archived, "epic": card.epic,
            "links": card.links,
        })

    if name == "get_card_by_number":
        from board.storage import sqlite_cache as _sc
        row = _sc.get_card_by_number(args["number"])
        if not row:
            return json.dumps({"error": f"Card #{args['number']} not found"})
        card = operations.get_card(row["id"])
        if not card:
            return json.dumps({"error": "not_found"})
        return json.dumps({
            "id": card.id, "number": card.number, "title": card.title, "lane": card.lane,
            "description": card.description, "priority": card.priority,
            "tags": card.tags, "archived": card.archived, "epic": card.epic,
            "links": card.links,
        })

    if name == "update_card":
        card = operations.update_card(**args)
        return json.dumps({"updated": bool(card)})

    if name == "move_card":
        card = operations.move_card(args["card_id"], args["target_lane"])
        return json.dumps({"moved": bool(card), "lane": card.lane if card else None})

    if name == "archive_card":
        card = operations.archive_card(args["card_id"])
        return json.dumps({"archived": bool(card)})

    if name == "unarchive_card":
        card = operations.unarchive_card(args["card_id"])
        return json.dumps({"unarchived": bool(card)})

    if name == "delete_card":
        ok = operations.delete_card(args["card_id"])
        return json.dumps({"deleted": ok, "error": None if ok else "not_found_or_not_archived"})

    if name == "create_epic":
        already_exists = _epics.epic_exists(args["name"])
        _epics.create_epic(args["name"])
        return json.dumps({"created": not already_exists, "already_exists": already_exists, "name": args["name"]})

    if name == "list_epics":
        include_archived = args.get("include_archived", False)
        return json.dumps(_epics.list_epics(include_archived=include_archived))

    if name == "set_card_epic":
        from board.storage import sqlite_cache as _sc
        row = _sc.get_card_by_number(args["card_number"])
        if not row:
            return json.dumps({"error": f"Card #{args['card_number']} not found"})
        card = operations.update_card(row["id"], epic=args.get("epic", _UNSET))
        return json.dumps({"updated": bool(card)})

    if name == "archive_epic":
        count = operations.archive_epic_op(args["epic_name"])
        return json.dumps({"archived_cards": count})

    if name == "unarchive_epic":
        count = operations.unarchive_epic_op(args["epic_name"])
        return json.dumps({"restored_cards": count})

    if name == "link_cards":
        err = operations.link_cards(args["from_number"], args["to_number"], args["link_type"])
        return json.dumps({"ok": err is None, "error": err})

    if name == "unlink_cards":
        err = operations.unlink_cards(args["from_number"], args["to_number"], args["link_type"])
        return json.dumps({"ok": err is None, "error": err})

    if name == "list_secret_names":
        try:
            return json.dumps(list_secret_names())
        except VaultLockedException:
            return json.dumps(
                {
                    "error": "vault_locked",
                    "message": "Vault is locked; secret names are unavailable.",
                }
            )

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

    if name == "get_board_summary":
        from board.storage import sqlite_cache as _sc
        import datetime as _dt
        epic = args.get("epic") or None
        by_lane = _sc.count_cards_by_lane(epic=epic)
        by_priority = _sc.count_cards_by_priority(epic=epic)
        total = sum(by_lane.values())
        done = by_lane.get("done", 0)
        return json.dumps({
            "by_lane": by_lane,
            "by_priority": by_priority,
            "total": total,
            "done": done,
            "completion_pct": round(done / total * 100) if total else 0,
            "avg_age_days": _sc.avg_card_age_days(epic=epic),
            "top_tags": _sc.count_cards_by_tag(10, epic=epic),
            "cfd_last_90_days": _sc.cfd_data(90, epic=epic),
            "completion_heatmap_this_year": _sc.done_cards_heatmap(_dt.date.today().year, epic=epic),
            "_metric_definitions": _sc.METRIC_DEFINITIONS,
        })

    return json.dumps({"error": "unknown_tool", "name": name})


def _make_client() -> AzureOpenAI:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not endpoint or not api_key:
        raise RuntimeError(
            "Azure OpenAI not configured. Set AZURE_OPENAI_ENDPOINT and "
            "AZURE_OPENAI_API_KEY in your .env file."
        )
    return AzureOpenAI(
        api_key=api_key,
        azure_endpoint=endpoint,
        api_version=_AZURE_API_VERSION,
    )


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
        from board.settings import load_models
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
            total_tokens = usage.total_tokens or 0
            prompt_tokens = usage.prompt_tokens or 0
            completion_tokens = usage.completion_tokens or 0
            details = getattr(usage, "completion_tokens_details", None)
            reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0

            raw_tool_calls = choice.message.tool_calls or []
            tool_calls_acc: dict[int, dict] = {}
            for i, tc in enumerate(raw_tool_calls):
                tool_calls_acc[i] = {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
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
            if name in _WRITE_TOOL_NAMES | _YOLO_TOOL_NAMES:
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
