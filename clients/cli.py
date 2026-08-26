#!/usr/bin/env python3
"""
Simple reference CLI client for ordo-bot.

Connects to the bot's frontend WebSocket and lets you chat.

Usage (bot must already be running):

    python clients/cli.py
    python clients/cli.py --url ws://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("Need websockets: pip install websockets", file=sys.stderr)
    sys.exit(1)


async def run(url: str) -> None:
    print(f"Connecting to {url} ...")
    async with websockets.connect(url) as ws:
        # First message should be a status
        raw = await ws.recv()
        try:
            status = json.loads(raw)
            print(
                f"Connected. ordo_connected={status.get('ordo_connected')} "
                f"model={status.get('model')}"
            )
        except Exception:
            print(f"Connected. (first message: {raw})")

        print("Type a message and press Enter. 'quit' to exit.\n")

        loop = asyncio.get_running_loop()

        while True:
            try:
                user_text = await loop.run_in_executor(None, lambda: input("you> "))
            except (EOFError, KeyboardInterrupt):
                print()
                break

            user_text = user_text.strip()
            if not user_text:
                continue
            if user_text.lower() in {"quit", "exit"}:
                break

            # Send chat message
            await ws.send(json.dumps({"type": "chat", "content": user_text}))

            # Wait for reply (message or error)
            while True:
                raw = await ws.recv()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    print(f"bot> (non-JSON) {raw}")
                    break

                msg_type = data.get("type")
                if msg_type == "message":
                    print(f"bot> {data.get('content', '')}")
                    print()
                    break
                if msg_type == "error":
                    print(f"bot> ERROR: {data.get('message', '')}")
                    print()
                    break
                if msg_type == "ordo_event":
                    # Just show it and keep waiting for the chat reply
                    print(f"[ordo event] {data.get('event')}: {data.get('data')}")
                    continue
                if msg_type == "status":
                    continue
                # Unknown – print and keep going
                print(f"[recv] {data}")


def main() -> None:
    p = argparse.ArgumentParser(description="ordo-bot CLI client")
    p.add_argument(
        "--url",
        default="ws://127.0.0.1:8765",
        help="Frontend WebSocket URL (default: ws://127.0.0.1:8765)",
    )
    args = p.parse_args()
    try:
        asyncio.run(run(args.url))
    except ConnectionRefusedError:
        print(
            f"Could not connect to {args.url}. Is ordo-bot running?",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
