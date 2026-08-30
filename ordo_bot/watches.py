"""
Watches driven by Ordo WebSocket broadcasts.

We react to *new* broadcast messages (jobs_changed / clusters_changed
and aliases). watch_job / watch_cluster default to any *terminal*
state (complete / failed / zombie, or state_id 5) so "tell me when
it's done" does not fire on starting/running.

An optional jobstate filter narrows that. A one-shot snapshot
(read_job / read_cluster) is applied by the agent on arm so an
already-terminal target resolves immediately.

Sugar:
  watch_job(id)     → jobs_changed (+ aliases), filter {id} + terminal
  watch_cluster(id) → clusters_changed (+ aliases), filter {id} + terminal

A child job going terminal does not finish a cluster watch. Only the
cluster row does.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger("ordo_bot.watches")

TERMINAL_JOBSTATES = frozenset({"complete", "failed", "zombie"})
TERMINAL_STATE_IDS = frozenset({5})  # 5 == complete
FILTER_TERMINAL = "_terminal"

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


def jobstate_of(obj: Dict[str, Any]) -> str:
    return str(
        obj.get("jobstate") or obj.get("state") or obj.get("status") or ""
    ).strip()


def is_terminal(obj: Optional[Dict[str, Any]]) -> bool:
    """True when the row has left live states."""
    if not obj or not isinstance(obj, dict):
        return False
    js = jobstate_of(obj).lower()
    if js in TERMINAL_JOBSTATES:
        return True
    try:
        sid = obj.get("state_id")
        if sid is not None and int(sid) in TERMINAL_STATE_IDS:
            return True
    except (TypeError, ValueError):
        pass
    return False


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
                    "(complete/failed/zombie)."
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
        """
        Sugar: watch job/cluster id on the usual update broadcasts.

        If jobstate is omitted, match any terminal state. Pass
        jobstate="complete" (etc.) to require one outcome.
        """
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
        """Apply a read_* row against armed watches (already-terminal)."""
        if not obj or not isinstance(obj, dict):
            return []
        return self._match_updates(event, [obj], require_event=False)

    def match_broadcast(self, event: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Only called when a broadcast is received — never polls."""
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
