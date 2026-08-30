"""
Ordo tools the agent can call (WebSocket command surface).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set

from ordo_bot.ordo_client import OrdoClient

log = logging.getLogger("ordo_bot.tools")

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
_NAME = {
    "type": "string",
    "description": "Cluster name or path (e.g. /root, Monitoring)",
}

_WATCH_JOBSTATE = {
    "type": "string",
    "description": (
        "Optional. If omitted, the watch matches any terminal jobstate "
        "(complete, failed, zombie). Pass a specific value only to require "
        "that one outcome."
    ),
}

READ_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    _fn(
        "get_documentation",
        "Fetch Ordo documentation. Prefer format=summary.",
        {"section": {"type": "string"}, "format": {"type": "string"}},
        ["section"],
    ),
    _fn("read_org", "Org / account info.", {}),
    _fn("read_user", "Current user profile.", {}),
    _fn(
        "find_cluster",
        "Look up clusters by path or name. Use argument 'name' (path is an alias).",
        {
            "name": _NAME,
            "path": {"type": "string", "description": "Alias for name"},
        },
        [],
    ),
    _fn("read_cluster", "Full cluster detail by id.", {"id": _ID}, ["id"]),
    _fn("read_job", "Full job detail by id.", {"id": _ID}, ["id"]),
    _fn("find_monitor", "List servers/monitors.", {}),
    _fn("find_cal", "List calendars and crons.", {}),
    _fn("read_cal", "Read one calendar by id or name.", {"id": _ID, "name": _NAME}),
    _fn("find_log", "List past run logs for a job id.", {"id": _ID}, ["id"]),
    _fn("read_log", "Read log output for a job id.", {"id": _ID}, ["id"]),
    _fn("sync", "Reconcile scheduler with server processes.", {}),
    _fn(
        "watch_event",
        "Subscribe to Ordo WebSocket broadcasts. Fires only when a matching "
        "broadcast arrives (never polls). Prefer watch_job / watch_cluster for "
        "completion. Use this for custom event+filter pairs.",
        {
            "event": {
                "type": "string",
                "description": "Broadcast name: job_updated, jobs_changed, cluster_updated, clusters_changed, or *",
            },
            "filter": {
                "type": "object",
                "description": "Matched against each object in broadcast updates (id, name, jobstate, ...)",
            },
            "id": {"type": "integer", "description": "Shorthand → filter.id"},
            "name": {"type": "string", "description": "Shorthand → filter.name"},
            "jobstate": {
                "type": "string",
                "description": "Shorthand → filter.jobstate (e.g. complete, running, failed)",
            },
            "label": {"type": "string"},
            "once": {"type": "boolean", "description": "Default true — remove watch after first match"},
        },
        ["event"],
    ),
    _fn(
        "watch_cluster",
        "Watch clusters_changed for one cluster id until that CLUSTER row is "
        "terminal (complete/failed/zombie). start_* success is not done. A child "
        "job completing does not finish this watch. Pass jobstate only to require "
        "one outcome.",
        {
            "id": _ID,
            "jobstate": _WATCH_JOBSTATE,
            "label": {"type": "string"},
        },
        ["id"],
    ),
    _fn(
        "watch_job",
        "Watch jobs_changed for one job id until that job is terminal "
        "(complete/failed/zombie). start_* success is not done. Pass jobstate "
        "only to require one outcome.",
        {
            "id": _ID,
            "jobstate": _WATCH_JOBSTATE,
            "label": {"type": "string"},
        },
        ["id"],
    ),
]

WRITE_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    _fn("start_cluster", "WRITE: start a cluster by id.", {"id": _ID}, ["id"]),
    _fn("start_job", "WRITE: start a job by id.", {"id": _ID}, ["id"]),
    _fn("kill_cluster", "WRITE: kill a cluster by id.", {"id": _ID}, ["id"]),
    _fn("kill_job", "WRITE: kill a job by id.", {"id": _ID}, ["id"]),
    _fn("ice_cluster", "WRITE: ice a cluster by id.", {"id": _ID}, ["id"]),
    _fn("ice_job", "WRITE: ice a job by id.", {"id": _ID}, ["id"]),
    _fn("hold_cluster", "WRITE: hold a cluster by id.", {"id": _ID}, ["id"]),
    _fn("hold_job", "WRITE: hold a job by id.", {"id": _ID}, ["id"]),
    _fn("release_cluster", "WRITE: release a cluster by id.", {"id": _ID}, ["id"]),
    _fn("release_job", "WRITE: release a job by id.", {"id": _ID}, ["id"]),
    _fn("melt_cluster", "WRITE: melt a cluster by id.", {"id": _ID}, ["id"]),
    _fn("melt_job", "WRITE: melt a job by id.", {"id": _ID}, ["id"]),
    _fn("complete_cluster", "WRITE: mark cluster complete.", {"id": _ID}, ["id"]),
    _fn("complete_job", "WRITE: mark job complete.", {"id": _ID}, ["id"]),
    _fn("reset_cluster", "WRITE: reset cluster to startable.", {"id": _ID}, ["id"]),
    _fn("clone_cluster", "WRITE: clone cluster by id.", {"id": _ID}, ["id"]),
    _fn(
        "create_cluster",
        "WRITE: create cluster.",
        {
            "name": {"type": "string"},
            "parent_id": {"type": "integer"},
            "description": {"type": "string"},
            "cal_id": {"type": "integer"},
        },
        ["name"],
    ),
    _fn(
        "create_job",
        "WRITE: create job.",
        {
            "name": {"type": "string"},
            "parent_id": {"type": "integer"},
            "script": {"type": "string"},
            "server_id": {"type": "integer"},
            "description": {"type": "string"},
            "retrys": {"type": "integer"},
            "delay": {"type": "integer"},
        },
        ["name", "parent_id", "script", "server_id"],
    ),
    _fn(
        "create_cal",
        "WRITE: create calendar.",
        {
            "name": {"type": "string"},
            "tz": {"type": "string"},
            "description": {"type": "string"},
        },
        ["name", "tz"],
    ),
    _fn(
        "create_cron",
        "WRITE: add Quartz cron; name=cron string, cal_id=calendar id.",
        {"name": {"type": "string"}, "cal_id": {"type": "integer"}},
        ["name", "cal_id"],
    ),
    _fn(
        "update_cluster",
        "WRITE: update cluster fields.",
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
        "WRITE: update job config.",
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
    _fn("delete_job", "WRITE: delete job by id.", {"id": _ID}, ["id"]),
    _fn("delete_cluster", "WRITE: delete cluster by id.", {"id": _ID}, ["id"]),
    _fn("delete_cron", "WRITE: delete cron by id.", {"id": _ID}, ["id"]),
    _fn("delete_cal", "WRITE: delete calendar by id.", {"id": _ID}, ["id"]),
]

TOOL_SCHEMAS: List[Dict[str, Any]] = READ_TOOL_SCHEMAS + WRITE_TOOL_SCHEMAS

READ_TOOL_NAMES: Set[str] = {t["function"]["name"] for t in READ_TOOL_SCHEMAS}
WRITE_TOOL_NAMES: Set[str] = {t["function"]["name"] for t in WRITE_TOOL_SCHEMAS}
LOCAL_TOOL_NAMES: Set[str] = {"watch_event", "watch_cluster", "watch_job"}

_WRITE_HINTS = (
    "start", "stop", "kill", "hold", "release", "ice", "melt",
    "complete", "reset", "clone", "create", "add", "delete", "remove",
    "update", "change", "modify", "edit", "run ", "schedule", "attach",
    "set ", "write", "deploy",
)


def user_wants_write(text: str) -> bool:
    lower = (text or "").lower()
    return any(h in lower for h in _WRITE_HINTS)


def tools_for_turn(*, allow_write: bool) -> List[Dict[str, Any]]:
    if allow_write:
        return TOOL_SCHEMAS
    return READ_TOOL_SCHEMAS


def _trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... [truncated, {len(text)} chars total]"


def _compact_cluster_tree(node: Any, depth: int = 0) -> Any:
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
    kids = node.get("clusters") or node.get("children") or []
    if isinstance(kids, list) and kids:
        keep["clusters"] = [_compact_cluster_tree(c, depth + 1) for c in kids[:40]]
    return {k: v for k, v in keep.items() if v is not None}


def _prepare_result(name: str, data: Any, max_chars: int) -> str:
    if name == "find_cluster" and isinstance(data, dict):
        out = dict(data)
        for key in ("clusters", "cluster", "tree", "data"):
            if key in out:
                out[key] = _compact_cluster_tree(out[key])
                break
        else:
            if "id" in out and "name" in out:
                out = _compact_cluster_tree(out)
        text = json.dumps(out, default=str)
    elif name == "find_monitor" and isinstance(data, dict):
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


def _normalize_args(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    args = dict(arguments or {})
    if name == "find_cluster":
        if not args.get("name") and args.get("path"):
            args["name"] = args.pop("path")
        elif args.get("path") and args.get("name"):
            if not str(args.get("name") or "").strip():
                args["name"] = args.pop("path")
            else:
                args.pop("path", None)
        if not args.get("name"):
            args["name"] = "/root"
    return args


_PASSTHROUGH = READ_TOOL_NAMES | WRITE_TOOL_NAMES


async def run_tool(
    ordo: OrdoClient,
    name: str,
    arguments: Dict[str, Any],
    *,
    max_chars: int = DEFAULT_TOOL_RESULT_MAX_CHARS,
) -> str:
    log.info("Tool call: %s(%s)", name, arguments)
    try:
        if name in LOCAL_TOOL_NAMES:
            return json.dumps({"error": f"{name} is local; handled by agent"})
        if name not in _PASSTHROUGH:
            return json.dumps({"error": f"Unknown tool: {name}"})

        args = _normalize_args(name, arguments)
        cmd: Dict[str, Any] = {"command": name}
        for k, v in args.items():
            if v is not None and k != "path":
                cmd[k] = v

        if name == "get_documentation":
            cmd.setdefault("format", "summary")
            cmd.setdefault("section", "overview")

        reply = await ordo.send_command(cmd)
        return _prepare_result(name, reply, max_chars)
    except Exception as e:
        log.exception("Tool %s failed", name)
        return json.dumps({"error": str(e)})
