#!/usr/bin/env python3
"""
Simple reference CLI client for ordo-bot.

Connects to the bot's frontend WebSocket and lets you chat.

Usage (bot must already be running):

    python clients/cli.py
    python clients/cli.py --url ws://127.0.0.1:8765
    python clients/cli.py --verbose          # one-line Ordo event summaries
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List

try:
    import websockets
except ImportError:
    print("Need websockets: pip install websockets", file=sys.stderr)
    sys.exit(1)


def summarize_ordo_event(data: Dict[str, Any]) -> str:
    """
    Turn a full ordo_event payload into one short line.

    Examples:
      [ordo] jobs_changed: panthero-stats → complete
      [ordo] clusters_changed: Monitoring → running
      [ordo] servers_changed: panthero
    """
    event = data.get("event") or "?"
    payload = data.get("data") or {}

    # Prefer the nested broadcast payload if present
    updates: List[dict] = payload.get("updates") or []
    names: List[str] = []
    states: List[str] = []

    for u in updates[:3]:  # at most a few names
        name = u.get("name")
        state = u.get("jobstate")
        if name:
            names.append(str(name))
        if state:
            states.append(str(state))

    if names and states and len(names) == 1 and len(states) == 1:
        return f"[ordo] {event}: {names[0]} → {states[0]}"
    if names and states:
        parts = [f"{n}→{s}" for n, s in zip(names, states)]
        return f"[ordo] {event}: {', '.join(parts)}"
    if names:
        return f"[ordo] {event}: {', '.join(names)}"
    return f"[ordo] {event}"


async def run(url: str, verbose: bool = False) -> None:
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

            # Wait for reply (message or error). Ordo events may arrive in between.
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
                    # Quiet by default; one-line summary with --verbose
                    if verbose:
                        print(summarize_ordo_event(data))
                    continue
                if msg_type == "status":
                    continue
                # Unknown – only show when verbose
                if verbose:
                    print(f"[recv] {data}")


def main() -> None:
    p = argparse.ArgumentParser(description="ordo-bot CLI client")
    p.add_argument(
        "--url",
        default="ws://127.0.0.1:8765",
        help="Frontend WebSocket URL (default: ws://127.0.0.1:8765)",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show one-line summaries of Ordo events (jobs_changed, etc.)",
    )
    args = p.parse_args()
    try:
        asyncio.run(run(args.url, verbose=args.verbose))
    except ConnectionRefusedError:
        print(
            f"Could not connect to {args.url}. Is ordo-bot running?",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
