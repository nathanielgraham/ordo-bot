"""
Agent brain for ordo-bot.

- Holds conversation history (capped, configurable)
- Calls the LLM with read-only tools by default; write tools when asked
- Bootstraps once with get_documentation(summary) so the model knows Ordo
- Ordo broadcasts never enter this history (frontend-only)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ordo_bot.llm import LLM
from ordo_bot.ordo_client import OrdoClient
from ordo_bot.tools import (
    DEFAULT_TOOL_RESULT_MAX_CHARS,
    run_tool,
    tools_for_turn,
    user_wants_write,
)

log = logging.getLogger("ordo_bot.agent")

DEFAULT_SYSTEM_PROMPT = """\
You are ordo-bot, an assistant for the Ordo job scheduler.

You talk to a live Ordo instance via tools.

Default tools are READ-ONLY (browse clusters/jobs, monitors, calendars, logs, docs).
WRITE tools (start/kill/create/delete/update/...) are only available when the user
clearly asks to change something. Do not invent ids or states — look them up.

At session start you may receive a short documentation summary; use it.
Prefer compact answers. When listing trees, use names, ids, and states — not full scripts.

If the user says "reset" about the conversation, they are clearing chat history
(handled outside tools); do not confuse that with reset_cluster.
"""

MAX_TOOL_ROUNDS = 5

# 0 = unlimited (larger models / paid tiers may prefer this)
DEFAULT_MAX_HISTORY_MESSAGES = 24


class Agent:
    """
    Conversational agent with optional Ordo tools.

    Usage:
        agent = Agent(llm, ordo=ordo_client, max_history_messages=24)
        await agent.bootstrap()   # once after Ordo login
        reply = await agent.handle_chat("Show the Monitoring cluster")
    """

    def __init__(
        self,
        llm: LLM,
        *,
        ordo: Optional[OrdoClient] = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
        tool_result_max_chars: int = DEFAULT_TOOL_RESULT_MAX_CHARS,
        bootstrap_docs: bool = True,
    ) -> None:
        self.llm = llm
        self.ordo = ordo
        self.system_prompt = system_prompt
        # 0 or negative => do not cap
        self.max_history_messages = max_history_messages
        self.tool_result_max_chars = tool_result_max_chars
        self.bootstrap_docs = bootstrap_docs
        self._bootstrapped = False
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

    async def bootstrap(self) -> None:
        """
        Once after Ordo login: fetch a short docs summary into context.

        Does not count as a user turn. Skipped if already done or no Ordo.
        """
        if self._bootstrapped or not self.bootstrap_docs:
            return
        if not self.ordo or not self.ordo.is_logged_in:
            return

        log.info("Bootstrapping agent with get_documentation(summary)")
        try:
            summary = await run_tool(
                self.ordo,
                "get_documentation",
                {"section": "api", "format": "summary"},
                max_chars=self.tool_result_max_chars,
            )
            # Inject as a system note so the model knows the surface without a user ask
            self.messages.append(
                {
                    "role": "system",
                    "content": (
                        "Ordo API documentation summary (bootstrapped; do not repeat unless asked):\n"
                        + summary
                    ),
                }
            )
            self._trim_history()
            self._bootstrapped = True
            log.info("Bootstrap docs loaded (%d chars)", len(summary))
        except Exception as e:
            log.warning("Bootstrap docs failed: %s", e)

    async def handle_chat(self, user_text: str) -> str:
        """
        Handle one user message. May run several tool rounds, then return text.
        """
        user_text = user_text.strip()
        if not user_text:
            return ""

        # Ensure bootstrap once we have Ordo
        if not self._bootstrapped:
            await self.bootstrap()

        log.debug("User: %s", user_text[:200])
        self.messages.append({"role": "user", "content": user_text})

        allow_write = user_wants_write(user_text)
        tools = None
        if self.ordo and self.ordo.is_logged_in:
            tools = tools_for_turn(allow_write=allow_write)
            if allow_write:
                log.info("Write tools enabled for this turn")

        try:
            for round_num in range(MAX_TOOL_ROUNDS):
                result = await self.llm.chat(self.messages, tools=tools)

                if not result.tool_calls:
                    reply = result.content or "(no response)"
                    self.messages.append(
                        {"role": "assistant", "content": reply}
                    )
                    self._trim_history()
                    log.debug("Assistant: %s", reply[:200])
                    return reply

                assistant_msg: Dict[str, Any] = {
                    "role": "assistant",
                    "content": result.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": tc.arguments,
                            },
                        }
                        for tc in result.tool_calls
                    ],
                }
                self.messages.append(assistant_msg)

                for tc in result.tool_calls:
                    args = self._parse_args(tc.arguments)
                    # Refuse write tools if this turn is read-only
                    if (
                        not allow_write
                        and tc.name
                        and tc.name
                        not in {
                            t["function"]["name"]
                            for t in tools_for_turn(allow_write=False)
                        }
                    ):
                        tool_result = json.dumps(
                            {
                                "error": (
                                    f"Tool {tc.name} is write-only. "
                                    "Ask the user to confirm a change first."
                                )
                            }
                        )
                    elif self.ordo is None:
                        tool_result = json.dumps(
                            {"error": "Ordo client not available"}
                        )
                    else:
                        tool_result = await run_tool(
                            self.ordo,
                            tc.name,
                            args,
                            max_chars=self.tool_result_max_chars,
                        )

                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": tool_result,
                        }
                    )
                    log.info(
                        "Tool %s finished (round %d, %d chars)",
                        tc.name,
                        round_num + 1,
                        len(tool_result),
                    )

            log.warning("Hit MAX_TOOL_ROUNDS=%d", MAX_TOOL_ROUNDS)
            result = await self.llm.chat(self.messages, tools=None)
            reply = result.content or "(stopped after too many tool calls)"
            self.messages.append({"role": "assistant", "content": reply})
            self._trim_history()
            return reply

        except Exception as e:
            log.error("Agent error: %s", e)
            if self.messages and self.messages[-1].get("role") == "user":
                self.messages.pop()
            return f"(error: {e})"

    def _parse_args(self, raw: str) -> Dict[str, Any]:
        try:
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            log.warning("Bad tool arguments JSON: %s", raw[:200])
            return {}

    def _trim_history(self) -> None:
        """
        Keep system messages + the last N non-system messages.

        max_history_messages <= 0 means unlimited (good for large-context models).
        """
        cap = self.max_history_messages
        if cap is None or cap <= 0:
            return

        system = [m for m in self.messages if m.get("role") == "system"]
        rest = [m for m in self.messages if m.get("role") != "system"]
        if len(rest) <= cap:
            return

        # Drop oldest non-system messages; avoid starting mid tool-call block
        rest = rest[-cap:]
        # If we start with a bare tool result, drop until a user/assistant boundary
        while rest and rest[0].get("role") == "tool":
            rest.pop(0)

        self.messages = system + rest
        log.debug(
            "History trimmed: %d system + %d messages",
            len(system),
            len(rest),
        )

    def reset(self) -> None:
        """Clear conversation history (keep system prompt). Re-bootstrap next chat."""
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self._bootstrapped = False
        log.info("Conversation history cleared")
