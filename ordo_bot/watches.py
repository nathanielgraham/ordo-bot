"""
Watches driven by Ordo WebSocket broadcasts.

Generic API: watch_event(event, filter)
Sugar:       watch_cluster / watch_job → terminal jobstate on that id

Notifications are pushed to the client socket; they never enter LLM history.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

log = logging.getLogger("ordo_bot.watches")

_TERMINAL = frozenset(
    {
        "complete",
        "completed",
        "failed",
        "error",
        "killed",
        "cancelled",
        "canceled",
    }
)

# Default events when kind sugar is used
_KIND_EVENTS = {
    "cluster": ("clusters_changed", "cluster_changed"),
    "job": ("jobs_changed", "job_changed"),
}


@dataclass
class Watch:
    """One registered watch."""

    event: str  # broadcast name, or "*" for any
    # Simple equality filters applied to each object in data["updates"]
    # Special keys:
    #   jobstate: str | list — match exact or any of list; "terminal" = terminal set
    filter: Dict[str, Any] = field(default_factory=dict)
    label: str = ""
    once: bool = True
    client: Any = None
    created: float = field(default_factory=time.time)


def _as_list(val: Any) -> List[Any]:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def _field_match(obj: Dict[str, Any], key: str, expected: Any) -> bool:
    actual = obj.get(key)
    if key in ("jobstate", "state", "status") and expected == "terminal":
        return str(actual or "").lower() in _TERMINAL or str(actual or "").lower().startswith(
            "complete"
        )

    if isinstance(expected, list):
        exp_norm = [str(x).lower() if key in ("jobstate", "state", "status") else x for x in expected]
        act = str(actual).lower() if key in ("jobstate", "state", "status") else actual
        return act in exp_norm

    if key in ("jobstate", "state", "status"):
        return str(actual or "").lower() == str(expected).lower()

    if key == "id":
        try:
            return int(actual) == int(expected)
        except (TypeError, ValueError):
            return actual == expected

    if key == "name":
        return str(actual or "").lower() == str(expected or "").lower()

    return actual == expected


def _object_matches(obj: Dict[str, Any], filt: Dict[str, Any]) -> bool:
    if not filt:
        return True
    for key, expected in filt.items():
        if expected is None:
            continue
        # jobstate filter also checks state/status aliases on the object
        if key == "jobstate":
            state_val = obj.get("jobstate") or obj.get("state") or obj.get("status")
            probe = dict(obj)
            probe["jobstate"] = state_val
            if not _field_match(probe, "jobstate", expected):
                return False
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
        # Normalize id to int when possible
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
                f"Watching event={event} filter={filt}"
                + (f" ({label})" if label else "")
                + ". You will be notified when a matching Ordo broadcast arrives."
            ),
        }

    def add_kind(
        self,
        *,
        kind: str,
        id: int,
        label: str = "",
        client: Any = None,
    ) -> Dict[str, Any]:
        """Sugar: watch cluster/job id until terminal jobstate."""
        kind = kind if kind in {"cluster", "job"} else "cluster"
        event = _KIND_EVENTS[kind][0]
        return self.add_event(
            event=event,
            filter={"id": int(id), "jobstate": "terminal"},
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
        if not self._watches:
            return []

        updates = data.get("updates") or []
        if not isinstance(updates, list):
            updates = []
        if not updates and isinstance(data.get("id"), (int, str)):
            updates = [data]

        fired: List[Dict[str, Any]] = []
        remaining: List[Watch] = []

        for w in self._watches:
            if w.event not in ("*", event):
                # Also accept kind sugar events' aliases
                aliases = {
                    "clusters_changed": {"cluster_changed", "clusters_changed"},
                    "jobs_changed": {"job_changed", "jobs_changed"},
                }
                ok = False
                for primary, group in aliases.items():
                    if w.event in group and event in group:
                        ok = True
                        break
                if not ok:
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
                + (f" is now **{state}**" if state else " matched")
                + f" [{event}]."
            )
            fired.append({"client": w.client, "text": text, "watch": w})
            log.info("Watch fired: event=%s filter=%s", event, w.filter)
            if not w.once:
                remaining.append(w)

        self._watches = remaining
        return fired
