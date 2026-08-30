"""Ordo socket for the bot: thin adapter over AsyncOrdoClient."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ordo_wsagent import AsyncOrdoClient

log = logging.getLogger("ordo_bot.ordo_client")


class OrdoClient(AsyncOrdoClient):
    """Bot-facing name. Argument order matches existing main.py / tests."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0,
    ) -> None:
        super().__init__(
            token=token,
            url=url,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._closed.is_set()

    async def send_command(
        self,
        command: Dict[str, Any],
        *,
        wait_for_reply: bool = True,
        wait: Optional[bool] = None,
        timeout: float = 60.0,
    ) -> Optional[Dict[str, Any]]:
        do_wait = wait_for_reply if wait is None else wait
        return await super().send_command(command, timeout=timeout, wait=do_wait)

    async def get_documentation(
        self, section: str = "overview", format: str = "markdown"
    ) -> Dict[str, Any]:
        return await self.command(
            "get_documentation", section=section, format=format
        )
