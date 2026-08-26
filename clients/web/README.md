# ordo-bot web client

Minimal plain HTML/JS UI for the frontend WebSocket (`ws://127.0.0.1:8765`).

## Run

1. Start the bot:

```bash
ordo-bot --config config.toml
```

2. Open the page (either works):

```bash
# file://
open clients/web/index.html   # macOS; or xdg-open on Linux

# or a tiny static server (needed if you ever split assets / CORS)
cd clients/web && python3 -m http.server 8080
# then http://127.0.0.1:8080/
```

## Behavior

- Connects to the URL in the header (saved in `localStorage`)
- On first connect of a page load, sends **`hello`** so the transcript is not empty
- Shows ack / progress as quiet status lines
- **Reset** clears bot history, queue, and watches
- **Ordo events** toggle shows raw `ordo_event` lines (off by default)
- Watch notifications (`Notification: …`) are highlighted
- Auto-reconnects after unexpected disconnect (3s); **Disconnect** stops that

Same protocol as `clients/cli.py` — see `docs/PROTOCOL.md`.
