# Frontend WebSocket protocol

Default URL: `ws://127.0.0.1:8765`  
Encoding: **one JSON object per WebSocket message** (not newline-framed).

## Client → bot

| `type` | Fields | Meaning |
|--------|--------|--------|
| `chat` | `content` (string) | User utterance; queued |
| `reset` | — | Clear history, drain queue, clear watches |
| `ping` | — | Liveness; expect `pong` |

Example:

```json
{"type": "chat", "content": "show the job tree"}
{"type": "reset"}
```

## Bot → client

| `type` | Fields | Meaning |
|--------|--------|--------|
| `status` | `ordo_connected`, `model` | Sent on connect (and when status changes) |
| `ack` | `content` (e.g. `received`), `queue_depth` | Chat accepted |
| `progress` | `content` (e.g. `processing`) | Worker started the turn |
| `message` | `content` | Assistant text **or** watch notification |
| `error` | `message` | Failure for that turn |
| `ordo_event` | `event`, `data` | Raw Ordo broadcast (optional UI; not agent history) |
| `pong` | — | Reply to `ping` |

## Typical sequence

```text
← status
→ chat
← ack
← progress
← message          # assistant reply
… later …
← message          # Notification: … (watch fired)
← ordo_event       # if client cares about raw broadcasts
```

## Notes

- Only one agent turn runs at a time; extra `chat`s queue.
- After `reset`, in-flight replies for the previous epoch are dropped.
- Watch notifications use `type: message` so simple clients do not need a separate event type.
- Ordo commands (via tools) may include optional `request_id`; the server echoes it on `command_reply` only. Omit it for today's protocol.
