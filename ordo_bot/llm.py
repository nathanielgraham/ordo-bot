"""
LLM wrapper for ordo-bot.

Talks to any OpenAI-compatible endpoint (Ollama, Groq, OpenRouter, xAI, …)
using the official `openai` Python SDK.

Includes request timeout and light retries for transient provider errors.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

log = logging.getLogger("ordo_bot.llm")

# Default: fail a single completion rather than hang forever
DEFAULT_LLM_TIMEOUT_SEC = 90.0
DEFAULT_LLM_RETRIES = 2
DEFAULT_LLM_RETRY_BACKOFF_SEC = 1.5


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


def _is_retryable(exc: BaseException) -> bool:
    """True if another attempt might succeed."""
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        # 408 timeout, 429 rate limit, 5xx server errors
        if exc.status_code in {408, 429, 500, 502, 503, 504}:
            return True
        # Groq: model emitted unparseable tool JSON
        body = getattr(exc, "body", None) or {}
        err = body.get("error") if isinstance(body, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        if code in {"output_parse_failed", "tool_use_failed"}:
            return True
        msg = str(exc).lower()
        if "output_parse_failed" in msg or "rate_limit" in msg or "tokens per minute" in msg:
            return True
    # Some SDKs wrap as generic Exception with useful text
    msg = str(exc).lower()
    if "rate_limit" in msg or "timeout" in msg or "output_parse_failed" in msg:
        return True
    return False


class LLM:
    """Thin async client around an OpenAI-compatible chat API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout_sec: float = DEFAULT_LLM_TIMEOUT_SEC,
        max_retries: int = DEFAULT_LLM_RETRIES,
        retry_backoff_sec: float = DEFAULT_LLM_RETRY_BACKOFF_SEC,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout_sec = timeout_sec
        self.max_retries = max(0, max_retries)
        self.retry_backoff_sec = retry_backoff_sec

        # timeout applies to each HTTP call (connect + read)
        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=api_key or "unused",
            timeout=timeout_sec,
            max_retries=0,  # we handle retries ourselves for clearer control
        )
        log.debug(
            "LLM ready: model=%s base_url=%s timeout=%ss retries=%s",
            model,
            self.base_url,
            timeout_sec,
            self.max_retries,
        )

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatResult:
        """
        Send a chat completion with timeout + limited retries.

        On output_parse_failed after retries, one final attempt without tools
        so the model can still answer in plain text.
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
            kwargs["tool_choice"] = "auto"

        last_exc: Optional[BaseException] = None
        attempts = 1 + self.max_retries

        for attempt in range(1, attempts + 1):
            try:
                log.debug(
                    "LLM chat attempt %d/%d: %d messages, tools=%s, model=%s",
                    attempt,
                    attempts,
                    len(messages),
                    bool(kwargs.get("tools")),
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

            except Exception as e:
                last_exc = e
                retryable = _is_retryable(e)
                log.warning(
                    "LLM chat failed (attempt %d/%d, retryable=%s): %s",
                    attempt,
                    attempts,
                    retryable,
                    e,
                )
                if not retryable or attempt >= attempts:
                    break
                await asyncio.sleep(self.retry_backoff_sec * attempt)

        # Last-ditch: if tools were enabled and the model kept failing to parse,
        # try once more with no tools so the user still gets a text reply.
        if tools and last_exc is not None:
            msg = str(last_exc).lower()
            if "output_parse_failed" in msg or "tool_use_failed" in msg:
                log.warning("Retrying final completion without tools after parse failure")
                try:
                    plain = dict(kwargs)
                    plain.pop("tools", None)
                    plain.pop("tool_choice", None)
                    response = await self._client.chat.completions.create(**plain)
                    content = (response.choices[0].message.content or "").strip()
                    return ChatResult(
                        content=content
                        or (
                            "(model failed to format a tool call; "
                            "try a narrower question or type reset)"
                        )
                    )
                except Exception as e2:
                    last_exc = e2

        assert last_exc is not None
        raise last_exc

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """Stream a plain text completion (no tools). Uses the same timeout."""
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
