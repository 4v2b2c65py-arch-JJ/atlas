"""Game-session event ledger — formalises LOOPING, LOADING and
PROGRESSION as first-class events.

Each event is classified from three sources:

  - INPUTS   : what the player/session did (action, target,
               env flags, raw payload)
  - OUTPUTS  : what the model produced (deltas, powers, names,
               unlock results)
  - CONTEXT  : live model state — reset ledger status, tiny-
               branch occupancy, genesis reality set, stack
               power of the involved node

Classification rules (all derived, none fabricated):

  loading     : inputs signal a load/continue/title-screen
                action, or outputs carry a 'loading' marker
  looping     : every output delta is 0 AND context shows a
                rising loop_streak or occupied tiny-branch slot
  progression : outputs show a positive gain vs the session's
                previous event (power up, ability added, new
                reality value)
  reset/hop/rewind : delegated shape — distribution deltas from
                the resets ledger semantics
  idle/other  : anything that fits none of the above

Every record stores the FULL VIEW: inputs, outputs, context
snapshot, device-local time (timezone-aware), session id and a
monotonic event id — nothing is summarized away.

Store: data/sessions/events.jsonl + sessions/<id>.jsonl
       + calendar/<date>.jsonl partitions (future datasets)
"""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "sessions"
EVENTS = OUT / "events.jsonl"
CAL = OUT / "calendar"

LOAD_WORDS = ("load", "continue", "title", "boot", "new_game",
              "resume", "save")
ZERO_EPS = 1e-6


def _device_time() -> dict:
    now = datetime.now().astimezone()
    off = now.utcoffset()
    return {
        "ts": now.timestamp(),
        "iso": now.isoformat(timespec="milliseconds"),
        "timezone": now.tzname() or "local",
        "utc_offset_s": int(off.total_seconds()) if off else 0,
        "date": now.date().isoformat(),
        "weekday": now.strftime("%A"),
        "hour": now.hour,
    }


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _context(node: str | None) -> dict:
    """Live model state snapshot at event time."""
    ctx = {"kind": "session_context", "derived": True}
    try:
        import resets
        ctx["reset_state"] = resets.status()
    except Exception as e:
        ctx["reset_state"] = {"error": str(e)}
    try:
        import genesis
        ctx["reality_set"] = genesis.reality_set()
    except Exception as e:
        ctx["reality_set"] = {"error": str(e)}
    if node:
        try:
            import stack
            full = stack.build_stack(node, "default")
            ctx["node_power"] = stack._power(full)
            ctx["node_abilities"] = sum(
                full["counts"].values())
        except Exception as e:
            ctx["node_power"] = {"error": str(e)}
    return ctx


def _all_zero(outputs: dict) -> bool:
    """Every numeric leaf in outputs is ~0 — the looping
    signature: the session produced nothing."""
    nums = []

    def walk(o):
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, (int, float)) and \
                not isinstance(o, bool):
            nums.append(abs(o))
    walk(outputs)
    return bool(nums) and all(n < ZERO_EPS for n in nums)


def _max_delta(outputs: dict) -> float:
    best = 0.0

    def walk(o):
        nonlocal best
        if isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
        elif isinstance(o, (int, float)) and \
                not isinstance(o, bool):
            best = max(best, abs(o))
    walk(outputs)
    return best


def _has_positive_gain(outputs: dict,
                       prev_outputs: dict | None) -> bool:
    """Progression = this event's peak output exceeds the
    session's previous peak — the model produced MORE than
    last time (new power, new ability, new value)."""
    cur = _max_delta(outputs)
    prev = _max_delta(prev_outputs or {})
    return cur > prev + ZERO_EPS


def _is_loading(inputs: dict, outputs: dict) -> bool:
    blob = json.dumps(inputs).lower()
    if any(w in blob for w in LOAD_WORDS):
        return True
    oblob = json.dumps(outputs).lower()
    return "loading" in oblob or "loaded" in oblob


def _classify(inputs: dict, outputs: dict,
              context: dict,
              prev_outputs: dict | None) -> str:
    if _is_loading(inputs, outputs):
        return "loading"
    rs = context.get("reset_state") or {}
    if _all_zero(outputs) and (
            rs.get("looping", 0) > 0 or
            rs.get("tiny_occupied", 0) > 0):
        return "looping"
    if _has_positive_gain(outputs, prev_outputs):
        return "progression"
    if _all_zero(outputs):
        return "looping"  # zero output alone still loops
    return "event"


def _prev_outputs(session: str) -> dict | None:
    path = OUT / "sessions" / (session + ".jsonl")
    last = None
    for r in _jsonl(path) or []:
        last = r
    return (last or {}).get("outputs")


def record_event(session: str, inputs: dict | None = None,
                 outputs: dict | None = None,
                 node: str | None = None,
                 cause: str = "player") -> dict:
    """Record a full-view game-session event. Classification is
    derived from inputs + outputs + live model context — the
    record keeps ALL of it, nothing trimmed."""
    session = (session or "default").lower().replace(" ", "_")
    inputs = inputs or {}
    outputs = outputs or {}
    context = _context(node)
    prev = _prev_outputs(session)
    label = _classify(inputs, outputs, context, prev)
    dev = _device_time()
    # monotonic per-session event id + content fingerprint
    sess_dir = OUT / "sessions"
    sess_dir.mkdir(parents=True, exist_ok=True)
    seq = sum(1 for _ in _jsonl(sess_dir / (session + ".jsonl"))
              or []) + 1
    vid = "evt-%s-%s-%04d" % (dev["date"], session, seq)
    fp = hashlib.sha256(json.dumps(
        {"i": inputs, "o": outputs, "c": label},
        sort_keys=True).encode()).hexdigest()[:16]
    rec = {
        "event_id": vid, "fingerprint": fp,
        "ts": dev["ts"], "device_time": dev,
        "kind": "session_event", "session": session,
        "node": node, "cause": cause,
        "event": label,
        "full_view": True,
        "inputs": inputs,
        "outputs": outputs,
        "model_context": context,
        "derived": True,
    }
    with EVENTS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with (sess_dir / (session + ".jsonl")).open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    day = CAL / (dev["date"] + ".jsonl")
    day.parent.mkdir(parents=True, exist_ok=True)
    with day.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def session_events(session: str) -> dict:
    """All events for one session, with label counts."""
    recs = list(_jsonl(OUT / "sessions" /
                       (session.lower() + ".jsonl")) or [])
    counts = {}
    for r in recs:
        counts[r.get("event", "event")] = \
            counts.get(r.get("event", "event"), 0) + 1
    return {"kind": "session_view", "session": session,
            "events": len(recs), "labels": counts,
            "records": recs}


def status() -> dict:
    recs = list(_jsonl(EVENTS) or [])
    labels = {}
    sessions = {}
    for r in recs:
        labels[r.get("event", "event")] = \
            labels.get(r.get("event", "event"), 0) + 1
        s = r.get("session", "?")
        sessions[s] = sessions.get(s, 0) + 1
    days = {}
    if CAL.exists():
        for p in sorted(CAL.glob("*.jsonl")):
            days[p.stem] = sum(1 for _ in p.open())
    return {"kind": "session_state", "ts": time.time(),
            "events": len(recs), "labels": labels,
            "sessions": sessions, "calendar": days,
            "note": ("looping/loading/progression formalised "
                     "from inputs+outputs+model context; "
                     "every record is a full view")}
