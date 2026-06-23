# Kanbai — MCP & External Agents

> How Kanbai exposes itself to external AI agents over the **Model Context
> Protocol (MCP)**, what those agents can do, how to wire them up, the
> **access-tier model** (`KANBAI_MCP_ACCESS=read|write|yolo`), and how
> authentication works.

---

## Quick Setup

If you want the agent client to spawn Kanbai as a subprocess, use stdio
(with this repo's local `.venv` interpreter):

```json
{
  "servers": {
    "kanbai": {
      "type": "stdio",
      "command": ".venv/bin/kanbai",
      "args": ["serve", "--mcp"],
      "env": { "KANBAI_MCP_ACCESS": "write" }
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

That's it. The agent's working directory must be within a repo that has `.kanbai/`
(or any parent directory). Kanbai auto-discovers the board.

File refs are entered as paths relative to the board's `project_root`, then
resolved and stored internally as absolute paths.
The MCP/UI layer resolves them against that root automatically; agents do not
need to look up the root just to add a file ref.

---

## Running the MCP Server

### For AI agents (stdio — headless)

```bash
kanbai serve --mcp
```

The agent client spawns this as a subprocess and communicates over stdin/stdout.
No UI, no browser window.

### For humans (UI + SSE)

```bash
kanbai serve
```

This launches both the Flet UI and an MCP SSE endpoint (default port 3001).
Multiple agents can connect to the same running instance over SSE.

---

## Access Tiers (`KANBAI_MCP_ACCESS`)

Tools above the configured tier are **never registered**, so a jailbroken prompt
cannot reach them.

| Tier | Tools | Use case |
|------|-------|----------|
| `read` | List, get, summary, health, docs | Reporting, dashboards, untrusted agents |
| `write` *(default)* | Read + create/update/move/archive/link/epics/comments | Trusted automation |
| `yolo` | Write + `delete_card` (permanent) | Explicit opt-in; irreversible |

---

## Tool Catalog

### Read tier (always available)

| Tool | Description |
|------|-------------|
| `list_cards` | List/filter cards by lane, tags, archived, text query, or epic |
| `get_card` | Full details of one card by ID |
| `list_epics` | Epic names (optionally including archived) |
| `get_epic` | Epic detail: description, comments, archived status |
| `get_board_summary` | Aggregated analytics: lane counts, completion %, CFD, heatmap |
| `get_project_root` | Absolute path of the target repo root for resolving file refs |
| `project_health` | Overall health (good/watch/bad) + 8 signals |
| `health_model` | Full health model documentation |
| `list_docs` / `get_doc` | Discover and read kanbai documentation |

### Write tier

| Tool | Description |
|------|-------------|
| `create_card` | Create a card (with optional epic, due date, tags, file refs) |
| `update_card` | Update fields (title, desc, tags, priority, epic, due date, file refs) |
| `move_card` | Move a card to another lane |
| `archive_card` / `unarchive_card` | Soft-delete / restore |
| `set_card_epic` | Assign epic by card number |
| `create_epic` / `archive_epic` / `unarchive_epic` | Epic lifecycle |
| `set_epic_description` | Set/replace epic description |
| `add_epic_comment` / `delete_epic_comment` | Epic comments |
| `add_card_comment` / `delete_card_comment` | Card comments |
| `add_file_ref` / `remove_file_ref` | Attach or detach file refs (input relative, stored absolute) |
| `link_cards` / `unlink_cards` | Typed relationships (auto-inverse) |

### Yolo tier

| Tool | Description |
|------|-------------|
| `delete_card` | **Permanent** delete (must be archived first) |

---

## How a Call Flows

```
Agent → MCP server (stdio) → operations.py → Markdown store + SQLite cache
                                                      ↓
                                            .kanbai/ files updated
                                                      ↓
                                            UI file watcher triggers refresh
```

The MCP layer is a thin wrapper: each tool delegates to `operations.py` (or
`epics.py`), which writes Markdown files and updates the cache. The UI's file
watcher picks up changes automatically.

---

## Authentication

**On stdio transport: no auth needed.** Trust is established by *who can launch
the process*. The server inherits the launching environment's file permissions.

**On SSE transport** (when running `kanbai serve`): currently unauthenticated
(localhost only). For production/remote use, add bearer token auth or put behind
a reverse proxy.

---

## Security Posture

- Capability scoping via `KANBAI_MCP_ACCESS` — tools above tier aren't registered
- Permanent deletion gated behind `yolo` tier
- Authentication = local launch trust (stdio) or localhost (SSE)
- No per-call audit log yet
- No write locking between concurrent callers (last-write-wins per card)
