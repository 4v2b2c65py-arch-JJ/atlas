"""EventBus — shared live-event channel for SSE.

Modules emit(kind, data); the hub's /api/events SSE endpoint
streams them to subscribers in real time. Decoupled from
server.py so any module can publish without circular
imports.

Store: data/events/bus.jsonl (audit copy of every emit)
"""

import json
import queue
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
BUS = ROOT / "data" / "events" / "bus.jsonl"

_subs: list[queue.Queue] = []


def subscribe() -> queue.Queue:
    q = queue.Queue(maxsize=200)
    _subs.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    if q in _subs:
        _subs.remove(q)


def subscriber_count() -> int:
    return len(_subs)


def emit(kind: str, data: dict) -> int:
    """Broadcast an event to all SSE subscribers + audit
    log. Returns subscriber count reached."""
    try:
        BUS.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.time(),
               "device_time": datetime.now().astimezone()
               .isoformat(timespec="milliseconds"),
               "kind": kind, "data": data}
        with BUS.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    msg = ("event: %s\ndata: %s\n\n" %
           (kind, json.dumps(data, ensure_ascii=False,
                             default=str)))
    n = 0
    for q in list(_subs):
        try:
            q.put_nowait(msg)
            n += 1
        except queue.Full:
            pass
    return n
