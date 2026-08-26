# ordo-bot

A downloadable agent that connects to an [Ordo](https://ordoscheduler.com) instance over **WebSocket**, uses **your own LLM** (you pay for tokens), and exposes a small **frontend WebSocket API** for CLI / web / any client.

- Live Ordo commands (same names as MCP / native API)
- Broadcast-driven **watches** (e.g. notify when a job completes)
- Chat queue, history caps, read-only tools by default

Ordo product docs: [Connecting to AI](https://ordoscheduler.com) → docs → *Connecting to AI*.

## Architecture

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for a diagram and component notes.

```text
  CLI / web / other clients
           │  ws://host:8765  (frontend protocol)
           ▼
      ┌─────────────┐
      │  ordo-bot   │  agent + tools + watches + queue
      └──────┬──────┘
             │  wss://…/websocket  (Ordo protocol)
             ▼
         Ordo server
             │
         your LLM
      (Groq / Ollama / …)
```

## Requirements

- Python 3.10+
- An Ordo account + API token (Settings)
- An OpenAI-compatible LLM endpoint (Groq, Ollama, OpenRouter, xAI, …)

## Quick start

```bash
git clone https://github.com/nathanielgraham/ordo-bot.git
cd ordo-bot
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .

cp config.example.toml config.toml
# Set ordo_token, llm_base_url, llm_api_key, llm_model

ordo-bot --config config.toml
```

### CLI

```bash
source .venv/bin/activate
python clients/cli.py
# optional: python clients/cli.py --verbose
```

### Web UI

```bash
# with ordo-bot already running:
xdg-open clients/web/index.html   # or open / start on macOS/Windows
# or: cd clients/web && python3 -m http.server 8080
```

Opens a simple chat page: connects to `ws://127.0.0.1:8765`, sends **hello** on first connect, supports reset and optional Ordo event lines. Details: [clients/web/README.md](clients/web/README.md).

### Config sketch

```toml
ordo_ws_url = "wss://ordoscheduler.com/websocket"
ordo_token  = "YOUR_ORDO_TOKEN"

llm_base_url = "https://api.groq.com/openai/v1"
llm_api_key  = "gsk_…"
llm_model    = "openai/gpt-oss-20b"
# or local:
# llm_base_url = "http://localhost:11434/v1"
# llm_api_key  = "ollama"
# llm_model    = "llama3.2"

frontend_host = "127.0.0.1"
frontend_port = 8765
```

Environment overrides use the `ORDO_BOT_` prefix (e.g. `ORDO_BOT_ORDO_TOKEN`).

## CLI client

- Fire-and-forget input (type while a turn is processing)
- `reset` — clear history, queue, and watches
- `quit` — exit
- Server sends `ack` → `progress` → `message` (and optional notifications from watches)
- Terminal settings are restored on exit so a disconnect does not leave echo off

## Web client

- Plain HTML/JS (`clients/web/index.html`)
- Auto-connect + first-load **hello**
- Quiet ack/progress lines; highlighted `Notification:` messages
- Reset, reconnect, Ordo-events toggle, URL in `localStorage`

## Watches (completion / broadcasts)

Ordo pushes `jobs_changed` / `clusters_changed` (and aliases). ordo-bot does **not** poll for completion.

| Tool | Use |
|------|-----|
| `watch_job` | Filter broadcasts for one job id; pass `jobstate="complete"` when you care about that state |
| `watch_cluster` | Same for a cluster id |
| `watch_event` | Generic: `event` + `filter` (`id`, `name`, `jobstate`, …) |

Example user request: *“Start concurrent-b and tell me when sleep-b completes.”*  
The agent should `start_cluster` then `watch_job(id=…, jobstate="complete")`. A later matching broadcast produces a client `message` notification.

## Frontend protocol

See **[docs/PROTOCOL.md](docs/PROTOCOL.md)**.

Client → bot: `chat`, `reset`, `ping`  
Bot → client: `status`, `ack`, `progress`, `message`, `error`, `ordo_event`, `pong`

## Project layout

```text
ordo-bot/
├── ordo_bot/
│   ├── main.py             # entry point
│   ├── config.py
│   ├── ordo_client.py      # Ordo WebSocket
│   ├── llm.py              # OpenAI-compatible chat + tools
│   ├── agent.py            # tool loop, bootstrap, history
│   ├── tools.py            # Ordo tool schemas + dispatch
│   ├── watches.py          # broadcast watches
│   ├── frontend_server.py  # client WebSocket + chat queue
│   └── protocol.py         # message models
├── clients/
│   ├── cli.py
│   └── web/
│       ├── index.html
│       └── README.md
├── docs/
│   ├── ARCHITECTURE.md
│   └── PROTOCOL.md
├── prompts/bootstrap.md    # optional playbook (standard/rich mode)
├── config.example.toml
└── pyproject.toml
```

## Related

- [Ordo](https://github.com/nathanielgraham/ordo) — scheduler
- [ordo-wsagent](https://github.com/nathanielgraham/ordo-wsagent) — optional thin NDJSON pipe (no LLM); prefer ordo-bot for a full agent
- Ordo docs: Connecting to AI, Agent protocol, API reference

## License

TBD
