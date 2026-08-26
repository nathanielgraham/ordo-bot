# ordo-bot

A downloadable agent that connects to an [Ordo](https://github.com/nathanielgraham/ordo) instance via WebSocket and uses **your own LLM** (you pay for the tokens).

The bot exposes a simple WebSocket API so you can talk to it from a CLI, a web UI, or any other client.

## Status

Early development (v0.1 scaffolding).

## Development LLM

We use **local Ollama** by default so development is free and unlimited.

```bash
# Install Ollama (Linux / macOS)
curl -fsSL https://ollama.com/install.sh | sh

# Pull a small model
ollama pull llama3.2
```

## Quick start (once the bot is further along)

```bash
# 1. Copy and edit config
cp config.example.toml config.toml
# edit config.toml with your Ordo credentials

# 2. Install the package (editable mode while developing)
pip install -e .

# 3. Run the bot
ordo-bot --config config.toml
```

## Project layout

```
ordo-bot/
── ordo_bot/
│   ── __init__.py
│   ── main.py           # entry point
│   ── config.py         # settings
│   ── protocol.py       # bot ↔ client message definitions
│   ── ordo_client.py    # (coming) WebSocket client for Ordo
│   ── llm.py            # (coming) LLM wrapper
│   ── agent.py          # (coming) agent brain
│   ── frontend_server.py# (coming) WebSocket API for clients
── clients/
│   ── cli.py            # (coming) reference CLI client
── config.example.toml
── pyproject.toml
── README.md
```

## License

TBD
