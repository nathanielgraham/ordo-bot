"""
Watches driven by Ordo WebSocket broadcasts.

Terminal detection comes from ordo-wsagent (jobstate names only:
complete / failed / zombie / killed). state_id is ignored.

Bot-specific: per-frontend-client targeting and notification text.
The async Ordo socket stays in ordo_bot.ordo_client.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from ordo_wsagent import TERMINAL_JOBSTATES
from ordo_wsagent import is_terminal as lib_is_terminal
from ordo_wsagent import jobstate_of as lib_jobstate_of

log = logging.getLogger("ordo_bot.watches")

FILTER_TERMINAL = "_terminal"

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


def jobstate_of(obj: Optional[Dict[str, Any]]) -> str:
    js = lib_jobstate_of(obj)
    if js:
        return js
    if not obj or not isinstance(obj, dict):
        return ""
    return str(obj.get("state") or obj.get("status") or "").strip().lower()


def is_terminal(obj: Optional[Dict[str, Any]]) -> bool:
    """True when jobstate is complete, failed, zombie, or killed."""
    if not obj or not isinstance(obj, dict):
        return False
    return lib_is_terminal({"jobstate": jobstate_of(obj)})


@dataclass
class Watch:
    event: str
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
    for _canon, aliases in _EVENT_ALIASES.items():
        if watch_event in aliases and incoming in aliases:
            return True
    return False


def _field_match(obj: Dict[str, Any], key: str, expected: Any) -> bool:
    if key == FILTER_TERMINAL:
        return bool(expected) and is_terminal(obj)

    actual = obj.get(key)

    if key in ("jobstate", "state", "status"):
        actual = jobstate_of(obj)
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


def _notify_text(event: str, obj: Dict[str, Any], watch: Watch) -> str:
    name = str(obj.get("name") or "")
    state = jobstate_of(obj)
    uid = obj.get("id", "")
    label = watch.label or name or str(uid)
    return (
        f"Notification: **{label}**"
        + (f" (id {uid})" if uid != "" else "")
        + (f" → **{state}**" if state else "")
        + f" [{event}]"
    )


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
            "filter": {k: v for k, v in filt.items() if not str(k).startswith("_")},
            "terminal_default": bool(filt.get(FILTER_TERMINAL)),
            "label": label,
            "once": once,
            "message": (
                f"Watching broadcasts event={event} filter={filt}. "
                "Fires on a matching Ordo broadcast (not by polling). "
                + (
                    "Default match is any terminal jobstate "
                    "(complete/failed/zombie/killed)."
                    if filt.get(FILTER_TERMINAL)
                    else ""
                )
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
        kind = kind if kind in {"cluster", "job"} else "cluster"
        event = _KIND_DEFAULT_EVENT[kind]
        filt: Dict[str, Any] = {"id": int(id)}
        js = str(jobstate).strip() if jobstate is not None else ""
        if js:
            filt["jobstate"] = jobstate
        else:
            filt[FILTER_TERMINAL] = True
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

    def match_snapshot(
        self, obj: Dict[str, Any], *, event: str = "snapshot"
    ) -> List[Dict[str, Any]]:
        if not obj or not isinstance(obj, dict):
            return []
        return self._match_updates(event, [obj], require_event=False)

    def match_broadcast(self, event: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self._watches:
            return []
        updates = data.get("updates") or []
        if not isinstance(updates, list):
            updates = []
        if not updates and data.get("id") is not None:
            updates = [data]
        return self._match_updates(event, updates, require_event=True)

    def _match_updates(
        self,
        event: str,
        updates: List[Any],
        *,
        require_event: bool,
    ) -> List[Dict[str, Any]]:
        fired: List[Dict[str, Any]] = []
        remaining: List[Watch] = []

        for w in self._watches:
            if require_event and not _events_match(w.event, event):
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

            text = _notify_text(event, matched_obj, w)
            fired.append(
                {
                    "client": w.client,
                    "text": text,
                    "watch": w,
                    "object": matched_obj,
                }
            )
            log.info(
                "Watch fired on %s filter=%s state=%s",
                event,
                w.filter,
                jobstate_of(matched_obj),
            )
            if not w.once:
                remaining.append(w)

        self._watches = remaining
        return fired
