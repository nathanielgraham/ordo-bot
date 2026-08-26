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
from typing import Any, Dict, List

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
                "Use for product knowledge (overview, jobs-and-clusters, mcp, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": (
                            "Doc section, e.g. overview, quickstart, "
                            "jobs-and-clusters, mcp"
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
                "(e.g. /root, /root/Monitoring, Monitoring). "
                "Returns matching cluster records."
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
            "name": "read_cluster",
            "description": (
                "Read full details for one cluster by numeric id, including "
                "its jobs (name, id, jobstate, server). Prefer this after "
                "find_cluster when you need job lists or full state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Cluster id (from find_cluster)",
                    },
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_job",
            "description": (
                "Read full details for one job by numeric id "
                "(script, server, jobstate, timings, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Job id",
                    },
                },
                "required": ["id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_monitor",
            "description": (
                "List servers / monitors registered with Ordo "
                "(id, name, host, resource stats)."
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
            "name": "find_cal",
            "description": (
                "List calendars (schedules) in the org, including cron expressions."
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
    {
        "type": "function",
        "function": {
            "name": "start_cluster",
            "description": (
                "Start a cluster by numeric id so its jobs begin running. "
                "This is a WRITE action — only call when the user clearly "
                "asks to start or run the cluster."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Cluster id to start",
                    },
                },
                "required": ["id"],
            },
        },
    },
]


def _trim_result(data: Any, max_chars: int = 6000) -> str:
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

        if name == "read_cluster":
            cid = int(arguments["id"])
            reply = await ordo.read_cluster(cid)
            return _trim_result(reply)

        if name == "read_job":
            jid = int(arguments["id"])
            reply = await ordo.read_job(jid)
            return _trim_result(reply)

        if name == "find_monitor":
            reply = await ordo.send_command({"command": "find_monitor"})
            return _trim_result(reply)

        if name == "find_cal":
            reply = await ordo.send_command({"command": "find_cal"})
            return _trim_result(reply)

        if name == "read_org":
            reply = await ordo.send_command({"command": "read_org"})
            return _trim_result(reply)

        if name == "start_cluster":
            cid = int(arguments["id"])
            reply = await ordo.start_cluster(cid)
            return _trim_result(reply)

        return json.dumps({"error": f"Unknown tool: {name}"})

    except Exception as e:
        log.exception("Tool %s failed", name)
        return json.dumps({"error": str(e)})
