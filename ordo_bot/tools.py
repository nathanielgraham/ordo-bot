"""
Ordo tools the agent can call (WebSocket command surface).

Schemas follow the live Ordo API. Write tools are marked clearly so the
model only uses them when the user asks.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from ordo_bot.ordo_client import OrdoClient

log = logging.getLogger("ordo_bot.tools")


def _fn(name: str, description: str, properties: Dict[str, Any], required: List[str] | None = None) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


_ID = {"type": "integer", "description": "Numeric id"}
_NAME = {"type": "string", "description": "Name or path"}

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    _fn(
        "get_documentation",
        "Fetch Ordo documentation (overview, api, jobs-and-clusters, mcp/connecting-to-ai, etc.).",
        {"section": {"type": "string", "description": "Doc section name"}},
        ["section"],
    ),
    _fn("read_org", "Org / account info for the logged-in user.", {}),
    _fn("read_user", "Current user profile (name, email, org, level).", {}),
    _fn(
        "find_cluster",
        "Look up clusters by path or name (e.g. /root, Monitoring).",
        {"name": _NAME},
        ["name"],
    ),
    _fn(
        "read_cluster",
        "Full cluster detail including nested jobs (by id).",
        {"id": _ID},
        ["id"],
    ),
    _fn("read_job", "Full job detail by id (script, server, state, timings).", {"id": _ID}, ["id"]),
    _fn("find_monitor", "List servers/monitors with live resource metrics.", {}),
    _fn("find_cal", "List calendars, cron expressions, attached clusters.", {}),
    _fn(
        "read_cal",
        "Read one calendar by id or name (includes crons).",
        {"id": _ID, "name": _NAME},
    ),
    _fn(
        "find_log",
        "List past run log entries for a job id (timestamps, exit codes).",
        {"id": {"type": "integer", "description": "Job id"}},
        ["id"],
    ),
    _fn(
        "read_log",
        "Read log output for a job (stdout/stderr, exit code). Pass job id.",
        {"id": {"type": "integer", "description": "Job id"}},
        ["id"],
    ),
    _fn("sync", "Reconcile scheduler state with actual processes on servers.", {}),
    _fn(
        "start_cluster",
        "WRITE: start a cluster by id so its jobs run. Only when user asks to start/run.",
        {"id": _ID},
        ["id"],
    ),
    _fn(
        "start_job",
        "WRITE: start a single job by id. Only when user asks.",
        {"id": _ID},
        ["id"],
    ),
    _fn("kill_cluster", "WRITE: kill a running cluster by id.", {"id": _ID}, ["id"]),
    _fn("kill_job", "WRITE: kill a running job by id.", {"id": _ID}, ["id"]),
    _fn("ice_cluster", "WRITE: ice (strong hold) a cluster by id.", {"id": _ID}, ["id"]),
    _fn("ice_job", "WRITE: ice (strong hold) a job by id.", {"id": _ID}, ["id"]),
    _fn("hold_cluster", "WRITE: hold a cluster by id.", {"id": _ID}, ["id"]),
    _fn("hold_job", "WRITE: hold a job by id.", {"id": _ID}, ["id"]),
    _fn("release_cluster", "WRITE: release a held cluster by id.", {"id": _ID}, ["id"]),
    _fn("release_job", "WRITE: release a held job by id.", {"id": _ID}, ["id"]),
    _fn("melt_cluster", "WRITE: melt (un-ice) a cluster by id.", {"id": _ID}, ["id"]),
    _fn("melt_job", "WRITE: melt (un-ice) a job by id.", {"id": _ID}, ["id"]),
    _fn("complete_cluster", "WRITE: mark a cluster complete by id.", {"id": _ID}, ["id"]),
    _fn("complete_job", "WRITE: mark a job complete by id.", {"id": _ID}, ["id"]),
    _fn("reset_cluster", "WRITE: reset a cluster to a clean startable state.", {"id": _ID}, ["id"]),
    _fn(
        "clone_cluster",
        "WRITE: clone a cluster (copy only; does not start). Only when user asks.",
        {"id": _ID},
        ["id"],
    ),
    _fn(
        "create_cluster",
        "WRITE: create a cluster. Requires name. Optional parent_id (default root).",
        {
            "name": {"type": "string"},
            "parent_id": {"type": "integer", "description": "Parent cluster id"},
            "description": {"type": "string"},
            "cal_id": {"type": "integer", "description": "Calendar id to attach"},
        },
        ["name"],
    ),
    _fn(
        "create_job",
        "WRITE: create a job in a cluster. Requires name, parent_id, script, server_id.",
        {
            "name": {"type": "string"},
            "parent_id": {"type": "integer", "description": "Owning cluster id"},
            "script": {"type": "string", "description": "Job script text (with shebang)"},
            "server_id": {"type": "integer", "description": "Server id from find_monitor"},
            "description": {"type": "string"},
            "retrys": {"type": "integer"},
            "delay": {"type": "integer"},
        },
        ["name", "parent_id", "script", "server_id"],
    ),
    _fn(
        "create_cal",
        "WRITE: create a calendar. Requires name and IANA tz (e.g. America/Chicago).",
        {
            "name": {"type": "string"},
            "tz": {"type": "string", "description": "IANA timezone"},
            "description": {"type": "string"},
        },
        ["name", "tz"],
    ),
    _fn(
        "create_cron",
        "WRITE: add a Quartz cron expression to a calendar. "
        "name is the cron string (e.g. '0 0 8 * * ? *'); cal_id is the calendar id. "
        "(WebSocket command is create_cron; MCP may call this add_cron.)",
        {
            "name": {
                "type": "string",
                "description": "Quartz cron expression string",
            },
            "cal_id": {"type": "integer", "description": "Calendar id"},
        },
        ["name", "cal_id"],
    ),
    _fn(
        "update_cluster",
        "WRITE: update cluster fields (description, cal_id, handlers, etc.). Only id required; omit fields to leave unchanged.",
        {
            "id": _ID,
            "newname": {"type": "string"},
            "description": {"type": "string"},
            "cal_id": {"type": "integer"},
            "auto_start": {"type": "boolean"},
        },
        ["id"],
    ),
    _fn(
        "update_job_config",
        "WRITE: update job config (script, server, retries, etc.). WebSocket name is update_job_config.",
        {
            "id": _ID,
            "newname": {"type": "string"},
            "script": {"type": "string"},
            "server_id": {"type": "integer"},
            "description": {"type": "string"},
            "retrys": {"type": "integer"},
            "delay": {"type": "integer"},
        },
        ["id"],
    ),
    _fn("delete_job", "WRITE: delete a job by id. Only when user explicitly asks.", {"id": _ID}, ["id"]),
    _fn(
        "delete_cluster",
        "WRITE: delete a cluster by id. Only when user explicitly asks.",
        {"id": _ID},
        ["id"],
    ),
    _fn("delete_cron", "WRITE: delete a cron entry by id.", {"id": _ID}, ["id"]),
    _fn("delete_cal", "WRITE: delete a calendar by id.", {"id": _ID}, ["id"]),
]


def _trim_result(data: Any, max_chars: int = 8000) -> str:
    text = json.dumps(data, default=str)
    if len(text) > max_chars:
        return text[:max_chars] + f"... [truncated, {len(text)} chars total]"
    return text


_PASSTHROUGH = {
    "get_documentation",
    "read_org",
    "read_user",
    "find_cluster",
    "read_cluster",
    "read_job",
    "find_monitor",
    "find_cal",
    "read_cal",
    "find_log",
    "read_log",
    "sync",
    "start_cluster",
    "start_job",
    "kill_cluster",
    "kill_job",
    "ice_cluster",
    "ice_job",
    "hold_cluster",
    "hold_job",
    "release_cluster",
    "release_job",
    "melt_cluster",
    "melt_job",
    "complete_cluster",
    "complete_job",
    "reset_cluster",
    "clone_cluster",
    "create_cluster",
    "create_job",
    "create_cal",
    "create_cron",
    "update_cluster",
    "update_job_config",
    "delete_job",
    "delete_cluster",
    "delete_cron",
    "delete_cal",
}


async def run_tool(ordo: OrdoClient, name: str, arguments: Dict[str, Any]) -> str:
    """Execute one tool; return JSON string for the model."""
    log.info("Tool call: %s(%s)", name, arguments)
    try:
        if name not in _PASSTHROUGH:
            return json.dumps({"error": f"Unknown tool: {name}"})

        cmd: Dict[str, Any] = {"command": name}
        for k, v in (arguments or {}).items():
            if v is not None:
                cmd[k] = v

        if name == "get_documentation":
            cmd.setdefault("format", "markdown")
            cmd.setdefault("section", "overview")

        reply = await ordo.send_command(cmd)
        return _trim_result(reply)
    except Exception as e:
        log.exception("Tool %s failed", name)
        return json.dumps({"error": str(e)})
