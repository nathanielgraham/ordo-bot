#!/usr/bin/env python3
"""
Reference CLI client for ordo-bot.

    python clients/cli.py
    python clients/cli.py --verbose

Commands:
  quit / exit  — leave
  reset        — clear agent conversation history

If the bot process stops, the client prints a message and exits
instead of sitting forever on the prompt.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List, Optional

try:
    import readline  # noqa: F401
except ImportError:
    pass

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
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


async def _read_line(prompt: str) -> str:
    """Blocking input in a thread so the event loop can still watch the socket."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))


async def run(url: str, verbose: bool = False) -> None:
    print(f"Connecting to {url} ...")
    try:
        ws = await websockets.connect(url, ping_interval=20, ping_timeout=20)
    except ConnectionRefusedError:
        raise

    try:
        raw = await ws.recv()
        try:
            status = json.loads(raw)
            print(
                f"Connected. ordo_connected={status.get('ordo_connected')} "
                f"model={status.get('model')}"
            )
        except Exception:
            print(f"Connected. (first message: {raw})")

        print(
            "Type a message and press Enter. "
            "'quit' to exit, 'reset' to clear history.\n"
        )

        # Background task: notice server death while we sit at the prompt
        disconnect = asyncio.Event()

        async def watch_connection() -> None:
            """
            Wait until the server closes. We do not consume chat replies here;
            those are read in the main loop. ping failures / close frames
            surface as ConnectionClosed on recv.
            """
            try:
                # Wait until the connection is closed without stealing messages.
                # connection_lost / wait_closed is the right signal.
                await ws.wait_closed()
            except Exception:
                pass
            finally:
                disconnect.set()

        watcher = asyncio.create_task(watch_connection())

        try:
            while True:
                if disconnect.is_set() or ws.close_code is not None:
                    print("\n[disconnected] ordo-bot closed the connection.")
                    break

                # Race: user types a line OR server dies
                line_task = asyncio.create_task(_read_line("you> "))
                disc_task = asyncio.create_task(disconnect.wait())
                done, pending = await asyncio.wait(
                    {line_task, disc_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if disc_task in done:
                    line_task.cancel()
                    try:
                        await line_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    print("\n[disconnected] ordo-bot closed the connection.")
                    break

                # User line ready
                disc_task.cancel()
                try:
                    await disc_task
                except (asyncio.CancelledError, Exception):
                    pass

                try:
                    user_text = line_task.result()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                except Exception as e:
                    # e.g. input error after disconnect
                    if disconnect.is_set():
                        print("\n[disconnected] ordo-bot closed the connection.")
                        break
                    raise e

                user_text = user_text.strip()
                if not user_text:
                    continue
                if user_text.lower() in {"quit", "exit"}:
                    break

                try:
                    if user_text.lower() in {"reset", "/reset"}:
                        await ws.send(json.dumps({"type": "reset"}))
                    else:
                        await ws.send(
                            json.dumps({"type": "chat", "content": user_text})
                        )
                except ConnectionClosed:
                    print("\n[disconnected] ordo-bot closed the connection.")
                    break

                # Wait for assistant message / error (skip events)
                got_reply = False
                while not got_reply:
                    if disconnect.is_set():
                        print("\n[disconnected] ordo-bot closed the connection.")
                        got_reply = True
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    except ConnectionClosed:
                        print("\n[disconnected] ordo-bot closed the connection.")
                        got_reply = True
                        break

                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        print(f"bot> (non-JSON) {raw}")
                        break

                    msg_type = data.get("type")
                    if msg_type == "message":
                        print(f"bot> {data.get('content', '')}")
                        print()
                        got_reply = True
                    elif msg_type == "error":
                        print(f"bot> ERROR: {data.get('message', '')}")
                        print()
                        got_reply = True
                    elif msg_type == "ordo_event":
                        if verbose:
                            print(summarize_ordo_event(data))
                    elif msg_type == "status":
                        continue
                    elif verbose:
                        print(f"[recv] {data}")

                if disconnect.is_set():
                    break
        finally:
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


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
    except ConnectionClosed:
        print("[disconnected] ordo-bot closed the connection.", file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()
