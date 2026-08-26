"""
Frontend WebSocket server.

Clients talk to ordo-bot; ordo-bot talks to Ordo and the LLM.

Ordo broadcasts are forwarded here only — they never enter agent history.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Set

import websockets
from websockets.server import WebSocketServerProtocol

from ordo_bot.agent import Agent
from ordo_bot.protocol import (
    AssistantMessage,
    ErrorMessage,
    OrdoEventMessage,
    PongMessage,
    StatusMessage,
    parse_client_message,
)

log = logging.getLogger("ordo_bot.frontend")


class FrontendServer:
    """Async WebSocket server for front-end clients."""

    def __init__(
        self,
        host: str,
        port: int,
        agent: Agent,
        *,
        ordo_connected: bool = False,
        model: str = "",
    ) -> None:
        self.host = host
        self.port = port
        self.agent = agent
        self.ordo_connected = ordo_connected
        self.model = model

        self._server: Any = None
        self._clients: Set[WebSocketServerProtocol] = set()
        self._chat_lock = asyncio.Lock()

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handle_client,
            self.host,
            self.port,
            ping_interval=30,
            ping_timeout=120,
        )
        log.info("Frontend WebSocket listening on ws://%s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self._clients.clear()
        log.info("Frontend WebSocket stopped")

    async def broadcast_ordo_event(self, event: str, data: Dict[str, Any]) -> None:
        """UI-only: never written into agent.messages."""
        msg = OrdoEventMessage(event=event, data=data)
        await self._broadcast(msg.model_dump())

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

    async def _handle_client(self, ws: WebSocketServerProtocol) -> None:
        peer = getattr(ws, "remote_address", None)
        log.info("Client connected: %s", peer)
        self._clients.add(ws)

        status = StatusMessage(
            ordo_connected=self.ordo_connected,
            model=self.model,
        )
        await ws.send(json.dumps(status.model_dump()))

        pending: Set[asyncio.Task] = set()
        try:
            async for raw in ws:
                task = asyncio.create_task(self._handle_message(ws, raw))
                pending.add(task)
                task.add_done_callback(pending.discard)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            log.exception("Error handling client %s", peer)
        finally:
            for task in pending:
                task.cancel()
            self._clients.discard(ws)
            log.info("Client disconnected: %s", peer)

    async def _handle_message(self, ws: WebSocketServerProtocol, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            err = ErrorMessage(message="invalid JSON")
            await ws.send(json.dumps(err.model_dump()))
            return

        try:
            msg = parse_client_message(data)
        except Exception as e:
            err = ErrorMessage(message=f"bad message: {e}")
            await ws.send(json.dumps(err.model_dump()))
            return

        if msg.type == "ping":
            await ws.send(json.dumps(PongMessage().model_dump()))
            return

        if msg.type == "reset":
            self.agent.reset()
            out = AssistantMessage(content="Conversation history cleared.")
            await ws.send(json.dumps(out.model_dump()))
            return

        if msg.type == "chat":
            await self._handle_chat(ws, msg.content)
            return

    async def _handle_chat(self, ws: WebSocketServerProtocol, content: str) -> None:
        async with self._chat_lock:
            try:
                reply = await self.agent.handle_chat(content)
            except Exception as e:
                log.exception("Agent error")
                err = ErrorMessage(message=str(e))
                await ws.send(json.dumps(err.model_dump()))
                return

            out = AssistantMessage(content=reply)
            await ws.send(json.dumps(out.model_dump()))
