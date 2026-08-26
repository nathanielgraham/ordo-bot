"""
Ordo WebSocket client.

This module talks to an Ordo instance over its WebSocket API.
It is the "Ordo side" of ordo-bot.

Protocol summary (from Ordo docs + existing wsagent.py):

  1. Connect to the WebSocket URL (e.g. wss://ordoscheduler.com/websocket).
  2. Send:  {"command": "login_user", "token": "<API token>"}
  3. On success the server replies with success=true and we are registered
     for live broadcasts (jobs_changed, clusters_changed, ...).
  4. Thereafter we send ordinary commands as JSON objects and receive
     either:
       - command replies  (they contain "command_reply": "<name>")
       - unsolicited broadcasts (they contain "broadcast": "<name>")

We use the async `websockets` library so the client fits cleanly into
an asyncio-based bot.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets
from websockets.client import WebSocketClientProtocol

log = logging.getLogger("ordo_bot.ordo_client")

# Type alias for the callback that receives every server message
MessageHandler = Callable[[Dict[str, Any]], Awaitable[None]]


class OrdoClient:
    """
    Async WebSocket client for Ordo.

    Typical usage:

        client = OrdoClient(url, token)
        client.on_message = my_handler          # optional callback
        await client.connect()
        reply = await client.send_command({"command": "find_cluster", "name": "/root"})
        ...
        await client.close()
    """

    def __init__(
        self,
        url: str,
        token: str,
        *,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0,
    ) -> None:
        """
        Create a client (does not connect yet).

        url   – WebSocket URL of the Ordo instance
        token – API token (from Ordo Settings)
        """
        self.url = url
        self.token = token
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout

        # Internal state
        self._ws: Optional[WebSocketClientProtocol] = None
        self._receiver_task: Optional[asyncio.Task] = None
        self._connected = asyncio.Event()
        self._logged_in = asyncio.Event()
        self._closed = asyncio.Event()

        # Optional user-supplied callback for every message from Ordo
        self.on_message: Optional[MessageHandler] = None

        # Pending request/response matching (simple: one outstanding at a time
        # is enough for v1; we can improve later if needed)
        self._pending_reply: Optional[asyncio.Future] = None
        self._pending_command: Optional[str] = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """True once the WebSocket is open."""
        return self._connected.is_set() and not self._closed.is_set()

    @property
    def is_logged_in(self) -> bool:
        """True after a successful login_user."""
        return self._logged_in.is_set()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, login_timeout: float = 30.0) -> None:
        """
        Open the WebSocket and log in.

        Raises on connection or login failure.
        """
        if self._ws is not None:
            raise RuntimeError("Already connected")

        log.info("Connecting to %s", self.url)
        self._ws = await websockets.connect(
            self.url,
            ping_interval=self.ping_interval,
            ping_timeout=self.ping_timeout,
            max_size=2**20,  # 1 MiB – plenty for Ordo messages
        )
        self._connected.set()
        log.info("WebSocket open")

        # Start the background receiver before we send login
        self._receiver_task = asyncio.create_task(
            self._receive_loop(), name="ordo-receiver"
        )

        # Send login
        login_cmd = {"command": "login_user", "token": self.token}
        log.debug("Sending login_user")
        await self._ws.send(json.dumps(login_cmd))

        # Wait for the login reply (handled inside _receive_loop)
        try:
            await asyncio.wait_for(self._logged_in.wait(), timeout=login_timeout)
        except asyncio.TimeoutError:
            await self.close()
            raise TimeoutError("Ordo login timed out")

        if not self.is_logged_in:
            await self.close()
            raise RuntimeError("Ordo login failed")

        log.info("Logged in to Ordo successfully")

    async def close(self) -> None:
        """
        Cleanly shut down the connection.
        Safe to call multiple times.
        """
        if self._closed.is_set():
            return
        self._closed.set()
        self._connected.clear()
        self._logged_in.clear()

        if self._receiver_task and not self._receiver_task.done():
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        log.info("Ordo client closed")

    # ------------------------------------------------------------------
    # Sending commands
    # ------------------------------------------------------------------

    async def send_command(
        self,
        command: Dict[str, Any],
        *,
        wait_for_reply: bool = True,
        timeout: float = 60.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Send a command to Ordo.

        command        – dict that must contain at least "command": "..."
        wait_for_reply – if True, wait for the matching command_reply
        timeout        – how long to wait for the reply

        Returns the reply dict, or None if wait_for_reply is False.
        """
        if not self.is_logged_in or self._ws is None:
            raise RuntimeError("Not logged in")

        cmd_name = command.get("command")
        if not cmd_name:
            raise ValueError("command dict must contain a 'command' key")

        if wait_for_reply:
            if self._pending_reply is not None:
                raise RuntimeError(
                    "Another command is already waiting for a reply "
                    "(v1 supports only one outstanding request)"
                )
            loop = asyncio.get_running_loop()
            self._pending_reply = loop.create_future()
            self._pending_command = cmd_name

        log.debug("Sending command: %s", cmd_name)
        await self._ws.send(json.dumps(command))

        if not wait_for_reply:
            return None

        try:
            reply = await asyncio.wait_for(self._pending_reply, timeout=timeout)
            return reply
        except asyncio.TimeoutError:
            raise TimeoutError(f"Timed out waiting for reply to '{cmd_name}'")
        finally:
            self._pending_reply = None
            self._pending_command = None

    # Convenience helpers for common commands -----------------------------

    async def get_documentation(
        self, section: str = "overview", format: str = "markdown"
    ) -> Dict[str, Any]:
        """Fetch a documentation section."""
        return await self.send_command(
            {
                "command": "get_documentation",
                "section": section,
                "format": format,
            }
        )

    async def find_cluster(self, name: str) -> Dict[str, Any]:
        """Look up a cluster by path/name."""
        return await self.send_command(
            {"command": "find_cluster", "name": name}
        )

    async def read_cluster(self, cluster_id: int) -> Dict[str, Any]:
        """Read one cluster by id (includes nested jobs)."""
        return await self.send_command({"command": "read_cluster", "id": cluster_id})

    async def read_job(self, job_id: int) -> Dict[str, Any]:
        """Read one job by id."""
        return await self.send_command({"command": "read_job", "id": job_id})

    async def start_cluster(self, cluster_id: int) -> Dict[str, Any]:
        """Start a cluster by id (WRITE action)."""
        return await self.send_command({"command": "start_cluster", "id": cluster_id})

    # ------------------------------------------------------------------
    # Internal receiver
    # ------------------------------------------------------------------

    async def _receive_loop(self) -> None:
        """
        Background task that reads every message from Ordo and dispatches it.
        """
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("Received non-JSON message: %s", raw[:200])
                    continue

                await self._dispatch(data)

        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosed as e:
            log.info("Ordo WebSocket closed: %s", e)
        except Exception:
            log.exception("Error in Ordo receive loop")
        finally:
            self._connected.clear()
            self._logged_in.clear()
            # Unblock any waiter
            if self._pending_reply and not self._pending_reply.done():
                self._pending_reply.set_exception(
                    ConnectionError("Connection closed while waiting for reply")
                )

    async def _dispatch(self, data: Dict[str, Any]) -> None:
        """
        Handle one decoded message from Ordo.
        """
        # ---- login handling ----
        if data.get("command_reply") == "login_user":
            if data.get("success"):
                self._logged_in.set()
                log.debug("login_user succeeded")
            else:
                log.error("login_user failed: %s", data)
                # Leave _logged_in unset; connect() will notice and raise
            # Fall through so the optional on_message still sees it

        # ---- match pending command reply ----
        reply_name = data.get("command_reply")
        if (
            reply_name
            and self._pending_reply is not None
            and not self._pending_reply.done()
            and reply_name == self._pending_command
        ):
            self._pending_reply.set_result(data)

        # ---- optional user callback (broadcasts + everything else) ----
        if self.on_message is not None:
            try:
                await self.on_message(data)
            except Exception:
                log.exception("Error in on_message callback")
