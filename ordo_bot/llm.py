"""
LLM wrapper for ordo-bot.

Talks to any OpenAI-compatible endpoint (local Ollama, Groq, OpenRouter,
xAI, etc.) using the official `openai` Python SDK.

Why a thin wrapper?
  - One place to configure base_url / api_key / model
  - Easy to swap providers later without touching the agent
  - Keeps streaming and non-streaming call sites simple

Default for development: local Ollama at http://localhost:11434/v1
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

log = logging.getLogger("ordo_bot.llm")


class LLM:
    """
    Thin async client around an OpenAI-compatible chat API.

    Typical usage:

        llm = LLM(base_url="http://localhost:11434/v1", api_key="ollama", model="llama3.2")
        reply = await llm.chat([{"role": "user", "content": "Hello"}])
        async for chunk in llm.chat_stream([...]):
            print(chunk, end="")
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> None:
        """
        base_url  – e.g. http://localhost:11434/v1  (Ollama)
                    or   https://api.x.ai/v1
        api_key   – provider key (Ollama ignores it, but the SDK still wants one)
        model     – model name the provider understands
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

        # AsyncOpenAI works with any OpenAI-compatible server
        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=api_key or "unused",
        )
        log.debug("LLM ready: model=%s base_url=%s", model, self.base_url)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Send a chat completion request and return the full assistant reply.

        messages – list of {"role": "system"|"user"|"assistant", "content": "..."}
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
        }
        if max_tokens is not None or self.max_tokens is not None:
            kwargs["max_tokens"] = max_tokens if max_tokens is not None else self.max_tokens

        log.debug("LLM chat request: %d messages, model=%s", len(messages), self.model)
        response = await self._client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        content = choice.message.content or ""
        log.debug("LLM chat reply length=%d", len(content))
        return content

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """
        Stream a chat completion. Yields text chunks as they arrive.

        Useful for the frontend WebSocket so the user sees typing in real time.
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "stream": True,
        }
        if max_tokens is not None or self.max_tokens is not None:
            kwargs["max_tokens"] = max_tokens if max_tokens is not None else self.max_tokens

        log.debug("LLM stream request: %d messages, model=%s", len(messages), self.model)

        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
