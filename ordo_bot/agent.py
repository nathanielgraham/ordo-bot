"""
Agent brain for ordo-bot.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ordo_bot.llm import LLM
from ordo_bot.ordo_client import OrdoClient
from ordo_bot.tools import (
    DEFAULT_TOOL_RESULT_MAX_CHARS,
    LOCAL_TOOL_NAMES,
    run_tool,
    tools_for_turn,
    user_wants_write,
)
from ordo_bot.watches import WatchRegistry

log = logging.getLogger("ordo_bot.agent")

DEFAULT_SYSTEM_PROMPT = """\
You are ordo-bot, an assistant for the Ordo job scheduler.
You use tools against a live Ordo instance. Follow any bootstrapped playbook.
Do not invent ids or states. Prefer compact summaries over raw dumps.

When the user asks to be notified when something finishes, after start_cluster
or start_job call watch_cluster or watch_job with the numeric id. Completion
notifications arrive later via Ordo WebSocket broadcasts (not by polling).
"""

MAX_TOOL_ROUNDS = 5
DEFAULT_MAX_HISTORY_MESSAGES = 24
DEFAULT_CHAT_TIMEOUT_SEC = 120.0


def _default_playbook_path() -> Path:
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "prompts" / "bootstrap.md",
        Path.cwd() / "prompts" / "bootstrap.md",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def _read_text_file(path: Path, *, label: str) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return None
        return text
    except OSError as e:
        log.warning("Could not read %s (%s): %s", label, path, e)
        return None


class Agent:
    def __init__(
        self,
        llm: LLM,
        *,
        ordo: Optional[OrdoClient] = None,
        watches: Optional[WatchRegistry] = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_history_messages: int = DEFAULT_MAX_HISTORY_MESSAGES,
        tool_result_max_chars: int = DEFAULT_TOOL_RESULT_MAX_CHARS,
        bootstrap_mode: str = "standard",
        bootstrap_docs: bool = True,
        bootstrap_playbook_path: Optional[Path] = None,
        bootstrap_extra_md: Optional[Path] = None,
        chat_timeout_sec: float = DEFAULT_CHAT_TIMEOUT_SEC,
    ) -> None:
        self.llm = llm
        self.ordo = ordo
        self.watches = watches or WatchRegistry()
        self.system_prompt = system_prompt
        self.max_history_messages = max_history_messages
        self.tool_result_max_chars = tool_result_max_chars
        mode = (bootstrap_mode or "standard").strip().lower()
        if mode not in {"minimal", "standard", "rich"}:
            mode = "standard"
        self.bootstrap_mode = mode
        self.bootstrap_docs = bool(bootstrap_docs) and mode != "minimal"
        self.bootstrap_playbook_path = bootstrap_playbook_path or _default_playbook_path()
        self.bootstrap_extra_md = bootstrap_extra_md
        self.chat_timeout_sec = chat_timeout_sec
        self._bootstrapped = False
        self._current_client: Any = None
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

    def _inject_system(self, title: str, body: str) -> None:
        self.messages.append(
            {"role": "system", "content": f"{title}\n\n{body}"}
        )

    async def bootstrap(self) -> None:
        if self._bootstrapped:
            return

        if self.bootstrap_mode in {"standard", "rich"}:
            playbook = _read_text_file(
                Path(self.bootstrap_playbook_path), label="bootstrap playbook"
            )
            if playbook:
                self._inject_system(
                    "ordo-bot playbook (fixed; follow unless the user overrides):",
                    playbook,
                )

        if self.bootstrap_mode == "rich" and self.bootstrap_extra_md:
            extra = _read_text_file(
                Path(self.bootstrap_extra_md), label="bootstrap_extra_md"
            )
            if extra:
                self._inject_system("Project-specific guidance:", extra)

        if self.bootstrap_docs and self.ordo and self.ordo.is_logged_in:
            try:
                summary = await run_tool(
                    self.ordo,
                    "get_documentation",
                    {"section": "api", "format": "summary"},
                    max_chars=self.tool_result_max_chars,
                )
                self._inject_system(
                    "Ordo API documentation summary (live):",
                    summary,
                )
            except Exception as e:
                log.warning("Bootstrap live docs failed: %s", e)

        self._trim_history()
        self._bootstrapped = True

    async def handle_chat(
        self, user_text: str, *, client: Any = None
    ) -> str:
        user_text = user_text.strip()
        if not user_text:
            return ""

        self._current_client = client
        timeout = self.chat_timeout_sec
        try:
            if timeout and timeout > 0:
                try:
                    return await asyncio.wait_for(
                        self._handle_chat_inner(user_text),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    log.error("Chat timed out after %ss", timeout)
                    if self.messages and self.messages[-1].get("role") == "user":
                        if self.messages[-1].get("content") == user_text:
                            self.messages.pop()
                    return (
                        f"(timed out after {int(timeout)}s — try a narrower "
                        "question, or type reset)"
                    )
            return await self._handle_chat_inner(user_text)
        finally:
            self._current_client = None

    def _run_local_tool(self, name: str, args: Dict[str, Any]) -> str:
        if name in {"watch_cluster", "watch_job"}:
            kind = "cluster" if name == "watch_cluster" else "job"
            try:
                oid = int(args.get("id"))
            except (TypeError, ValueError):
                return json.dumps({"error": "id must be an integer"})
            result = self.watches.add(
                kind=kind,
                id=oid,
                client=self._current_client,
                label=str(args.get("label") or ""),
            )
            return json.dumps(result)
        return json.dumps({"error": f"Unknown local tool: {name}"})

    def _finalize_from_tools(self) -> str:
        chunks: List[str] = []
        for msg in reversed(self.messages):
            if msg.get("role") != "tool":
                if msg.get("role") == "user":
                    break
                continue
            content = msg.get("content") or ""
            if content:
                chunks.append(content[:800])
            if len(chunks) >= 3:
                break
        chunks.reverse()
        if not chunks:
            return "(stopped after too many tool calls)"
        return "(reached tool-call limit; latest tool data)\n" + "\n---\n".join(
            chunks
        )

    async def _handle_chat_inner(self, user_text: str) -> str:
        if not self._bootstrapped:
            await self.bootstrap()

        self.messages.append({"role": "user", "content": user_text})

        # Notify/watch counts as needing write tools available (start+watch)
        allow_write = user_wants_write(user_text) or any(
            w in user_text.lower()
            for w in ("notify", "when it", "when finished", "when complete", "let me know")
        )
        tools = None
        if self.ordo and self.ordo.is_logged_in:
            tools = tools_for_turn(allow_write=allow_write)

        try:
            for round_num in range(MAX_TOOL_ROUNDS):
                result = await self.llm.chat(self.messages, tools=tools)

                if not result.tool_calls:
                    reply = result.content or "(no response)"
                    self.messages.append({"role": "assistant", "content": reply})
                    self._trim_history()
                    return reply

                self.messages.append(
                    {
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
                )

                for tc in result.tool_calls:
                    args = self._parse_args(tc.arguments)
                    if tc.name in LOCAL_TOOL_NAMES:
                        tool_result = self._run_local_tool(tc.name, args)
                    elif (
                        not allow_write
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
                                    "Confirm a change first."
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

            reply = self._finalize_from_tools()
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
            return {}

    def _trim_history(self) -> None:
        cap = self.max_history_messages
        if cap is None or cap <= 0:
            return
        system = [m for m in self.messages if m.get("role") == "system"]
        rest = [m for m in self.messages if m.get("role") != "system"]
        if len(rest) <= cap:
            return
        rest = rest[-cap:]
        while rest and rest[0].get("role") == "tool":
            rest.pop(0)
        self.messages = system + rest

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self._bootstrapped = False
        log.info("Conversation history cleared")
