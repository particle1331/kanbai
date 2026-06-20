# Kanbai 看板

> AI-powered Kanban board — per-repo CLI sidecar for humans and agents.

Kanbai lives **inside** your project as a `.kanbai/` directory. It provides:

- **A visual command center** (Flet UI) for humans to monitor and orchestrate work
- **An MCP server** for AI agents to manage cards, report progress, and read health signals
- **A CLI** for quick terminal actions and scripting

```
┌─────────────────────────────────────────┐
│  Human (orchestrator)                   │
│  └── kanbai serve → UI + MCP (SSE)     │
├─────────────────────────────────────────┤
│  AI Agents (workers)                    │
│  └── kanbai serve --mcp → stdio MCP    │
├─────────────────────────────────────────┤
│  .kanbai/  ← shared board state in repo │
└─────────────────────────────────────────┘
```

---

## Quick Start

```bash
# Install
pipx install kanbai
# or: pip install kanbai

# Initialize in your project
cd my-project
kanbai init

# Launch the board
kanbai serve
# → Opens UI at http://localhost:8765
# → MCP SSE at http://localhost:3001/sse
```

---

## CLI Commands

```bash
# Board management
kanbai add "Fix login bug" --lane in_progress --priority critical --tags "bugfix"
kanbai ls --lane in_progress
kanbai show 42
kanbai move 42 review
kanbai edit 42 --priority high --due 2026-07-01
kanbai archive 42
kanbai comment 42 "Fixed in commit abc123"
kanbai link 42 43 blocks

# Epics
kanbai epic create "auth-system" --desc "Authentication module"
kanbai epic list
kanbai epic archive "old-feature"

# Status & Health
kanbai status
kanbai health

# Git sync
kanbai sync

# Settings
kanbai config
kanbai config git_tracking false

# Server
kanbai serve              # UI + MCP SSE (for humans)
kanbai serve --mcp        # Headless stdio MCP (for agents)
```

---

## Agent Integration (MCP)

Add this to your AI agent's MCP configuration:

**Claude Desktop / Cursor / VS Code (`.vscode/mcp.json`):**

```json
{
  "servers": {
    "kanbai": {
      "type": "stdio",
      "command": "kanbai",
      "args": ["serve", "--mcp"],
      "env": { "KANBAI_MCP_ACCESS": "write" }
    }
  }
}
```

The agent will have access to 15+ tools for managing the board: creating cards,
moving them between lanes, managing epics, reading health signals, and more.

### Access Tiers

| `KANBAI_MCP_ACCESS` | Tools | Use case |
|---------------------|-------|----------|
| `read` | List, get, summary, health | Dashboards, untrusted agents |
| `write` (default) | Read + create/update/move/archive/link/epics | Trusted automation |
| `yolo` | Write + permanent delete | Full trust; irreversible ops |

---

## How It Works

All board state lives in `.kanbai/` inside your project repo:

```
my-project/
├── .kanbai/
│   ├── backlog/          # Card markdown files
│   ├── in_progress/
│   ├── review/
│   ├── done/
│   ├── attachments/
│   ├── epics.json
│   ├── counter.json
│   ├── settings.json
│   ├── transitions.jsonl  # Event log for analytics
│   └── .gitignore         # Excludes .cache.db, ui_state.json
└── ... your project files
```

Cards are stored as YAML-frontmatter Markdown files. The SQLite cache (`.cache.db`)
is rebuilt on startup from the Markdown files — it's never committed.

### Live Refresh

The UI watches `.kanbai/` for filesystem changes. When an agent moves a card via
MCP or you edit a card from the CLI, the UI updates automatically — no manual
refresh needed.

---

## Initialization Options

```bash
kanbai init                    # Default: git tracking enabled
kanbai init --no-git           # Disable git commit/push integration
kanbai init --from ./old-board # Migrate from existing board_data/ directory
```

---

## Development

```bash
git clone https://github.com/particle1331/kanbai
cd kanbai
uv sync
uv run kanbai --help
```

---

## License

MIT
