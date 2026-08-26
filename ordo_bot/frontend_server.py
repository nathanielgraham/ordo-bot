"""
Frontend WebSocket server.

Chat queue + completion watches driven by Ordo broadcasts.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

import websockets
from websockets.server import WebSocketServerProtocol

from ordo_bot.agent import Agent
from ordo_bot.protocol import (
    AckMessage,
    AssistantMessage,
    ErrorMessage,
    OrdoEventMessage,
    PongMessage,
    ProgressMessage,
    StatusMessage,
    parse_client_message,
)
from ordo_bot.watches import WatchRegistry

log = logging.getLogger("ordo_bot.frontend")


@dataclass
class _QueuedChat:
    ws: WebSocketServerProtocol
    content: str


class FrontendServer:
    def __init__(
        self,
        host: str,
        port: int,
        agent: Agent,
        *,
        ordo_connected: bool = False,
        model: str = "",
        watches: Optional[WatchRegistry] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.agent = agent
        self.ordo_connected = ordo_connected
        self.model = model
        self.watches = watches or WatchRegistry()

        self._server: Any = None
        self._clients: Set[WebSocketServerProtocol] = set()
        self._queue: asyncio.Queue[Optional[_QueuedChat]] = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._epoch: int = 0

    async def start(self) -> None:
        self._worker_task = asyncio.create_task(self._chat_worker())
        self._server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=120,
        )
        log.info("Frontend WebSocket listening on ws://%s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._worker_task is not None:
            await self._queue.put(None)
            try:
                await asyncio.wait_for(self._worker_task, timeout=5)
            except Exception:
                self._worker_task.cancel()
            self._worker_task = None

        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self._clients.clear()
        log.info("Frontend WebSocket stopped")

    async def broadcast_ordo_event(self, event: str, data: Dict[str, Any]) -> None:
        msg = OrdoEventMessage(event=event, data=data)
        await self._broadcast(msg.model_dump())
        # Completion watches (async notify, not in agent history)
        await self._fire_watches(event, data)

    async def _fire_watches(self, event: str, data: Dict[str, Any]) -> None:
        fired = self.watches.match_broadcast(event, data)
        for item in fired:
            client = item.get("client")
            text = item.get("text") or ""
            if client is None:
                # Notify all clients if no specific socket
                await self._broadcast(
                    AssistantMessage(content=text).model_dump()
                )
            else:
                await self._safe_send(
                    client, AssistantMessage(content=text).model_dump()
                )

    async def broadcast_status(self) -> None:
        msg = StatusMessage(
            ordo_connected=self.ordo_connected,
            model=self.model,
        )
        await self._broadcast(msg.model_dump())

    async def _broadcast(self, payload: dict) -> None:
        if not self._clients:
            return
        raw = json.dumps(payload)
        dead = []
        for ws in self._clients:
            try:
                await ws.send(raw)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    async def _safe_send(self, ws: WebSocketServerProtocol, payload: dict) -> None:
        try:
            await ws.send(json.dumps(payload))
        except Exception:
            self._clients.discard(ws)

    def _drain_queue(self) -> int:
        n = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is None:
                self._queue.put_nowait(None)
                break
            n += 1
            self._queue.task_done()
        return n

    async def _chat_worker(self) -> None:
        log.info("Chat queue worker started")
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return

                epoch = self._epoch
                ws, content = item.ws, item.content

                await self._safe_send(
                    ws, ProgressMessage(content="processing").model_dump()
                )

                try:
                    reply = await self.agent.handle_chat(
                        content, client=ws
                    )
                except Exception as e:
                    log.exception("Agent error")
                    if self._epoch == epoch:
                        await self._safe_send(
                            ws, ErrorMessage(message=str(e)).model_dump()
                        )
                    continue

                if self._epoch != epoch:
                    log.info("Dropping stale reply after reset")
                    continue

                await self._safe_send(
                    ws, AssistantMessage(content=reply).model_dump()
                )
            finally:
                self._queue.task_done()

    async def _handle_client(self, ws: WebSocketServerProtocol) -> None:
        peer = getattr(ws, "remote_address", None)
        log.info("Client connected: %s", peer)
        self._clients.add(ws)

        status = StatusMessage(
            ordo_connected=self.ordo_connected,
            model=self.model,
        )
        await self._safe_send(ws, status.model_dump())

        try:
            async for raw in ws:
                await self._handle_message(ws, raw)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            log.exception("Error handling client %s", peer)
        finally:
            self.watches.clear_for_client(ws)
            self._clients.discard(ws)
            log.info("Client disconnected: %s", peer)

    async def _handle_message(self, ws: WebSocketServerProtocol, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await self._safe_send(ws, ErrorMessage(message="invalid JSON").model_dump())
            return

        try:
            msg = parse_client_message(data)
        except Exception as e:
            await self._safe_send(
                ws, ErrorMessage(message=f"bad message: {e}").model_dump()
            )
            return

        if msg.type == "ping":
            await self._safe_send(ws, PongMessage().model_dump())
            return

        if msg.type == "reset":
            self._epoch += 1
            dropped = self._drain_queue()
            self.watches.clear_all()
            self.agent.reset()
            log.info("Reset: history + queue + watches cleared")
            note = "Conversation history cleared."
            if dropped:
                note += f" Dropped {dropped} queued message(s)."
            await self._safe_send(ws, AssistantMessage(content=note).model_dump())
            return

        if msg.type == "chat":
            depth = self._queue.qsize()
            await self._queue.put(_QueuedChat(ws=ws, content=msg.content))
            await self._safe_send(
                ws,
                AckMessage(content="received", queue_depth=depth).model_dump(),
            )
            return
