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
You use tools against a live Ordo instance. Prefer compact summaries.
Do not invent ids or states.

Start vs done:
- start_cluster / start_job success is an ACK, not completion.
- Do not treat command_reply as \"finished.\"

Broadcast watches (WebSocket only — never poll):
- After start_*, if the user wants to know when work is done, call
  watch_cluster (cluster id) or watch_job (job id).
- Default watch matches ANY terminal state: complete, failed, zombie
  (state_id 5 also counts as complete). Pass jobstate only to require
  one outcome (e.g. jobstate=\"complete\").
- watch_cluster waits for the CLUSTER row. A child job going complete
  does not finish the cluster (prep != Bork da Cake).
- Example: watch_cluster(id=18, label=\"Bork da Cake\")
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
        return text or None
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
                Path(self.bootstrap_playbook_path), label="playbook"
            )
            if playbook:
                self._inject_system(
                    "ordo-bot playbook (fixed; follow unless the user overrides):",
                    playbook,
                )

        if self.bootstrap_mode == "rich" and self.bootstrap_extra_md:
            extra = _read_text_file(
                Path(self.bootstrap_extra_md), label="extra"
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
        return json.dumps({"error": f"{name} must be run via _run_local_tool_async"})

    async def _run_local_tool_async(self, name: str, args: Dict[str, Any]) -> str:
        if name == "watch_event":
            event = str(args.get("event") or "*").strip()
            filt = args.get("filter") or {}
            if not isinstance(filt, dict):
                filt = {}
            for k in ("id", "name", "jobstate"):
                if k in args and args[k] is not None and k not in filt:
                    filt[k] = args[k]
            once = args.get("once", True)
            if isinstance(once, str):
                once = once.lower() not in {"false", "0", "no"}
            result = self.watches.add_event(
                event=event,
                filter=filt,
                label=str(args.get("label") or ""),
                once=bool(once),
                client=self._current_client,
            )
            return json.dumps(result)

        if name in {"watch_cluster", "watch_job"}:
            kind = "cluster" if name == "watch_cluster" else "job"
            try:
                oid = int(args.get("id"))
            except (TypeError, ValueError):
                return json.dumps({"error": "id must be an integer"})
            result = self.watches.add_kind(
                kind=kind,
                id=oid,
                label=str(args.get("label") or ""),
                client=self._current_client,
                jobstate=args.get("jobstate"),
            )
            result = await self._snapshot_watch(kind, oid, result)
            return json.dumps(result)

        return json.dumps({"error": f"Unknown local tool: {name}"})

    async def _snapshot_watch(
        self, kind: str, oid: int, result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Resolve immediately if the target is already in the watched state."""
        if not self.ordo or not self.ordo.is_logged_in:
            return result
        try:
            if kind == "cluster":
                data = await self.ordo.read_cluster(oid)
            else:
                data = await self.ordo.read_job(oid)
        except Exception as e:
            log.warning("Watch snapshot %s %s failed: %s", kind, oid, e)
            result["snapshot_error"] = str(e)
            return result

        if not isinstance(data, dict):
            return result
        fired = self.watches.match_snapshot(data)
        if not fired:
            result["snapshot"] = {
                "id": data.get("id"),
                "name": data.get("name"),
                "jobstate": data.get("jobstate"),
                "state_id": data.get("state_id"),
                "matched": False,
            }
            return result

        obj = fired[0].get("object") or data
        result["already_terminal"] = True
        result["source"] = "snapshot"
        result["snapshot"] = {
            "id": obj.get("id"),
            "name": obj.get("name"),
            "jobstate": obj.get("jobstate"),
            "state_id": obj.get("state_id"),
            "started": obj.get("started"),
            "ended": obj.get("ended"),
            "exit_code": obj.get("exit_code"),
            "matched": True,
        }
        result["message"] = (
            f"{kind} {oid} already "
            f"{obj.get('jobstate') or 'terminal'}; "
            "watch completed from snapshot (no broadcast wait)."
        )
        return result

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

        allow_write = user_wants_write(user_text) or any(
            w in user_text.lower()
            for w in (
                "notify",
                "when it",
                "when finished",
                "when complete",
                "let me know",
                "watch",
            )
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
                        tool_result = await self._run_local_tool_async(tc.name, args)
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
        if not cap or cap <= 0:
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
