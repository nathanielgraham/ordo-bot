"""
Minimal agent brain for ordo-bot.

For now this is intentionally simple:
  - Holds a short conversation history
  - Sends messages to the LLM
  - Returns the assistant reply

Later we will add:
  - Tool calling (Ordo commands)
  - Streaming replies to the frontend
  - Handling of live Ordo broadcasts
"""

from __future__ import annotations

import logging
from typing import Dict, List

from ordo_bot.llm import LLM

log = logging.getLogger("ordo_bot.agent")

# System prompt that tells the model what it is.
# Keep it short; we will expand it when tools arrive.
DEFAULT_SYSTEM_PROMPT = """\
You are ordo-bot, an assistant that helps the user work with the Ordo job scheduler.
You are helpful, concise, and practical.
When you don't know something, say so clearly.
"""


class Agent:
    """
    Very small conversational agent.

    Usage:
        agent = Agent(llm)
        reply = await agent.handle_chat("Hello")
    """

    def __init__(self, llm: LLM, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
        self.llm = llm
        self.system_prompt = system_prompt

        # Conversation history (system + alternating user/assistant messages)
        self.messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt},
        ]

    async def handle_chat(self, user_text: str) -> str:
        """
        Accept a user message, call the LLM, return the assistant reply.

        Also appends both the user message and the reply to history
        so the next turn has context.
        """
        user_text = user_text.strip()
        if not user_text:
            return ""

        log.debug("User: %s", user_text[:200])
        self.messages.append({"role": "user", "content": user_text})

        try:
            reply = await self.llm.chat(self.messages)
        except Exception as e:
            log.error("LLM call failed: %s", e)
            # Roll back the user message so a retry is clean
            self.messages.pop()
            return f"(error talking to the model: {e})"

        reply = (reply or "").strip()
        log.debug("Assistant: %s", reply[:200])
        self.messages.append({"role": "assistant", "content": reply})
        return reply

    def reset(self) -> None:
        """Clear conversation history (keep only the system prompt)."""
        self.messages = [{"role": "system", "content": self.system_prompt}]
        log.info("Conversation history cleared")
