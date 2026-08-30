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

TOOL_ALIASES: Dict[str, str] = {
    "list_jobs": "find_cluster",
    "list_clusters": "find_cluster",
    "list_job": "find_cluster",
    "ordo.list_jobs": "find_cluster",
    "ordo.list_clusters": "find_cluster",
    "ordo.find_cluster": "find_cluster",
    "ordo.read_cluster": "read_cluster",
    "ordo.read_job": "read_job",
}


def canonical_tool_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return raw
    lower = raw.lower()
    if lower in TOOL_ALIASES:
        return TOOL_ALIASES[lower]
    if lower.startswith("ordo."):
        rest = raw.split(".", 1)[1]
        mapped = TOOL_ALIASES.get(rest.lower())
        return mapped or rest
    return raw
