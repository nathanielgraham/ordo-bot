"""
Watches driven by Ordo WebSocket broadcasts.

We only react to *new* broadcast messages (e.g. jobs_changed / job_updated).
We do not poll Ordo and we do not treat "already complete" as a special case —
if the model wants completion only, it puts jobstate in the filter.

Sugar:
  watch_job(id)     → event jobs_changed (and aliases), filter {id}
  watch_cluster(id) → event clusters_changed (and aliases), filter {id}
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("ordo_bot.watches")

# Broadcast names Ordo may use (and common aliases)
_EVENT_ALIASES: Dict[str, Set[str]] = {
    "jobs_changed": {"jobs_changed", "job_changed", "job_updated", "jobs_updated"},
    "job_updated": {"jobs_changed", "job_changed", "job_updated", "jobs_updated"},
    "clusters_changed": {
        "clusters_changed",
        "cluster_changed",
        "cluster_updated",
        "clusters_updated",
    },
    "cluster_updated": {
        "clusters_changed",
        "cluster_changed",
        "cluster_updated",
        "clusters_updated",
    },
}

_KIND_DEFAULT_EVENT = {
    "job": "job_updated",
    "cluster": "cluster_updated",
}


@dataclass
class Watch:
    event: str  # canonical or "*"
    filter: Dict[str, Any] = field(default_factory=dict)
    label: str = ""
    once: bool = True
    client: Any = None
    created: float = field(default_factory=time.time)


def _events_match(watch_event: str, incoming: str) -> bool:
    if watch_event in ("*", ""):
        return True
    if watch_event == incoming:
        return True
    group = _EVENT_ALIASES.get(watch_event)
    if group and incoming in group:
        return True
    # Incoming might be the alias key
    for _canon, aliases in _EVENT_ALIASES.items():
        if watch_event in aliases and incoming in aliases:
            return True
    return False


def _field_match(obj: Dict[str, Any], key: str, expected: Any) -> bool:
    actual = obj.get(key)

    if key in ("jobstate", "state", "status"):
        # Prefer jobstate, fall back
        actual = obj.get("jobstate") or obj.get("state") or obj.get("status")
        if isinstance(expected, list):
            return str(actual or "").lower() in {str(x).lower() for x in expected}
        return str(actual or "").lower() == str(expected).lower()

    if key == "id":
        try:
            return int(actual) == int(expected)
        except (TypeError, ValueError):
            return actual == expected

    if key == "name":
        return str(actual or "").lower() == str(expected or "").lower()

    if isinstance(expected, list):
        return actual in expected

    return actual == expected


def _object_matches(obj: Dict[str, Any], filt: Dict[str, Any]) -> bool:
    if not filt:
        return True
    for key, expected in filt.items():
        if expected is None:
            continue
        if not _field_match(obj, key, expected):
            return False
    return True


class WatchRegistry:
    def __init__(self) -> None:
        self._watches: List[Watch] = []

    def add_event(
        self,
        *,
        event: str,
        filter: Optional[Dict[str, Any]] = None,
        label: str = "",
        once: bool = True,
        client: Any = None,
    ) -> Dict[str, Any]:
        event = (event or "*").strip() or "*"
        filt = dict(filter or {})
        if "id" in filt and filt["id"] is not None:
            try:
                filt["id"] = int(filt["id"])
            except (TypeError, ValueError):
                pass

        w = Watch(
            event=event,
            filter=filt,
            label=label or "",
            once=bool(once),
            client=client,
        )
        self._watches.append(w)
        log.info("Watch registered: event=%s filter=%s once=%s", event, filt, once)
        return {
            "ok": True,
            "event": event,
            "filter": filt,
            "label": label,
            "once": once,
            "message": (
                f"Watching broadcasts event={event} filter={filt}. "
                "Notification will fire when a matching Ordo broadcast arrives "
                "(not by polling current state)."
            ),
        }

    def add_kind(
        self,
        *,
        kind: str,
        id: int,
        label: str = "",
        client: Any = None,
        jobstate: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Sugar: watch job/cluster id on the usual update broadcasts.

        Does *not* imply terminal state. Pass jobstate="complete" if that is
        what the user asked for.
        """
        kind = kind if kind in {"cluster", "job"} else "cluster"
        event = _KIND_DEFAULT_EVENT[kind]
        filt: Dict[str, Any] = {"id": int(id)}
        if jobstate is not None:
            filt["jobstate"] = jobstate
        return self.add_event(
            event=event,
            filter=filt,
            label=label or f"{kind} {id}",
            once=True,
            client=client,
        )

    def clear_for_client(self, client: Any) -> int:
        before = len(self._watches)
        self._watches = [w for w in self._watches if w.client is not client]
        return before - len(self._watches)

    def clear_all(self) -> int:
        n = len(self._watches)
        self._watches.clear()
        return n

    def match_broadcast(self, event: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Only called when a broadcast is received — never polls."""
        if not self._watches:
            return []

        updates = data.get("updates") or []
        if not isinstance(updates, list):
            updates = []
        if not updates and data.get("id") is not None:
            updates = [data]

        fired: List[Dict[str, Any]] = []
        remaining: List[Watch] = []

        for w in self._watches:
            if not _events_match(w.event, event):
                remaining.append(w)
                continue

            matched_obj: Optional[Dict[str, Any]] = None
            for u in updates:
                if isinstance(u, dict) and _object_matches(u, w.filter):
                    matched_obj = u
                    break

            if matched_obj is None:
                remaining.append(w)
                continue

            name = str(matched_obj.get("name") or "")
            state = str(
                matched_obj.get("jobstate")
                or matched_obj.get("state")
                or matched_obj.get("status")
                or ""
            )
            uid = matched_obj.get("id", "")
            label = w.label or name or str(uid)
            text = (
                f"Notification: **{label}**"
                + (f" (id {uid})" if uid != "" else "")
                + (f" → **{state}**" if state else "")
                + f" [{event}]"
            )
            fired.append({"client": w.client, "text": text, "watch": w})
            log.info(
                "Watch fired on broadcast %s filter=%s state=%s",
                event,
                w.filter,
                state,
            )
            if not w.once:
                remaining.append(w)

        self._watches = remaining
        return fired
