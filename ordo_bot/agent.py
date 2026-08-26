"""
Agent brain for ordo-bot.

- Holds conversation history
- Calls the LLM
- When the model requests tools, runs them against Ordo and feeds results back
- Returns a final text reply to the user
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ordo_bot.llm import LLM
from ordo_bot.ordo_client import OrdoClient
from ordo_bot.tools import TOOL_SCHEMAS, run_tool

log = logging.getLogger("ordo_bot.agent")

DEFAULT_SYSTEM_PROMPT = """\
You are ordo-bot, an assistant for the Ordo job scheduler.

You can call tools against the live Ordo instance. Available tools:
  - find_cluster: look up a cluster by name or path
  - read_cluster: full cluster detail + jobs (by id)
  - read_job: full job detail (by id)
  - find_monitor: list servers / monitors
  - find_cal: list calendars / schedules
  - get_documentation: fetch Ordo docs for a section
  - read_org: org / account info
  - start_cluster: START a cluster by id (write — only if user asks)

Use tools when the user asks about their real jobs, clusters, or servers.
Do not invent cluster names or job states — look them up.
Only call start_cluster when the user clearly asks to start/run a cluster.
If the user asks what tools you have, list them.

Be helpful, concise, and practical.
When a tool returns data, summarize the useful parts for the user.
"""

# Safety: don't let a runaway model loop tools forever
MAX_TOOL_ROUNDS = 5


class Agent:
    """
    Conversational agent with optional Ordo tools.

    Usage:
        agent = Agent(llm, ordo=ordo_client)
        reply = await agent.handle_chat("Do you see the Monitoring cluster?")
    """

    def __init__(
        self,
        llm: LLM,
        *,
        ordo: Optional[OrdoClient] = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ) -> None:
        self.llm = llm
        self.ordo = ordo
        self.system_prompt = system_prompt
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

    async def handle_chat(self, user_text: str) -> str:
        """
        Handle one user message. May run several tool rounds, then return text.
        """
        user_text = user_text.strip()
        if not user_text:
            return ""

        log.debug("User: %s", user_text[:200])
        self.messages.append({"role": "user", "content": user_text})

        # Tools only if we have a live Ordo connection
        tools = TOOL_SCHEMAS if self.ordo and self.ordo.is_logged_in else None

        try:
            for round_num in range(MAX_TOOL_ROUNDS):
                result = await self.llm.chat(self.messages, tools=tools)

                # No tool calls → final answer
                if not result.tool_calls:
                    reply = result.content or "(no response)"
                    self.messages.append(
                        {"role": "assistant", "content": reply}
                    )
                    log.debug("Assistant: %s", reply[:200])
                    return reply

                # Model wants tools — record the assistant message with tool_calls
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

                # Run each tool and append results
                for tc in result.tool_calls:
                    args = self._parse_args(tc.arguments)
                    if self.ordo is None:
                        tool_result = json.dumps(
                            {"error": "Ordo client not available"}
                        )
                    else:
                        tool_result = await run_tool(self.ordo, tc.name, args)

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

            # Hit max rounds — ask for a final answer without tools
            log.warning("Hit MAX_TOOL_ROUNDS=%d", MAX_TOOL_ROUNDS)
            result = await self.llm.chat(self.messages, tools=None)
            reply = result.content or "(stopped after too many tool calls)"
            self.messages.append({"role": "assistant", "content": reply})
            return reply

        except Exception as e:
            log.error("Agent error: %s", e)
            # Roll back the user message on hard failure
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

    def reset(self) -> None:
        """Clear conversation history (keep system prompt)."""
        self.messages = [{"role": "system", "content": self.system_prompt}]
        log.info("Conversation history cleared")
