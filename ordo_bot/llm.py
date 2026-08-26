"""
LLM wrapper for ordo-bot.

Talks to any OpenAI-compatible endpoint (Ollama, Groq, OpenRouter, xAI, …)
using the official `openai` Python SDK.

Includes request timeout, light retries, and recovery for Groq tool_use_failed
(where the model *did* produce a tool call but the API rejected the frame).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

log = logging.getLogger("ordo_bot.llm")

DEFAULT_LLM_TIMEOUT_SEC = 90.0
DEFAULT_LLM_RETRIES = 2
DEFAULT_LLM_RETRY_BACKOFF_SEC = 1.5


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string from the model


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)


def _error_body(exc: BaseException) -> Dict[str, Any]:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return body
    # openai SDK sometimes only puts detail in the message string
    return {}


def _error_code(exc: BaseException) -> Optional[str]:
    err = _error_body(exc).get("error")
    if isinstance(err, dict):
        return err.get("code")
    return None


def _failed_generation(exc: BaseException) -> Optional[str]:
    err = _error_body(exc).get("error")
    if isinstance(err, dict):
        fg = err.get("failed_generation")
        if isinstance(fg, str) and fg.strip():
            return fg.strip()
    # Fallback: scrape from stringified exception
    m = re.search(r"'failed_generation':\s*'((?:\\'|[^'])*)'", str(exc))
    if m:
        return m.group(1).encode().decode("unicode_escape")
    m = re.search(r'"failed_generation":\s*"((?:\\"|[^"])*)"', str(exc))
    if m:
        return m.group(1).encode().decode("unicode_escape")
    return None


def _tool_calls_from_failed_generation(raw: str) -> List[ToolCall]:
    """
    Groq sometimes returns tool intent only inside failed_generation, e.g.
      {"name": "find_cluster", "arguments": {"path": "/root"}}
    or an OpenAI-ish tool_calls fragment.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []

    calls: List[ToolCall] = []

    def add(name: str, arguments: Any) -> None:
        if not name:
            return
        if isinstance(arguments, dict):
            args_s = json.dumps(arguments)
        elif isinstance(arguments, str):
            args_s = arguments
        else:
            args_s = "{}"
        calls.append(
            ToolCall(
                id=f"recovered_{uuid.uuid4().hex[:12]}",
                name=name,
                arguments=args_s,
            )
        )

    if isinstance(data, dict):
        if "name" in data and ("arguments" in data or "parameters" in data):
            add(data.get("name") or "", data.get("arguments", data.get("parameters")))
        elif "tool_calls" in data and isinstance(data["tool_calls"], list):
            for tc in data["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or tc
                if isinstance(fn, dict):
                    add(fn.get("name") or "", fn.get("arguments", {}))
        elif data.get("type") == "function" and isinstance(data.get("function"), dict):
            fn = data["function"]
            add(fn.get("name") or "", fn.get("arguments", {}))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "name" in item:
                add(item.get("name") or "", item.get("arguments", item.get("parameters")))

    return calls


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        if exc.status_code in {408, 429, 500, 502, 503, 504}:
            return True
        code = _error_code(exc)
        # tool_use_failed with recoverable generation is handled specially, not retried blindly
        if code == "output_parse_failed":
            return True
        msg = str(exc).lower()
        if "rate_limit" in msg or "tokens per minute" in msg:
            return True
    msg = str(exc).lower()
    if "rate_limit" in msg or "timeout" in msg:
        return True
    return False


def _retry_after_seconds(exc: BaseException, default: float) -> float:
    """Parse Groq 'Please try again in 8.25s' if present."""
    m = re.search(r"try again in ([0-9.]+)\s*s", str(exc), re.I)
    if m:
        try:
            return max(float(m.group(1)), 0.5)
        except ValueError:
            pass
    return default


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

        self._client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=api_key or "unused",
            timeout=timeout_sec,
            max_retries=0,
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

                # Groq: model called a tool but API rejected the frame — recover intent
                code = _error_code(e)
                fg = _failed_generation(e)
                if code == "tool_use_failed" or (
                    fg and "tool_use_failed" in str(e).lower()
                ):
                    if fg:
                        recovered = _tool_calls_from_failed_generation(fg)
                        if recovered:
                            log.warning(
                                "Recovered %d tool call(s) from failed_generation",
                                len(recovered),
                            )
                            return ChatResult(content="", tool_calls=recovered)

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
                delay = _retry_after_seconds(e, self.retry_backoff_sec * attempt)
                await asyncio.sleep(delay)

        # Do NOT retry with tools stripped when history expects tool calling:
        # gpt-oss on Groq often emits tools anyway → "Tool choice is none, but model called a tool".
        assert last_exc is not None
        raise last_exc

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
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
