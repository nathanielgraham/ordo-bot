"""
Completion watches driven by Ordo WebSocket broadcasts.

The LLM registers a watch (cluster or job id). When jobs_changed /
clusters_changed shows that id in a terminal state, we push a message
to the client that requested the watch — without another user turn.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger("ordo_bot.watches")

# States that mean "done enough to notify"
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


@dataclass
class Watch:
    kind: str  # "cluster" | "job"
    id: int
    label: str = ""
    # Opaque client handle (WebSocket); typed Any to avoid circular imports
    client: Any = None
    created: float = field(default_factory=time.time)


class WatchRegistry:
    """In-memory watches; not part of LLM history."""

    def __init__(self) -> None:
        self._watches: List[Watch] = []

    def add(
        self,
        *,
        kind: str,
        id: int,
        client: Any = None,
        label: str = "",
    ) -> Dict[str, Any]:
        kind = kind if kind in {"cluster", "job"} else "cluster"
        w = Watch(kind=kind, id=int(id), label=label or "", client=client)
        self._watches.append(w)
        log.info("Watch registered: %s id=%s label=%r", kind, id, label)
        return {
            "ok": True,
            "kind": kind,
            "id": int(id),
            "label": label,
            "message": (
                f"Watching {kind} {id}"
                + (f" ({label})" if label else "")
                + " for completion via Ordo broadcasts. "
                "You will be notified when it reaches a terminal state."
            ),
        }

    def clear_for_client(self, client: Any) -> int:
        before = len(self._watches)
        self._watches = [w for w in self._watches if w.client is not client]
        return before - len(self._watches)

    def clear_all(self) -> int:
        n = len(self._watches)
        self._watches.clear()
        return n

    def match_broadcast(self, event: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Return fired notifications: [{client, text, watch}, ...]
        Removes matched watches.
        """
        if not self._watches:
            return []

        updates = data.get("updates") or []
        if not isinstance(updates, list):
            updates = []

        # Also accept top-level single object
        if not updates and isinstance(data.get("id"), int):
            updates = [data]

        fired: List[Dict[str, Any]] = []
        remaining: List[Watch] = []

        for w in self._watches:
            hit = self._match_one(w, event, updates)
            if hit is None:
                remaining.append(w)
                continue
            name, state = hit
            label = w.label or name or str(w.id)
            text = (
                f"Notification: {w.kind} **{label}** (id {w.id}) "
                f"is now **{state}**."
            )
            fired.append({"client": w.client, "text": text, "watch": w})
            log.info("Watch fired: %s id=%s state=%s", w.kind, w.id, state)

        self._watches = remaining
        return fired

    def _match_one(
        self, w: Watch, event: str, updates: List[Any]
    ) -> Optional[tuple]:
        for u in updates:
            if not isinstance(u, dict):
                continue
            uid = u.get("id")
            try:
                uid_i = int(uid) if uid is not None else None
            except (TypeError, ValueError):
                uid_i = None
            if uid_i != w.id:
                continue

            state = (
                u.get("jobstate")
                or u.get("state")
                or u.get("status")
                or ""
            )
            state_s = str(state).lower()

            # Prefer matching event type to kind, but accept either
            if w.kind == "job" and event not in {
                "jobs_changed",
                "job_changed",
                "",
            }:
                # still allow if state is terminal on a generic update
                pass
            if w.kind == "cluster" and event not in {
                "clusters_changed",
                "cluster_changed",
                "",
            }:
                pass

            if state_s in _TERMINAL or state_s.startswith("complete"):
                return (str(u.get("name") or ""), state_s)
        return None
