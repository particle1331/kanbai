"""Shared agent behaviour rules — imported by both the internal chat agent
(kanbai/agent/chat.py) and the MCP server (kanbai/mcp_server/server.py).

Keep this as the single source of truth for any rule that applies to ALL
agents interacting with the board, regardless of whether they are the
embedded Kanbot or an external MCP client.
"""

# ── Workflow discipline ───────────────────────────────────────────────────────
# Mandatory lifecycle steps every agent must follow for every piece of work.

WORKFLOW_DISCIPLINE = (
    "WORKFLOW DISCIPLINE (mandatory):\n"
    "1. PICK UP — when you start work on a card, immediately call get_card_context "
    "to gather the bundled card context, then move it to in_progress using "
    "move_card before doing any other work on it.\n"
    "2. TICK OFF CHECKLIST ITEMS — as you complete each acceptance-criteria or "
    "subtask item, call toggle_checklist_item to mark it done. "
    "This gives real-time progress visibility.\n"
    "3. LOG PROGRESS — before moving to review, add a comment with "
    "role='update' summarising what was done. Cover:\n"
    "   a. What you understood the task to be.\n"
    "   b. Any problems or edge-cases you encountered.\n"
    "   c. What you changed and why.\n"
    "If you need to update the card description, post a role='decision' comment "
    "first explaining what you are changing and why — the description is the spec "
    "as the card creator left it, and silent edits cause expectation drift.\n"
    "4. COMMIT — stage and commit all changed files with a descriptive "
    "conventional-commit message (feat/fix/refactor/chore/docs). "
    "Skip only for pure board housekeeping where no file outside .kanbai/ changed.\n"
    "5. HAND OFF — only after committing, move the card to review.\n"
    "Never skip or reorder these steps. "
    "backlog → in_progress → review is the expected path for every card.\n"
)

# ── Card creation defaults ────────────────────────────────────────────────────
# Applied whenever a field is omitted; the user can always override explicitly.

CARD_CREATION_DEFAULTS = (
    "CARD CREATION DEFAULTS (apply when a field is not explicitly supplied):\n"
    "  • priority — infer from urgency cues in the title/description "
    "(high if blocking/urgent, low if minor/polish, medium otherwise).\n"
    "  • epic — call list_epics and pick the most contextually appropriate one; "
    "use Unassigned only if nothing fits.\n"
    "  • tags — always include one type tag: 'bugfix' (something broken), "
    "'feature' (new capability), or 'enhancement' (improvement to existing "
    "behaviour). Derive 1–2 additional tags from the title, description, and epic.\n"
    "  • description — write a concise task summary (1–3 sentences) describing "
    "what needs to be done, sized for 1–6 days of work.\n"
    "  • checklist — after creating the card, populate its checklist with "
    "acceptance criteria and key subtasks using add_checklist_item. "
    "Draw items from the description or any explicit AC; aim for 3–8 concrete, "
    "verifiable items. Omit only if the task is genuinely trivial (< 1 hour).\n"
)

# ── Checklist discipline ──────────────────────────────────────────────────────
# Rules for maintaining the checklist as a live contract during execution.

CHECKLIST_DISCIPLINE = (
    "CHECKLIST DISCIPLINE:\n"
    "The checklist is the live contract for a card. Agents may adapt it during "
    "execution, but every change must be justified with a comment:\n"
    "  • Adding an item — if you discover a necessary subtask not in the original "
    "AC, add it with add_checklist_item and post a role='decision' comment "
    "explaining why it was added.\n"
    "  • Skipping an item — if an AC item is out of scope, already satisfied, or "
    "intentionally deferred, remove it with remove_checklist_item and post a "
    "role='decision' comment explaining the rationale.\n"
    "  • Never silently skip unchecked items when handing off to review — "
    "every unchecked item must either be ticked (done) or removed with justification.\n"
)

# ── Discovered work discipline ───────────────────────────────────────────────

DISCOVERED_WORK_DISCIPLINE = (
    "DISCOVERED WORK (mandatory when you notice adjacent out-of-scope work):\n"
    "If you discover a bug, prerequisite, or follow-up task that is outside the "
    "current card but should not be lost, create a new backlog card immediately. "
    "Use discover_work when available; otherwise create the card, link it to the "
    "current card, and add a brief note on the current card. Tag the new card with "
    "'discovered' and keep working on the original card. Do not fix the discovery inline.\n"
)

# ── Comment role discipline ───────────────────────────────────────────────────
# Use the role parameter of add_card_comment to categorise every comment.

COMMENT_ROLE_RULES = (
    "COMMENT ROLES (use the role= parameter of add_card_comment):\n"
    "  • instruction — human directive to agent; use when relaying a task or approach.\n"
    "  • update      — agent progress report; use when describing what was completed.\n"
    "  • decision    — architectural or design choice, or checklist change justification.\n"
    "  • blocker     — something preventing progress that needs human input.\n"
    "  • question    — agent asking for clarification before proceeding.\n"
    "  • note        — everything else (default if role is omitted).\n"
    "Always set an explicit role — never rely on the default 'note' unless the "
    "comment genuinely does not fit any other category.\n"
)

# ── Combined rules string ─────────────────────────────────────────────────────
# Convenience constant — use this unless you need to include only a subset.

SHARED_RULES = (
    WORKFLOW_DISCIPLINE + "\n"
    + CARD_CREATION_DEFAULTS + "\n"
    + CHECKLIST_DISCIPLINE + "\n"
    + DISCOVERED_WORK_DISCIPLINE + "\n"
    + COMMENT_ROLE_RULES
)
