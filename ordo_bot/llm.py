"""
LLM wrapper for ordo-bot.

Talks to any OpenAI-compatible endpoint (local Ollama, Groq, OpenRouter,
xAI, etc.) using the official `openai` Python SDK.

Supports plain chat and tool (function) calling.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import AsyncOpenAI

log = logging.getLogger("ordo_bot.llm")


@dataclass
class ToolCall:
    """One function call requested by the model."""
    id: str
    name: str
    arguments: str  # JSON string from the model


@dataclass
class ChatResult:
    """
    Result of a chat completion.

    content    – assistant text (may be empty if the model only wants tools)
    tool_calls – list of tools the model wants to run (may be empty)
    """
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)


class LLM:
    """
    Thin async client around an OpenAI-compatible chat API.
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
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=api_key or "unused",
        )
        log.debug("LLM ready: model=%s base_url=%s", model, self.base_url)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatResult:
        """
        Send a chat completion.

        messages – conversation so far (may include tool roles)
        tools    – optional OpenAI-style tool schemas

        Returns ChatResult with text and/or tool_calls.
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
        }
        if max_tokens is not None or self.max_tokens is not None:
            kwargs["max_tokens"] = (
                max_tokens if max_tokens is not None else self.max_tokens
            )
        if tools:
            kwargs["tools"] = tools
            # Let the model decide when to call tools
            kwargs["tool_choice"] = "auto"

        log.debug(
            "LLM chat: %d messages, tools=%s, model=%s",
            len(messages),
            bool(tools),
            self.model,
        )
        response = await self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message

        result = ChatResult(content=(message.content or "").strip())

        if message.tool_calls:
            for tc in message.tool_calls:
                result.tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments or "{}",
                    )
                )
            log.debug("LLM requested %d tool call(s)", len(result.tool_calls))

        return result

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """Stream a plain text completion (no tools)."""
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "stream": True,
        }
        if max_tokens is not None or self.max_tokens is not None:
            kwargs["max_tokens"] = (
                max_tokens if max_tokens is not None else self.max_tokens
            )

        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
