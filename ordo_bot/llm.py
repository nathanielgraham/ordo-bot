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
  5. Rate limits: short retries only. Do not sleep for multi-minute "try again"
     windows (daily caps) — that looks like a client hang. Raise so the UI
     can show the error.
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
# Never sleep longer than this between retries (avoids "hang" on TPM/TPD messages).
MAX_RETRY_SLEEP_SEC = 15.0


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)


def _new_call_id() -> str:
    return f"recovered_{uuid.uuid4().hex[:12]}"


def _args_to_json(arguments: Any) -> str:
    if isinstance(arguments, dict):
        return json.dumps(arguments)
    if isinstance(arguments, str):
        try:
            json.loads(arguments)
            return arguments
        except json.JSONDecodeError:
            return json.dumps({"value": arguments})
    return "{}"


def tool_calls_from_obj(data: Any) -> List[ToolCall]:
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
    blobs: List[Any] = []
    if not text:
        return blobs

    try:
        blobs.append(json.loads(text))
        return blobs
    except json.JSONDecodeError:
        pass

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
    found: List[ToolCall] = []
    for blob in _extract_json_blobs(text):
        found.extend(tool_calls_from_obj(blob))
    return found


def _error_payload_text(exc: BaseException) -> str:
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
    return recover_tool_calls_from_text(_error_payload_text(exc))


def _is_daily_or_quota_exhausted(exc: BaseException) -> bool:
    """
    Limits that will not clear in a few seconds — do not retry / long-sleep.
    """
    text = _error_payload_text(exc).lower()
    markers = (
        "tokens per day",
        "tpd",
        "daily",
        "quota",
        "billing",
        "insufficient_quota",
        "exceeded your current quota",
    )
    return any(m in text for m in markers)


def _is_retryable(exc: BaseException) -> bool:
    if _is_daily_or_quota_exhausted(exc):
        return False
    if isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError, asyncio.TimeoutError)):
        # RateLimitError may still be short TPM — allow limited retries unless daily
        if isinstance(exc, RateLimitError) and _is_daily_or_quota_exhausted(exc):
            return False
        return True
    if isinstance(exc, APIStatusError):
        if exc.status_code in {408, 429, 500, 502, 503, 504}:
            if exc.status_code == 429 and _is_daily_or_quota_exhausted(exc):
                return False
            return True
        msg = str(exc).lower()
        if "rate_limit" in msg or "tokens per minute" in msg:
            return not _is_daily_or_quota_exhausted(exc)
        if "output_parse" in msg or ("parse" in msg and "tool" in msg):
            return True
    msg = str(exc).lower()
    if "rate_limit" in msg or "timeout" in msg:
        return not _is_daily_or_quota_exhausted(exc)
    return False


def _retry_after_seconds(exc: BaseException, default: float) -> float:
    """
    Honor provider 'try again in Xs' but never sleep longer than MAX_RETRY_SLEEP_SEC.
    Multi-minute waits make the CLI/web UI look hung.
    """
    text = str(exc)
    m = re.search(r"try again in ([0-9.]+)\s*s", text, re.I)
    if m:
        try:
            sec = float(m.group(1))
            return max(0.5, min(sec, MAX_RETRY_SLEEP_SEC))
        except ValueError:
            pass
    # "try again in 7m53s" style
    m = re.search(r"try again in ([0-9]+)\s*m(?:in(?:ute)?s?)?(?:\s*([0-9.]+)\s*s)?", text, re.I)
    if m:
        try:
            mins = int(m.group(1))
            secs = float(m.group(2) or 0)
            total = mins * 60 + secs
            # Cap — caller should usually not retry daily limits at all
            return max(0.5, min(total, MAX_RETRY_SLEEP_SEC))
        except ValueError:
            pass
    return min(default, MAX_RETRY_SLEEP_SEC)


def format_llm_error(exc: BaseException) -> str:
    """Short user-facing string for rate limits and other LLM failures."""
    text = _error_payload_text(exc)
    if _is_daily_or_quota_exhausted(exc) or "rate_limit" in text.lower() or "429" in text:
        # Prefer provider message if present
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                return f"LLM rate limit: {err['message']}"
        return f"LLM rate limit: {exc}"
    return str(exc)


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

        if not result.tool_calls and content:
            recovered = recover_tool_calls_from_text(content)
            if recovered:
                log.info(
                    "Parsed %d tool call(s) from assistant content text",
                    len(recovered),
                )
                result.tool_calls = recovered
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
                log.info("Retrying LLM chat in %.1fs", delay)
                await asyncio.sleep(delay)

        assert last_exc is not None
        # Re-raise with a clearer message for the agent/UI
        raise RuntimeError(format_llm_error(last_exc)) from last_exc

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
