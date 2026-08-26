"""
Ordo tools the agent can call.

Each tool is:
  - a JSON schema (for the LLM / OpenAI tools API)
  - an async function that runs the real Ordo command

The agent loop in agent.py decides when to call these.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ordo_bot.ordo_client import OrdoClient

log = logging.getLogger("ordo_bot.tools")

# ---------------------------------------------------------------------------
# Tool schemas (OpenAI-compatible function definitions)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_documentation",
            "description": (
                "Fetch Ordo documentation for a section. "
                "Use this when you need accurate product knowledge "
                "(overview, jobs-and-clusters, api, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": (
                            "Doc section name, e.g. overview, quickstart, "
                            "jobs-and-clusters, agent-protocol, api"
                        ),
                    },
                },
                "required": ["section"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_cluster",
            "description": (
                "Look up a cluster by path or name "
                "(e.g. /root, /root/Monitoring, Monitoring)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Cluster path or name",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_monitor",
            "description": (
                "List servers / monitors registered with Ordo "
                "(id, name, host, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_org",
            "description": "Read basic org / account info for the logged-in user.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


def _trim_result(data: Any, max_chars: int = 4000) -> str:
    """Serialize a tool result to JSON, truncating if huge."""
    text = json.dumps(data, default=str)
    if len(text) > max_chars:
        return text[:max_chars] + f"... [truncated, {len(text)} chars total]"
    return text


async def run_tool(
    ordo: OrdoClient,
    name: str,
    arguments: Dict[str, Any],
) -> str:
    """
    Execute one tool by name and return a string result for the LLM.
    """
    log.info("Tool call: %s(%s)", name, arguments)

    try:
        if name == "get_documentation":
            section = arguments.get("section") or "overview"
            reply = await ordo.get_documentation(section=section, format="markdown")
            return _trim_result(reply)

        if name == "find_cluster":
            cluster_name = arguments.get("name") or "/root"
            reply = await ordo.find_cluster(cluster_name)
            return _trim_result(reply)

        if name == "find_monitor":
            reply = await ordo.send_command({"command": "find_monitor"})
            return _trim_result(reply)

        if name == "read_org":
            reply = await ordo.send_command({"command": "read_org"})
            return _trim_result(reply)

        return json.dumps({"error": f"Unknown tool: {name}"})

    except Exception as e:
        log.exception("Tool %s failed", name)
        return json.dumps({"error": str(e)})
