# Kanbai 看板

> AI-powered Kanban board: per-repo CLI sidecar for humans and agents.

Kanbai lives **inside** your project as a `.kanbai/` directory. It provides:

- **A visual command center** (Flet UI) for humans to monitor and orchestrate work
- **An MCP server** for AI agents to manage cards, report progress, and read health signals
- **A CLI** for quick terminal actions and scripting

---

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [AI Chat Setup](#ai-chat-setup)
- [Usage Guide](#usage-guide)
  - [Initializing a Board](#initializing-a-board)
  - [Launching the Board](#launching-the-board)
  - [Managing Cards](#managing-cards)
  - [Managing Epics](#managing-epics)
  - [Viewing Status & Health](#viewing-status--health)
  - [Git Sync](#git-sync)
  - [Configuration](#configuration)
- [Agent Integration (MCP)](#agent-integration-mcp)
- [UI Overview](#ui-overview)
- [How It Works](#how-it-works)
- [Development](#development)

---

## Installation

```bash
# With uv (recommended)
uv tool install kanbai

# Or with pipx
pipx install kanbai

# Or with pip
pip install kanbai

# From source (development)
git clone https://github.com/particle1331/kanbai
cd kanbai
uv sync
```

After installation, the `kanbai` command is available globally.

---

## Quick Start

```bash
# 1. Go to your project
cd my-project

# 2. Initialize the board
kanbai init

# 3. Open the board (UI + MCP server)
kanbai
# → UI at http://localhost:8765
# → MCP SSE at http://localhost:3001/sse
```

That's it. Your project now has a `.kanbai/` directory with a full Kanban board.
The board state is committed alongside your code — teammates who clone the repo
get the board too.

---

## AI Chat Setup

The board UI works out of the box, but the **in-app AI chat agent** requires
an Azure OpenAI deployment. Create a `.env` file in your project root:

```bash
# .env — required for the kanbai chat agent
AZURE_OPENAI_BASE_URL=https://your-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your-api-key-here
```

Then configure your model(s) in `.kanbai/models.yml`:

```yaml
# Each entry maps a display name to an env var holding the deployment name.
models:
  - handle: mini
    env_var: AZURE_OPENAI_DEPLOYMENT_MINI
  - handle: nano
    env_var: AZURE_OPENAI_DEPLOYMENT_NANO
```

And set the deployment names in `.env`:

```bash
AZURE_OPENAI_DEPLOYMENT_MINI=gpt-4o-mini
AZURE_OPENAI_DEPLOYMENT_NANO=gpt-4o-nano
```

Optional settings:

```bash
# Context window size for the ctx% indicator (default: 128000)
KANBAI_CONTEXT_WINDOW=128000

# Azure API version (default: 2025-04-01-preview)
AZURE_OPENAI_API_VERSION=2025-04-01-preview
```

> **Note:** Only Azure OpenAI is supported as a provider currently. If the env
> vars are missing, the chat panel will show a warning but the rest of the board
> (kanban, health, graph, table, MCP) works normally.

---

## Usage Guide

### Initializing a Board

```bash
# Standard init (git tracking enabled)
kanbai init

# Without git tracking (no auto-commit/push of board changes)
kanbai init --no-git

# Migrate from an existing board_data/ directory
kanbai init --from /path/to/old/board_data
```

This creates a `.kanbai/` directory with:

- Lane subdirectories (`backlog/`, `in_progress/`, `review/`, `done/`)
- `counter.json` — monotonic card number counter
- `epics.json` — epic definitions
- `settings.json` — board configuration
- `transitions.jsonl` — event log for analytics
- `.gitignore` — excludes derived files (`.cache.db`, `ui_state.json`)

---

### Launching the Board

```bash
# Just type kanbai (opens UI + starts MCP server)
kanbai

# Or explicitly:
kanbai serve

# Custom ports:
kanbai serve --port 9000 --mcp-port 4000

# Headless MCP server only (for agent clients, no browser)
kanbai serve --mcp
```

When you run `kanbai` or `kanbai serve`:

- The **Flet UI** opens in your browser — this is your command center
- An **MCP SSE server** starts on port 3001 — agents can connect to it
- The UI **auto-refreshes** when agents or CLI commands modify the board

---

### Managing Cards

#### Create a card

```bash
kanbai add "Implement user authentication"
kanbai add "Fix login bug" --lane in_progress --priority critical
kanbai add "Write API docs" --tags "docs,api" --epic backend --due 2026-07-15
```

Options:

- `--lane`, `-l` — Lane: `backlog` (default), `in_progress`, `review`, `done`
- `--priority`, `-p` — Priority: `low`, `medium` (default), `high`, `critical`
- `--tags`, `-t` — Comma-separated tags
- `--epic`, `-e` — Epic name (must exist)
- `--desc`, `-d` — Description text
- `--due` — Due date (`YYYY-MM-DD`)

#### List cards

```bash
kanbai ls                          # All active cards
kanbai ls --lane in_progress       # Filter by lane
kanbai ls --epic backend           # Filter by epic
kanbai ls --tag bugfix             # Filter by tag
kanbai ls --archived               # Show archived cards
kanbai ls --query "auth"           # Full-text search
kanbai ls --limit 100              # Show more (default: 50)
```

#### Show card details

```bash
kanbai show 42
```

Displays: lane, priority, epic, tags, due date, links, description, and recent comments.

#### Move a card

```bash
kanbai move 42 review              # Move card #42 to review
kanbai move 42 done                # Move card #42 to done
```

#### Edit a card

```bash
kanbai edit 42 --title "New title"
kanbai edit 42 --priority high --due 2026-08-01
kanbai edit 42 --tags "backend,auth"   # Replaces all tags
kanbai edit 42 --epic backend          # Assign to epic
kanbai edit 42 --epic none             # Remove from epic
kanbai edit 42 --due none              # Clear due date
```

#### Archive / Unarchive

```bash
kanbai archive 42                  # Soft-delete (hidden from default views)
kanbai unarchive 42                # Restore
```

#### Comments

```bash
kanbai comment 42 "Fixed in commit abc123"
kanbai comment 42 "Blocked waiting on API team"
```

#### Card Links

```bash
kanbai link 42 43 blocks           # Card #42 blocks #43
kanbai link 44 42 child_of         # Card #44 is a child of #42
kanbai unlink 42 43 blocks         # Remove the link
```

Link types: `blocks`, `blocked_by`, `parent_of`, `child_of`, `duplicate`, `relates_to`

---

### Managing Epics

Epics group related cards. A card can belong to one epic or be "Unassigned."

```bash
# List epics
kanbai epic list
kanbai epic list --archived        # Include archived epics

# Create an epic
kanbai epic create "auth-system"
kanbai epic create "v2-launch" --desc "Everything for the v2 release"

# Archive an epic (archives all its cards too)
kanbai epic archive "old-feature"

# Restore an archived epic
kanbai epic unarchive "old-feature"
```

---

### Viewing Status & Health

#### Board status (quick summary)

```bash
kanbai status                      # Board-wide
kanbai status --epic backend       # Filter by epic
```

Shows card counts by lane and overall health assessment.

#### Health signals (detailed)

```bash
kanbai health                      # Board-wide
kanbai health --epic backend       # Filter by epic
```

Shows 8 health signals with good/watch/bad bands:

| Signal | What it measures |
|--------|-----------------|
| Throughput 7d | Completed ÷ started ratio |
| WIP Count | Cards in progress + review |
| WIP Median Age | How long WIP cards have been in WIP |
| Median Cycle Time | Time from in_progress to done |
| Pull Rate 7d | Started ÷ created ratio |
| Median Backlog Age | How old backlog cards are |
| Blockers | WIP cards with blocking links |
| Due in 3d | Cards with upcoming deadlines |

---

### Git Sync

If git tracking is enabled (default), you can commit and push board changes:

```bash
kanbai sync
```

This stages all `.kanbai/` changes, commits with an auto-message, and pushes.
The UI also shows a git badge when there are uncommitted board changes — click it
to sync from the UI.

---

### Configuration

```bash
# View all settings
kanbai config

# View a single setting
kanbai config git_tracking

# Change a setting
kanbai config git_tracking false
kanbai config max_agent_steps 12
```

Available settings:

| Key | Default | Description |
|-----|---------|-------------|
| `git_tracking` | `true` | Enable git commit/push for board changes |
| `use_local_tz` | `true` | Display times in local timezone |
| `reasoning_effort` | `none` | AI reasoning level (none/low/medium/high) |
| `max_completion_tokens` | `2048` | Max AI response tokens |
| `stream_response` | `true` | Stream AI responses token-by-token |
| `max_agent_steps` | `8` | Max AI tool-call turns per message |

---

## Agent Integration (MCP)

Kanbai exposes a full board management API via the [Model Context Protocol](https://modelcontextprotocol.io/).
Any MCP-compatible AI agent can manage your board.

### Setup

Add to your agent's MCP configuration:

**VS Code (`.vscode/mcp.json`):**

```json
{
  "servers": {
    "kanbai": {
      "type": "stdio",
      "command": ".venv/bin/kanbai",
      "args": ["serve", "--mcp"]
    }
  }
}
```

**Claude Desktop / Cursor:**

```json
{
  "mcpServers": {
    "kanbai": {
      "type": "stdio",
      "command": ".venv/bin/kanbai",
      "args": ["serve", "--mcp"]
    }
  }
}
```

If your board is already running with `kanbai serve`, you can also connect
to the live SSE endpoint instead:

```json
{
  "servers": {
    "kanbai": {
      "type": "sse",
      "url": "http://localhost:3001/sse"
    }
  }
}
```

The agent's working directory should be within the project (or a subdirectory).
Kanbai auto-discovers `.kanbai/` by walking up parent directories.

### Access Tiers

Control what agents can do via the `KANBAI_MCP_ACCESS` environment variable:

| Tier | Tools available | Use case |
|------|----------------|----------|
| `read` | List, get, summary, health, docs | Dashboards, untrusted agents |
| `write` *(default)* | Read + create/update/move/archive/link/epics/comments | Trusted automation |
| `yolo` | Write + permanent delete | Full trust; irreversible operations |

### Available Tools (16+)

**Read:** `list_cards`, `get_card`, `list_epics`, `get_epic`, `get_board_summary`, `get_project_root`, `project_health`, `health_model`, `list_docs`, `get_doc`

**Write:** `create_card`, `update_card`, `move_card`, `archive_card`, `unarchive_card`, `set_card_epic`, `create_epic`, `archive_epic`, `unarchive_epic`, `set_epic_description`, `add_epic_comment`, `delete_epic_comment`, `add_card_comment`, `delete_card_comment`, `link_cards`, `unlink_cards`, `add_file_ref`, `remove_file_ref`

**Yolo:** `delete_card`

### Workflow Example

A typical agent workflow:

1. Agent reads the board: `list_cards(lane="in_progress")`
2. Agent completes work and moves card: `move_card(card_id, "review")`
3. Agent adds a comment: `add_card_comment(card_id, "Implemented in commit abc123")`
4. Agent adds or updates file refs with `add_file_ref` / `remove_file_ref`
   (input is relative; Kanbai stores absolute paths)
5. Human sees the update live in the UI

---

## UI Overview

The Flet UI is your **command center** for monitoring and orchestrating work:

- **Board view** — Kanban columns with drag-and-drop, epic filtering, card creation
- **Health view** — 8 real-time health signals with good/watch/bad indicators
- **Graph view** — Card dependency graph visualization
- **Table view** — Sortable, filterable spreadsheet view of all cards
- **Chat panel** — In-app AI assistant that can manage the board
- **Search** — `Ctrl+K` / `Cmd+K` for instant card search

The UI auto-refreshes when external changes are detected (agents, CLI, direct file edits).

---

## How It Works

### Board State

All state lives in `.kanbai/` as plain files:

```
.kanbai/
├── backlog/           # Card markdown files (one per card)
│   └── <uuid>.md     # YAML frontmatter + description body
├── in_progress/
├── review/
├── done/
├── attachments/       # File attachments by card ID
├── epics.json         # Epic definitions & metadata
├── counter.json       # Monotonic card number counter
├── settings.json      # Board configuration
├── transitions.jsonl  # Lane transition events (for analytics)
└── .gitignore         # Excludes .cache.db, ui_state.json
```

### Card Format

Each card is a Markdown file with YAML frontmatter:

```markdown
---
id: a1b2c3d4-...
number: 42
title: Implement user authentication
lane: in_progress
priority: high
tags: [backend, auth]
epic: auth-system
due_date: '2026-07-15'
created_at: '2026-06-20T10:00:00+00:00'
updated_at: '2026-06-20T14:30:00+00:00'
archived: false
links:
  - {number: 43, type: blocks}
comments:
  - {id: ..., text: "Started implementation", created_at: ...}
---

Implement OAuth2 + JWT token flow for the API.
Needs to support Google and GitHub providers.
```

### SQLite Cache

A `.cache.db` file is built on startup from the Markdown files. It provides
fast queries, full-text search, and analytics. It's never committed — it's
rebuilt automatically from the source-of-truth Markdown files.

### Live Refresh

A filesystem watcher monitors `.kanbai/` for changes. When any `.md` or `.json`
file is modified (by an agent via MCP, by a CLI command, or by hand), the UI
re-syncs and refreshes within ~1 second.

---

## Multiple Projects

Each project gets its own independent board. Run multiple instances on different ports:

```bash
# Terminal 1
cd project-a && kanbai serve --port 8001 --mcp-port 3001

# Terminal 2
cd project-b && kanbai serve --port 8002 --mcp-port 3002
```

---

## Migrating from Old Kanbai

If you have an existing `board_data/` directory from the pre-0.2 standalone kanbai:

```bash
cd my-project
kanbai init --from /path/to/old/kanbai/board_data
```

This copies all cards, epics, settings, and transitions into `.kanbai/`.

---

## Development

```bash
git clone https://github.com/particle1331/kanbai
cd kanbai
uv sync

# Run from source
uv run kanbai --help

# Run tests in a temp project
cd /tmp && mkdir test && cd test
uv run --project /path/to/kanbai kanbai init
uv run --project /path/to/kanbai kanbai add "Hello world"
uv run --project /path/to/kanbai kanbai ls
```

For a guided understanding of the codebase, see [docs/dev/README.md](docs/dev/README.md).

---

## License

MIT
