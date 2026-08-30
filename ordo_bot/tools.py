"""
Ordo tools the agent can call (WebSocket command surface).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from ordo_bot.ordo_client import OrdoClient

log = logging.getLogger("ordo_bot.tools")

DEFAULT_TOOL_RESULT_MAX_CHARS = 2500
FIND_CLUSTER_MAX_CHARS = 8000

TOOL_ALIASES = {
    "list_jobs": "find_cluster",
    "list_clusters": "find_cluster",
    "list_job": "find_cluster",
    "ordo.list_jobs": "find_cluster",
    "ordo.list_clusters": "find_cluster",
    "ordo.find_cluster": "find_cluster",
    "ordo.read_cluster": "read_cluster",
    "ordo.read_job": "read_job",
}


def canonical_tool_name(name):
    raw = (name or "").strip()
    if not raw:
        return raw
    lower = raw.lower()
    if lower in TOOL_ALIASES:
        return TOOL_ALIASES[lower]
    if lower.startswith("ordo."):
        rest = raw.split(".", 1)[1]
        return TOOL_ALIASES.get(rest.lower()) or rest
    return raw


def _fn(name, description, properties, required=None):
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
    "description": "Cluster name or path (e.g. /root, /root/ops, Monitoring)",
}
_WATCH_JOBSTATE = {
    "type": "string",
    "description": "Optional. If omitted, the watch matches any terminal jobstate (complete, failed, zombie). Pass a specific value only to require that one outcome.",
}

READ_TOOL_SCHEMAS = [
    _fn("get_documentation", "Fetch Ordo documentation. Prefer format=summary.", {"section": {"type": "string"}, "format": {"type": "string"}}, ["section"]),
    _fn("read_org", "Org / account info.", {}),
    _fn("read_user", "Current user profile.", {}),
    _fn("find_cluster", "Look up clusters by path or name and return a NESTED tree plus a flat index. Use this for job trees and paths like /root or /root/ops. Argument name (path is an alias). Default name=/root. Aliases: list_jobs, list_clusters.", {"name": _NAME, "path": {"type": "string", "description": "Alias for name"}}, []),
    _fn("list_jobs", "Alias for find_cluster. Use name=/root for the full tree.", {"name": _NAME, "path": {"type": "string"}}, []),
    _fn("list_clusters", "Alias for find_cluster. Use name=/root for the full tree.", {"name": _NAME, "path": {"type": "string"}}, []),
    _fn("list_tools", "List tools actually available this turn. command_reply is NOT a tool.", {}, []),
    _fn("read_cluster", "One cluster by numeric id: metadata + its direct jobs only. Does NOT include child clusters. For trees/paths use find_cluster.", {"id": _ID}, ["id"]),
    _fn("read_job", "Full job detail by id. Timestamps are ISO-8601 UTC.", {"id": _ID}, ["id"]),
    _fn("find_monitor", "List servers/monitors.", {}),
    _fn("find_cal", "List calendars and crons.", {}),
    _fn("read_cal", "Read one calendar by id or name.", {"id": _ID, "name": _NAME}),
    _fn("find_log", "List past run logs for a job id.", {"id": _ID}, ["id"]),
    _fn("read_log", "Read log output for a job id.", {"id": _ID}, ["id"]),
    _fn("sync", "Reconcile scheduler with server processes.", {}),
    _fn("watch_event", "Subscribe to Ordo WebSocket broadcasts. Prefer watch_job / watch_cluster for completion.", {"event": {"type": "string"}, "filter": {"type": "object"}, "id": {"type": "integer"}, "name": {"type": "string"}, "jobstate": {"type": "string"}, "label": {"type": "string"}, "once": {"type": "boolean"}}, ["event"]),
    _fn("watch_cluster", "Watch clusters_changed for one cluster id until that CLUSTER row is terminal (complete/failed/zombie). start_* success is not done. A child job completing does not finish this watch.", {"id": _ID, "jobstate": _WATCH_JOBSTATE, "label": {"type": "string"}}, ["id"]),
    _fn("watch_job", "Watch jobs_changed for one job id until that job is terminal (complete/failed/zombie). start_* success is not done.", {"id": _ID, "jobstate": _WATCH_JOBSTATE, "label": {"type": "string"}}, ["id"]),
]

WRITE_TOOL_SCHEMAS = [
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
    _fn("create_cluster", "WRITE: create cluster.", {"name": {"type": "string"}, "parent_id": {"type": "integer"}, "description": {"type": "string"}, "cal_id": {"type": "integer"}}, ["name"]),
    _fn("create_job", "WRITE: create job.", {"name": {"type": "string"}, "parent_id": {"type": "integer"}, "script": {"type": "string"}, "server_id": {"type": "integer"}, "description": {"type": "string"}, "retrys": {"type": "integer"}, "delay": {"type": "integer"}}, ["name", "parent_id", "script", "server_id"]),
    _fn("create_cal", "WRITE: create calendar.", {"name": {"type": "string"}, "tz": {"type": "string"}, "description": {"type": "string"}}, ["name", "tz"]),
    _fn("create_cron", "WRITE: add Quartz cron; name=cron string, cal_id=calendar id.", {"name": {"type": "string"}, "cal_id": {"type": "integer"}}, ["name", "cal_id"]),
    _fn("update_cluster", "WRITE: update cluster fields.", {"id": _ID, "newname": {"type": "string"}, "description": {"type": "string"}, "cal_id": {"type": "integer"}, "auto_start": {"type": "boolean"}}, ["id"]),
    _fn("update_job_config", "WRITE: update job config.", {"id": _ID, "newname": {"type": "string"}, "script": {"type": "string"}, "server_id": {"type": "integer"}, "description": {"type": "string"}, "retrys": {"type": "integer"}, "delay": {"type": "integer"}}, ["id"]),
    _fn("delete_job", "WRITE: delete job by id.", {"id": _ID}, ["id"]),
    _fn("delete_cluster", "WRITE: delete cluster by id.", {"id": _ID}, ["id"]),
    _fn("delete_cron", "WRITE: delete cron by id.", {"id": _ID}, ["id"]),
    _fn("delete_cal", "WRITE: delete calendar by id.", {"id": _ID}, ["id"]),
]

TOOL_SCHEMAS = READ_TOOL_SCHEMAS + WRITE_TOOL_SCHEMAS
READ_TOOL_NAMES = {t["function"]["name"] for t in READ_TOOL_SCHEMAS}
WRITE_TOOL_NAMES = {t["function"]["name"] for t in WRITE_TOOL_SCHEMAS}
LOCAL_TOOL_NAMES = {"watch_event", "watch_cluster", "watch_job", "list_tools"}

_WRITE_HINTS = (
    "start", "stop", "kill", "hold", "release", "ice", "melt",
    "complete", "reset", "clone", "create", "add", "delete", "remove",
    "update", "change", "modify", "edit", "run ", "schedule", "attach",
    "set ", "write", "deploy",
)


def user_wants_write(text):
    lower = (text or "").lower()
    return any(h in lower for h in _WRITE_HINTS)


def tools_for_turn(*, allow_write):
    return TOOL_SCHEMAS if allow_write else READ_TOOL_SCHEMAS


def tool_inventory(*, allow_write):
    return {
        "read": sorted(READ_TOOL_NAMES),
        "write": sorted(WRITE_TOOL_NAMES) if allow_write else [],
        "aliases": dict(TOOL_ALIASES),
        "not_tools": ["command_reply", "request_id"],
        "notes": "command_reply is the WebSocket response envelope, not a tool. list_jobs / list_clusters alias find_cluster. find_cluster returns a nested tree; read_cluster is one node + jobs only.",
    }


def _trim_text(text, max_chars):
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"... [truncated, {len(text)} chars total]"


def _iso(value):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        if value.endswith("Z") and "T" in value:
            return value
        try:
            value = int(value)
        except ValueError:
            return value
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OverflowError, OSError, ValueError):
            return value
    return value


def _slim_job(job):
    keep = {
        "id": job.get("id"),
        "name": job.get("name"),
        "jobstate": job.get("jobstate"),
        "server_id": job.get("server_id"),
        "server_name": job.get("server_name") or job.get("server"),
        "started": _iso(job.get("started") or job.get("started_at")),
        "ended": _iso(job.get("ended") or job.get("ended_at")),
        "exit_code": job.get("exit_code"),
    }
    return {k: v for k, v in keep.items() if v is not None}


def _slim_cluster(node, *, include_jobs=True):
    keep = {
        "id": node.get("id"),
        "name": node.get("name"),
        "jobstate": node.get("jobstate"),
        "parent_id": node.get("parent_id"),
        "cal_id": node.get("cal_id"),
        "cal_name": node.get("cal_name"),
        "auto_start": node.get("auto_start"),
        "started": _iso(node.get("started") or node.get("started_at")),
        "ended": _iso(node.get("ended") or node.get("ended_at")),
        "next_run": _iso(node.get("next_run") or node.get("next_scheduled")),
    }
    if include_jobs:
        jobs = node.get("jobs") or node.get("job") or []
        if isinstance(jobs, list) and jobs:
            keep["jobs"] = [_slim_job(j) for j in jobs[:40] if isinstance(j, dict)]
    return {k: v for k, v in keep.items() if v is not None}


def _extract_cluster_list(data):
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("clusters", "cluster", "tree", "data"):
        val = data.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
        if isinstance(val, dict) and ("id" in val or "name" in val):
            return [val]
    if "id" in data and "name" in data:
        return [data]
    return []


def _nest_clusters(flat):
    nodes = {}
    for raw in flat:
        slim = _slim_cluster(raw)
        slim["clusters"] = []
        cid = slim.get("id")
        if cid is None:
            continue
        nodes[cid] = slim
    children_ids = set()
    for raw in flat:
        cid = raw.get("id")
        pid = raw.get("parent_id")
        if cid in nodes and pid in nodes and pid != cid:
            nodes[pid]["clusters"].append(nodes[cid])
            children_ids.add(cid)
    roots = [n for cid, n in nodes.items() if cid not in children_ids]
    if not roots:
        return {"name": "empty", "clusters": []}
    named_root = next((r for r in roots if str(r.get("name") or "").lower() == "root"), None)
    root = named_root or (roots[0] if len(roots) == 1 else None)
    if root is None:
        return {"name": "forest", "clusters": roots}
    extra = [r for r in roots if r is not root]
    if extra:
        root.setdefault("clusters", []).extend(extra)
    return root


def _prepare_find_cluster(data):
    rows = _extract_cluster_list(data)
    index = [{"id": r.get("id"), "name": r.get("name"), "parent_id": r.get("parent_id"), "jobstate": r.get("jobstate")} for r in rows]
    tree = _nest_clusters(rows) if rows else {}
    return {
        "success": (data.get("success") if isinstance(data, dict) else 1) or 1,
        "count": len(index),
        "index": index,
        "tree": tree,
        "hint": "Render tree. Child clusters live under tree.clusters, not read_cluster.",
    }


def _prepare_read_cluster(data):
    if not isinstance(data, dict):
        return {"value": data}
    out = _slim_cluster(data, include_jobs=True)
    out["child_clusters"] = (
        "not included on read_cluster; call find_cluster "
        f"(name=/{out.get('name') or ''} or name=/root) for nested clusters"
    )
    return out


def _prepare_read_job(data):
    if not isinstance(data, dict):
        return {"value": data}
    out = _slim_job(data)
    for extra in ("parent_id", "description", "retrys", "delay"):
        if data.get(extra) is not None:
            out[extra] = data.get(extra)
    return out


def _prepare_result(name, data, max_chars):
    resolved = canonical_tool_name(name)
    if resolved in {"find_cluster", "list_jobs", "list_clusters"}:
        text = json.dumps(_prepare_find_cluster(data), default=str)
        cap = max(max_chars, FIND_CLUSTER_MAX_CHARS)
    elif resolved == "read_cluster":
        text = json.dumps(_prepare_read_cluster(data), default=str)
        cap = max_chars
    elif resolved == "read_job":
        text = json.dumps(_prepare_read_job(data), default=str)
        cap = max_chars
    elif resolved == "find_monitor" and isinstance(data, dict):
        monitors = data.get("monitors") or data.get("servers") or data.get("data")
        if isinstance(monitors, list):
            slim = []
            for m in monitors[:30]:
                if isinstance(m, dict):
                    slim.append({"id": m.get("id"), "name": m.get("name"), "host": m.get("host") or m.get("ip"), "success": m.get("success"), "pctcpu": m.get("pctcpu"), "pctmem": m.get("pctmem")})
            text = json.dumps({"monitors": slim, "success": data.get("success")}, default=str)
        else:
            text = json.dumps(data, default=str)
        cap = max_chars
    else:
        text = json.dumps(data, default=str)
        cap = max_chars
    return _trim_text(text, cap)


def _normalize_args(name, arguments):
    args = dict(arguments or {})
    resolved = canonical_tool_name(name)
    if resolved == "find_cluster":
        args.pop("limit", None)
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


_PASSTHROUGH = (READ_TOOL_NAMES | WRITE_TOOL_NAMES) - LOCAL_TOOL_NAMES


async def run_tool(ordo, name, arguments, *, max_chars=DEFAULT_TOOL_RESULT_MAX_CHARS):
    resolved = canonical_tool_name(name)
    log.info("Tool call: %s -> %s(%s)", name, resolved, arguments)
    try:
        if resolved in LOCAL_TOOL_NAMES:
            if resolved == "list_tools":
                return json.dumps(tool_inventory(allow_write=True))
            return json.dumps({"error": f"{resolved} is local; handled by agent"})
        if resolved not in _PASSTHROUGH:
            return json.dumps({"error": f"Unknown tool: {name}"})
        args = _normalize_args(resolved, arguments)
        cmd = {"command": resolved}
        for k, v in args.items():
            if v is not None and k != "path":
                cmd[k] = v
        if resolved == "get_documentation":
            cmd.setdefault("format", "summary")
            cmd.setdefault("section", "overview")
        reply = await ordo.send_command(cmd)
        return _prepare_result(resolved, reply, max_chars)
    except Exception as e:
        log.exception("Tool %s failed", name)
        return json.dumps({"error": str(e)})
