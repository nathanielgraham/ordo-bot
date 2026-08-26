#!/usr/bin/env python3
"""
Reference CLI client for ordo-bot.

Fire-and-forget input; redraws you> after bot lines so the prompt returns
without needing a blank Enter.
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


def _print_bot_line(prefix: str, text: str, *, redraw_prompt: bool = False) -> None:
    print(f"\r{prefix}{text}")
    if redraw_prompt:
        print("you> ", end="", flush=True)


async def _read_line(prompt: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))


async def run(url: str, verbose: bool = False) -> None:
    print(f"Connecting to {url} ...")
    ws = await websockets.connect(url, ping_interval=20, ping_timeout=20)

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
            "Type a message and press Enter (fire-and-forget; no need to wait).\n"
            "'quit' to exit, 'reset' to clear history + queue.\n"
        )

        disconnect = asyncio.Event()
        stop_reader = asyncio.Event()
        # True while blocked in input() — so we can re-print you> after bot lines
        at_prompt = asyncio.Event()

        async def recv_loop() -> None:
            try:
                while not stop_reader.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        if ws.close_code is not None:
                            break
                        continue
                    except ConnectionClosed:
                        break

                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        _print_bot_line(
                            "bot> ",
                            f"(non-JSON) {raw}",
                            redraw_prompt=at_prompt.is_set(),
                        )
                        continue

                    msg_type = data.get("type")
                    redraw = at_prompt.is_set()

                    if msg_type == "ack":
                        depth = data.get("queue_depth", 0)
                        msg = (
                            f"received (queued behind {depth})"
                            if depth
                            else "received"
                        )
                        _print_bot_line("… ", msg, redraw_prompt=redraw)
                    elif msg_type == "progress":
                        _print_bot_line(
                            "… ",
                            data.get("content") or "processing",
                            redraw_prompt=redraw,
                        )
                    elif msg_type == "message":
                        _print_bot_line(
                            "bot> ",
                            data.get("content") or "",
                            redraw_prompt=False,
                        )
                        print()
                        if redraw:
                            print("you> ", end="", flush=True)
                    elif msg_type == "error":
                        _print_bot_line(
                            "bot> ERROR: ",
                            data.get("message") or "",
                            redraw_prompt=False,
                        )
                        print()
                        if redraw:
                            print("you> ", end="", flush=True)
                    elif msg_type == "ordo_event":
                        if verbose:
                            _print_bot_line(
                                "",
                                summarize_ordo_event(data),
                                redraw_prompt=redraw,
                            )
                    elif msg_type in ("status", "pong"):
                        continue
                    elif verbose:
                        _print_bot_line("[recv] ", str(data), redraw_prompt=redraw)
            finally:
                disconnect.set()

        reader = asyncio.create_task(recv_loop())

        try:
            while True:
                if disconnect.is_set():
                    print("\n[disconnected] ordo-bot closed the connection.")
                    break

                at_prompt.set()
                line_task = asyncio.create_task(_read_line("you> "))
                disc_task = asyncio.create_task(disconnect.wait())
                done, _pending = await asyncio.wait(
                    {line_task, disc_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                at_prompt.clear()

                if disc_task in done:
                    line_task.cancel()
                    try:
                        await line_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    print("\n[disconnected] ordo-bot closed the connection.")
                    break

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
                except Exception:
                    if disconnect.is_set():
                        print("\n[disconnected] ordo-bot closed the connection.")
                        break
                    raise

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
        finally:
            stop_reader.set()
            reader.cancel()
            try:
                await reader
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        try:
            await ws.close()
        except Exception:
            pass


def main() -> None:
    p = argparse.ArgumentParser(description="ordo-bot CLI client")
    p.add_argument("--url", default="ws://127.0.0.1:8765")
    p.add_argument("--verbose", "-v", action="store_true")
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
