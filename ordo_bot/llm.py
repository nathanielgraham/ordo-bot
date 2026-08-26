"""
LLM wrapper for ordo-bot.

OpenAI-compatible chat + tools (Ollama, Groq, OpenRouter, xAI, …).

Robustness principles (provider-agnostic):
  1. Timeouts and limited retries on transient errors (429/5xx/timeout).
  2. Never strip tools mid-turn as a "fix" — many models still emit tool
     calls and APIs then reject with "tool choice is none".
  3. If the HTTP API rejects a completion but the error payload still carries
     a tool-call-shaped JSON blob, recover it and let the agent run the tool.
  4. If the model puts a tool call in plain text content, parse it out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

log = logging.getLogger("ordo_bot.llm")

DEFAULT_LLM_TIMEOUT_SEC = 90.0
DEFAULT_LLM_RETRIES = 2
DEFAULT_LLM_RETRY_BACKOFF_SEC = 1.5


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Generic extraction of tool-call intent from messy text / error bodies
# ---------------------------------------------------------------------------

def _new_call_id() -> str:
    return f"recovered_{uuid.uuid4().hex[:12]}"


def _args_to_json(arguments: Any) -> str:
    if isinstance(arguments, dict):
        return json.dumps(arguments)
    if isinstance(arguments, str):
        # already JSON or plain string
        try:
            json.loads(arguments)
            return arguments
        except json.JSONDecodeError:
            return json.dumps({"value": arguments})
    return "{}"


def tool_calls_from_obj(data: Any) -> List[ToolCall]:
    """
    Accept common shapes from any provider / model:
      {"name": "fn", "arguments": {...}}
      {"name": "fn", "parameters": {...}}
      {"tool_calls": [{"function": {"name", "arguments"}}]}
      {"type": "function", "function": {...}}
      [ {...}, ... ]
    """
    calls: List[ToolCall] = []

    def add(name: Any, arguments: Any, call_id: Optional[str] = None) -> None:
        if not isinstance(name, str) or not name.strip():
            return
        calls.append(
            ToolCall(
                id=call_id or _new_call_id(),
                name=name.strip(),
                arguments=_args_to_json(arguments),
            )
        )

    if isinstance(data, list):
        for item in data:
            calls.extend(tool_calls_from_obj(item))
        return calls

    if not isinstance(data, dict):
        return calls

    if "tool_calls" in data and isinstance(data["tool_calls"], list):
        for tc in data["tool_calls"]:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
            add(fn.get("name"), fn.get("arguments", fn.get("parameters")), tc.get("id"))
        return calls

    if data.get("type") == "function" and isinstance(data.get("function"), dict):
        fn = data["function"]
        add(fn.get("name"), fn.get("arguments", fn.get("parameters")), data.get("id"))
        return calls

    if "name" in data and ("arguments" in data or "parameters" in data or len(data) <= 3):
        add(
            data.get("name"),
            data.get("arguments", data.get("parameters", {})),
            data.get("id"),
        )
        return calls

    return calls


def _extract_json_blobs(text: str) -> List[Any]:
    """Pull balanced {...} or [...] JSON values out of an arbitrary string."""
    blobs: List[Any] = []
    if not text:
        return blobs

    # Fast path: whole string is JSON
    try:
        blobs.append(json.loads(text))
        return blobs
    except json.JSONDecodeError:
        pass

    # Scan for objects / arrays
    for opener, closer in (("{", "}"), ("[", "]")):
        start = None
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(text):
            if start is None:
                if ch == opener:
                    start = i
                    depth = 1
                    in_str = False
                    esc = False
                continue
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0 and start is not None:
                    chunk = text[start : i + 1]
                    try:
                        blobs.append(json.loads(chunk))
                    except json.JSONDecodeError:
                        pass
                    start = None
    return blobs


def recover_tool_calls_from_text(text: str) -> List[ToolCall]:
    """Provider-agnostic: find tool-call-shaped JSON in free text."""
    found: List[ToolCall] = []
    for blob in _extract_json_blobs(text):
        found.extend(tool_calls_from_obj(blob))
    return found


def _error_payload_text(exc: BaseException) -> str:
    """Flatten exception body + message into searchable text."""
    parts: List[str] = [str(exc)]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        try:
            parts.append(json.dumps(body))
        except (TypeError, ValueError):
            parts.append(str(body))
        err = body.get("error")
        if isinstance(err, dict):
            for key in ("failed_generation", "message", "code"):
                val = err.get(key)
                if isinstance(val, str):
                    parts.append(val)
    return "\n".join(parts)


def recover_tool_calls_from_exception(exc: BaseException) -> List[ToolCall]:
    """
    If a provider rejects the HTTP response but still echoes the model's
    intended tool call somewhere in the error, recover it.

    Works for Groq failed_generation and any API that embeds similar JSON.
    """
    return recover_tool_calls_from_text(_error_payload_text(exc))


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError, asyncio.TimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        if exc.status_code in {408, 429, 500, 502, 503, 504}:
            return True
        msg = str(exc).lower()
        if "rate_limit" in msg or "tokens per minute" in msg:
            return True
        # Parse failures: retry once; recovery path runs before retry decision
        if "output_parse" in msg or "parse" in msg and "tool" in msg:
            return True
    msg = str(exc).lower()
    if "rate_limit" in msg or "timeout" in msg:
        return True
    return False


def _retry_after_seconds(exc: BaseException, default: float) -> float:
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

    def _result_from_message(self, message: Any) -> ChatResult:
        content = (message.content or "").strip()
        result = ChatResult(content=content)

        if message.tool_calls:
            for tc in message.tool_calls:
                result.tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=tc.function.arguments or "{}",
                    )
                )

        # Some models put tool intent only in text (no native tool_calls field)
        if not result.tool_calls and content:
            recovered = recover_tool_calls_from_text(content)
            if recovered:
                log.info(
                    "Parsed %d tool call(s) from assistant content text",
                    len(recovered),
                )
                result.tool_calls = recovered
                # Avoid showing raw JSON as the user-visible answer
                result.content = ""

        return result

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
                return self._result_from_message(response.choices[0].message)

            except Exception as e:
                last_exc = e

                # Generic recovery: error body still contains tool-call JSON
                recovered = recover_tool_calls_from_exception(e)
                if recovered:
                    log.warning(
                        "Recovered %d tool call(s) from API error payload",
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
