# Architecture

## Diagram

```text
┌──────────────────────────────────────────────────────────────────┐
│                         Your machine                             │
│                                                                  │
│   clients/cli.py  (or web UI / custom client)                    │
│         │                                                        │
│         │  WebSocket  ws://127.0.0.1:8765                        │
│         │  NDJSON: chat | reset | ack | progress | message | …   │
│         ▼                                                        │
│   ┌─────────────────────────────────────────────────────────┐    │
│   │                      ordo-bot                           │    │
│   │                                                         │    │
│   │  FrontendServer     chat queue (one turn at a time)     │    │
│   │       │             ack → progress → message            │    │
│   │       ▼                                                 │    │
│   │  Agent              history, bootstrap, tool loop       │    │
│   │       │                                                 │    │
│   │       ├── LLM (OpenAI-compatible)  Groq / Ollama / …    │    │
│   │       │                                                 │    │
│   │       ├── tools.py  →  Ordo commands (find_*, start_*,) │    │
│   │       │                                                 │    │
│   │       └── watches.py  match live broadcasts → notify    │    │
│   │                                                         │    │
│   │  OrdoClient         login_user, send_command, on_msg    │    │
│   └─────────────────────────────────────────────────────────┘    │
│                               │                                  │
└───────────────────────────────────────────────────────────────┴──────────────────────────────────┘
                                │
                                │  wss://ordoscheduler.com/websocket
                                │  NDJSON commands + broadcasts
                                ▼
                         ┌─────────────┐
                         │ Ordo server │
                         └─────────────┘
```

## Components

| Piece | Responsibility |
|-------|----------------|
| **FrontendServer** | Accept client WebSockets; queue chats; send ack/progress/replies; forward `ordo_event`; fire watch notifications |
| **Agent** | System/playbook bootstrap; LLM tool loop; read-only vs write tools; history cap; local watch tools |
| **LLM** | OpenAI-compatible `chat.completions` with tools; retries; recover tool intent from error payloads when needed |
| **tools.py** | Tool schemas (aligned with Ordo/MCP command names); slim results; dispatch to `OrdoClient` |
| **watches.py** | Register filters on broadcast stream; never poll; notify client on match |
| **OrdoClient** | Token login, request/reply correlation, unsolicited broadcasts |

## Data flow

### Chat turn

1. Client sends `{type:"chat", content:"…"}`.
2. Server replies `{type:"ack"}` immediately and enqueues work.
3. Worker sends `{type:"progress"}`, runs the agent (LLM ± tools).
4. Final `{type:"message"}` with the assistant text.
5. `reset` bumps an epoch, drains the queue, clears history and watches.

### Ordo tool call

1. Model emits a tool call (`find_cluster`, `start_cluster`, …).
2. Agent calls Ordo over the existing WebSocket (`command` field).
3. Compact JSON result is appended as a tool message; model continues.

Write tools are only offered when the user turn looks like a change request (or notify/watch wording).

### Completion notify

1. After start, model calls `watch_job` / `watch_cluster`. Default filter is **any terminal** state (`complete` / `failed` / `zombie`). Pass `jobstate` only to require one outcome.
2. Agent takes one `read_job` / `read_cluster` snapshot. If already matching, the watch completes immediately (no hang).
3. Otherwise Ordo later emits `jobs_changed` / `clusters_changed`. Only the watched **row** counts: a child job does not finish `watch_cluster`.
4. `WatchRegistry` matches filter → FrontendServer pushes `{type:"message", content:"Notification: …"}` without another user turn.
5. Watch payloads are **not** stored in LLM history (avoids TPM bloat).

## Design choices

- **Two WebSockets** — Ordo connection stays server-side; clients only talk to the bot (token stays on the bot host).
- **Shared agent history** — one conversation per bot process; queue serializes turns.
- **Broadcasts ≠ history** — UI may show `ordo_event`; the model only sees tool results + chat unless a watch notification is a normal `message`.
- **Same command names** as Ordo MCP / native API for one vocabulary across surfaces.

## Optional thin client

[ordo-wsagent](https://github.com/nathanielgraham/ordo-wsagent) is a stdin/stdout NDJSON pipe to Ordo with **no LLM**. Prefer **ordo-bot** when you want an agent. Keep wsagent for scripting or embedding a custom model loop.
