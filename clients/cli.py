#!/usr/bin/env python3
"""
Reference CLI client for ordo-bot.

    python clients/cli.py
    python clients/cli.py --verbose

Commands:
  quit / exit  — leave
  reset        — clear agent conversation history
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List

try:
    import readline  # noqa: F401
except ImportError:
    pass

try:
    import websockets
except ImportError:
    print("Need websockets: pip install websockets", file=sys.stderr)
    sys.exit(1)


def summarize_ordo_event(data: Dict[str, Any]) -> str:
    event = data.get("event") or "?"
    payload = data.get("data") or {}
    updates: List[dict] = payload.get("updates") or []
    names: List[str] = []
    states: List[str] = []

    for u in updates[:3]:
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
    async with websockets.connect(url, ping_interval=30, ping_timeout=120) as ws:
        raw = await ws.recv()
        try:
            status = json.loads(raw)
            print(
                f"Connected. ordo_connected={status.get('ordo_connected')} "
                f"model={status.get('model')}"
            )
        except Exception:
            print(f"Connected. (first message: {raw})")

        print("Type a message and press Enter. 'quit' to exit, 'reset' to clear history.\n")

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

            if user_text.lower() in {"reset", "/reset"}:
                await ws.send(json.dumps({"type": "reset"}))
            else:
                await ws.send(json.dumps({"type": "chat", "content": user_text}))

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
                    if verbose:
                        print(summarize_ordo_event(data))
                    continue
                if msg_type == "status":
                    continue
                if verbose:
                    print(f"[recv] {data}")


def main() -> None:
    p = argparse.ArgumentParser(description="ordo-bot CLI client")
    p.add_argument(
        "--url",
        default="ws://127.0.0.1:8765",
        help="Frontend WebSocket URL",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show one-line summaries of Ordo events",
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
    except (KeyboardInterrupt, asyncio.CancelledError):
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()
