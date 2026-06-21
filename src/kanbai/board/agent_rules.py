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
    "1. PICK UP — when you start work on a card, immediately move it to "
    "in_progress using move_card before doing any other work on it.\n"
    "2. UPDATE DESCRIPTION — before moving to review, append a timestamped "
    "section to the card description (descriptions render Markdown) covering:\n"
    "   a. What you understood the issue/task to be.\n"
    "   b. Any problems or edge-cases you encountered.\n"
    "   c. What you changed and why.\n"
    "   Format: ---\\n**Fixed [YYYY-MM-DD HH:MM]**\\n- **Understood:** ...\\n"
    "   - **Encountered:** ...\\n- **Fixed:** ...\n"
    "3. COMMIT — stage and commit all changed files with a descriptive "
    "conventional-commit message (feat/fix/refactor/chore/docs). "
    "Skip only for pure board housekeeping where no file outside .kanbai/ changed.\n"
    "4. HAND OFF — only after committing, move the card to review.\n"
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
)

# ── Combined rules string ─────────────────────────────────────────────────────
# Convenience constant — use this unless you need to include only a subset.

SHARED_RULES = WORKFLOW_DISCIPLINE + "\n" + CARD_CREATION_DEFAULTS
