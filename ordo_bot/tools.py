"""
Ordo tools the agent can call (WebSocket command surface).

Schemas follow the live Ordo API. Write tools are gated: the agent only
exposes them when the user (or co-agent) asks to change something.

Broadcasts from Ordo are never tool results and never enter chat history;
they are forwarded only to frontend clients.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set

from ordo_bot.ordo_client import OrdoClient

log = logging.getLogger("ordo_bot.tools")

# Default max size for a single tool result in the LLM context.
# Configurable via Agent / settings; kept here as a fallback.
DEFAULT_TOOL_RESULT_MAX_CHARS = 2500


def _fn(
    name: str,
    description: str,
    properties: Dict[str, Any],
    required: Optional[List[str]] = None,
) -> Dict[str, Any]:
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

# ------------------------------------------------------------------
# Read-only tools (always offered when Ordo is connected)
# ------------------------------------------------------------------
READ_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    _fn(
        "get_documentation",
        "Fetch Ordo documentation. Prefer format=summary and section=api or overview.",
        {
            "section": {
                "type": "string",
                "description": "Doc section (overview, api, jobs-and-clusters, ...)",
            },
            "format": {
                "type": "string",
                "description": "markdown or summary (prefer summary to save tokens)",
            },
        },
        ["section"],
    ),
    _fn("read_org", "Org / account info for the logged-in user.", {}),
    _fn("read_user", "Current user profile (name, email, org, level).", {}),
    _fn(
        "find_cluster",
        "Look up clusters by path or name (e.g. /root, Monitoring). "
        "Returns a compact tree (ids, names, states) — not full scripts.",
        {"name": _NAME},
        ["name"],
    ),
    _fn(
        "read_cluster",
        "Full cluster detail including nested jobs (by id). Prefer over a huge find_cluster when you know the id.",
        {"id": _ID},
        ["id"],
    ),
    _fn(
        "read_job",
        "Full job detail by id (script, server, state, timings).",
        {"id": _ID},
        ["id"],
    ),
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
]

# ------------------------------------------------------------------
# Write / lifecycle tools (only when user asks to change something)
# ------------------------------------------------------------------
WRITE_TOOL_SCHEMAS: List[Dict[str, Any]] = [
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
        "name is the cron string (e.g. '0 0 8 * * ? *'); cal_id is the calendar id.",
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
        "WRITE: update cluster fields (description, cal_id, handlers, etc.). Only id required.",
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
        "WRITE: update job config (script, server, retries, etc.).",
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

# Full catalog (for docs / "what tools do you have?")
TOOL_SCHEMAS: List[Dict[str, Any]] = READ_TOOL_SCHEMAS + WRITE_TOOL_SCHEMAS

READ_TOOL_NAMES: Set[str] = {
    t["function"]["name"] for t in READ_TOOL_SCHEMAS
}
WRITE_TOOL_NAMES: Set[str] = {
    t["function"]["name"] for t in WRITE_TOOL_SCHEMAS
}

# Phrases that suggest the user wants a mutating action
_WRITE_HINTS = (
    "start", "stop", "kill", "hold", "release", "ice", "melt",
    "complete", "reset", "clone", "create", "add", "delete", "remove",
    "update", "change", "modify", "edit", "run ", "schedule", "attach",
    "set ", "write", "deploy",
)


def user_wants_write(text: str) -> bool:
    """Heuristic: does the user message ask to change Ordo state?"""
    lower = (text or "").lower()
    return any(h in lower for h in _WRITE_HINTS)


def tools_for_turn(*, allow_write: bool) -> List[Dict[str, Any]]:
    """Schemas to send to the LLM for this turn."""
    if allow_write:
        return TOOL_SCHEMAS
    return READ_TOOL_SCHEMAS


def _trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... [truncated, {len(text)} chars total]"


def _compact_cluster_tree(node: Any, depth: int = 0) -> Any:
    """
    Shrink find_cluster / nested cluster payloads for the LLM.
    Keep id, name, jobstate, and children; drop scripts and bulky fields.
    """
    if isinstance(node, list):
        return [_compact_cluster_tree(x, depth) for x in node[:50]]
    if not isinstance(node, dict):
        return node

    keep = {
        "id": node.get("id"),
        "name": node.get("name"),
        "jobstate": node.get("jobstate"),
        "state_id": node.get("state_id"),
        "parent_id": node.get("parent_id"),
        "cal_id": node.get("cal_id"),
        "cal_name": node.get("cal_name"),
        "auto_start": node.get("auto_start"),
    }
    # Nested jobs (compact)
    jobs = node.get("jobs") or node.get("job") or []
    if isinstance(jobs, list) and jobs:
        keep["jobs"] = [
            {
                "id": j.get("id"),
                "name": j.get("name"),
                "jobstate": j.get("jobstate"),
                "server_id": j.get("server_id"),
                "server_name": j.get("server_name"),
            }
            for j in jobs[:40]
            if isinstance(j, dict)
        ]
    # Nested clusters
    kids = node.get("clusters") or node.get("children") or []
    if isinstance(kids, list) and kids:
        keep["clusters"] = [_compact_cluster_tree(c, depth + 1) for c in kids[:40]]
    return {k: v for k, v in keep.items() if v is not None}


def _prepare_result(name: str, data: Any, max_chars: int) -> str:
    """Serialize tool result, compacting heavy commands."""
    if name == "find_cluster" and isinstance(data, dict):
        # Prefer compacting the tree under common keys
        out = dict(data)
        for key in ("clusters", "cluster", "tree", "data"):
            if key in out:
                out[key] = _compact_cluster_tree(out[key])
                break
        else:
            # Whole reply might be the cluster
            if "id" in out and "name" in out:
                out = _compact_cluster_tree(out)
        text = json.dumps(out, default=str)
    elif name == "find_monitor" and isinstance(data, dict):
        # Drop ultra-noisy metrics if present; keep id/name/host/success
        monitors = data.get("monitors") or data.get("servers") or data.get("data")
        if isinstance(monitors, list):
            slim = []
            for m in monitors[:30]:
                if not isinstance(m, dict):
                    continue
                slim.append(
                    {
                        "id": m.get("id"),
                        "name": m.get("name"),
                        "host": m.get("host") or m.get("ip"),
                        "success": m.get("success"),
                        "pctcpu": m.get("pctcpu"),
                        "pctmem": m.get("pctmem"),
                    }
                )
            text = json.dumps({"monitors": slim, "success": data.get("success")}, default=str)
        else:
            text = json.dumps(data, default=str)
    else:
        text = json.dumps(data, default=str)
    return _trim_text(text, max_chars)


_PASSTHROUGH = READ_TOOL_NAMES | WRITE_TOOL_NAMES


async def run_tool(
    ordo: OrdoClient,
    name: str,
    arguments: Dict[str, Any],
    *,
    max_chars: int = DEFAULT_TOOL_RESULT_MAX_CHARS,
) -> str:
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
            cmd.setdefault("format", "summary")
            cmd.setdefault("section", "overview")

        reply = await ordo.send_command(cmd)
        return _prepare_result(name, reply, max_chars)
    except Exception as e:
        log.exception("Tool %s failed", name)
        return json.dumps({"error": str(e)})
