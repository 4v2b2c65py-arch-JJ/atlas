"""Multiplayer session layer — online list, chat log, multi-
session by profile and character.

Model:

  - SESSIONS are rooms. Players JOIN with a character +
    profile + optional TIMELINE PHRASE ("extreme pacifist",
    "corrupted save"...). Duplicates are PERMITTED: the same
    character may occupy a session many times, because each
    join mints a unique instance id
    <char>@<timeline_phrase>#<occurrence>.

  - Each instance gets its OWN MEMORY — assembled from every
    recorded dataset that mentions that character: session
    events, reset ledger, ripples, proximity encounters,
    charpack unlocks, variants, timeline combos, mechanics.
    Memory = the layers shaping that instance's interactions.

  - CHAT LOG persists per session (chat.jsonl) with device-
    local timestamps; presence is heartbeat-based
    (join/leave/last_seen) so the online list is real.

Store: data/multi/sessions/<id>/room.json + chat.jsonl
       data/multi/instances/<instance_id>.json (memory)
"""

import json
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "multi"
SESS = OUT / "sessions"
INST = OUT / "instances"

MEMORY_SOURCES = [
    ("sessions", DATA / "sessions" / "events.jsonl",
     lambda r: r.get("node")),
    ("resets", DATA / "resets" / "resets.jsonl",
     lambda r: r.get("node")),
    ("ripples", DATA / "ripples" / "ripples.jsonl",
     lambda r: r.get("entity")),
    ("encounters", DATA / "familytree" /
     "encounters.jsonl",
     lambda r: None),  # pair records — handled below
    ("unlocks", DATA / "charpack" / "unlocks.jsonl",
     lambda r: r.get("character")),
    ("variants", DATA / "variants" / "variants.jsonl",
     lambda r: r.get("node") or r.get("id")),
]


def _device_time() -> dict:
    now = datetime.now().astimezone()
    off = now.utcoffset()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "utc_offset_s": int(off.total_seconds())
            if off else 0,
            "date": now.date().isoformat()}


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _load(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return default


def _room(session: str) -> Path:
    return SESS / session


def _norm_session(s: str) -> str:
    return (s or "lobby").lower().replace(" ", "_")


def _norm_char(c: str) -> str:
    return (c or "").lower().replace(" ", "_")


def _norm_phrase(p: str | None) -> str:
    return (p or "baseline").lower().replace(" ", "_")


def _occurrence(session: str, char: str,
                phrase: str) -> int:
    """How many instances of char@phrase already occupy this
    session — the duplicate counter that makes repeats
    legal."""
    room = _load(_room(session) / "room.json", {})
    n = 0
    for p in room.get("players", []):
        if p.get("char") == char and \
                p.get("timeline_phrase") == phrase:
            n += 1
    return n + 1


def _char_mentions(record: dict, char: str) -> bool:
    """Does this dataset record mention the character —
    directly or inside a pair/compound field?"""
    blob = json.dumps(record).lower()
    return char in blob


def build_memory(char: str, phrase: str,
                 profile: str) -> dict:
    """Assemble an instance's memory from recorded datasets —
    vid/lore/session/reset/ripple records that mention the
    character shape its layers and interactions."""
    memory = {"kind": "instance_memory", "char": char,
              "timeline_phrase": phrase, "profile": profile,
              "layers": {}, "counts": {}}
    for name, path, keyfn in MEMORY_SOURCES:
        hits = []
        for r in _jsonl(path) or []:
            try:
                if keyfn(r) == char or _char_mentions(r, char):
                    hits.append(r)
            except Exception:
                continue
        if hits:
            memory["layers"][name] = hits[-50:]  # bounded
            memory["counts"][name] = len(hits)
    # timeline-phrase layer: combos/mechanics matching the
    # phrase tokens belong to this instance's phrasing
    phrase_tokens = set(phrase.split("_"))
    phrase_hits = []
    for path in (DATA / "profiles" / profile /
                 "timeline_combos.jsonl",):
        for r in _jsonl(path) or []:
            blob = json.dumps(r).lower()
            if phrase_tokens & set(blob.replace(
                    '"', " ").replace(":", " ").split()):
                phrase_hits.append(r)
    if phrase_hits:
        memory["layers"]["timeline_phrase"] = phrase_hits[-50:]
        memory["counts"]["timeline_phrase"] = len(phrase_hits)
    memory["total_records"] = sum(memory["counts"].values())
    memory["derived"] = True
    return memory


def join(session: str, char: str, profile: str = "default",
         timeline_phrase: str | None = None,
         label: str | None = None) -> dict:
    """Join a session. Duplicates permitted — each join mints
    <char>@<phrase>#<occ> and builds that instance's memory."""
    session = _norm_session(session)
    char = _norm_char(char)
    phrase = _norm_phrase(timeline_phrase)
    occ = _occurrence(session, char, phrase)
    inst_id = "%s@%s#%d" % (char, phrase, occ)
    rd = _room(session)
    rd.mkdir(parents=True, exist_ok=True)
    room = _load(rd / "room.json",
                 {"kind": "session_room", "id": session,
                  "created": _device_time()["ts"],
                  "players": []})
    memory = build_memory(char, phrase, profile)
    INST.mkdir(parents=True, exist_ok=True)
    inst = {"instance_id": inst_id, "char": char,
            "timeline_phrase": phrase, "profile": profile,
            "session": session, "label": label,
            "joined": _device_time(),
            "last_seen": _device_time()["ts"],
            "online": True,
            "memory_records": memory["total_records"],
            "memory_layers": sorted(memory["layers"])}
    (INST / (inst_id.replace("/", "_") + ".json")
     ).write_text(json.dumps(
        {**inst, "memory": memory}, indent=1))
    room["players"] = [p for p in room.get("players", [])
                       if p.get("instance_id") != inst_id]
    room["players"].append(inst)
    room["last_activity"] = _device_time()["ts"]
    (rd / "room.json").write_text(json.dumps(room, indent=1))
    # chat join line — the log records arrivals too
    say(session, inst_id, "join",
        meta={"char": char, "phrase": phrase,
              "memory_records": memory["total_records"]})
    return {"kind": "join_result", "instance_id": inst_id,
            "session": session, "online": True,
            "memory_records": memory["total_records"],
            "memory_layers": inst["memory_layers"],
            "duplicates_permitted": True}


def say(session: str, instance_id: str, text: str,
        meta: dict | None = None) -> dict:
    """Append to the session chat log + heartbeat the sender."""
    session = _norm_session(session)
    rd = _room(session)
    rd.mkdir(parents=True, exist_ok=True)
    chat = rd / "chat.jsonl"
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "chat", "session": session,
           "instance": instance_id, "text": text,
           "meta": meta or {}, "derived": True}
    with chat.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    # heartbeat
    room = _load(rd / "room.json", {"players": []})
    for p in room.get("players", []):
        if p.get("instance_id") == instance_id:
            p["last_seen"] = rec["ts"]
            p["online"] = True
    room["last_activity"] = rec["ts"]
    (rd / "room.json").write_text(json.dumps(room, indent=1))
    return rec


def leave(session: str, instance_id: str) -> dict:
    session = _norm_session(session)
    rd = _room(session)
    room = _load(rd / "room.json", {"players": []})
    for p in room.get("players", []):
        if p.get("instance_id") == instance_id:
            p["online"] = False
            p["left"] = _device_time()["ts"]
    (rd / "room.json").write_text(json.dumps(room, indent=1))
    say(session, instance_id, "leave")
    return {"kind": "leave_result",
            "instance_id": instance_id, "online": False}


def roster(session: str | None = None) -> dict:
    """Online list — all sessions or one session's players."""
    if session:
        room = _load(_room(_norm_session(session)) /
                     "room.json", {"players": []})
        return {"kind": "roster", "session": session,
                "players": room.get("players", [])}
    rooms = []
    if SESS.exists():
        for d in sorted(SESS.iterdir()):
            if d.is_dir():
                room = _load(d / "room.json", {})
                rooms.append({
                    "session": d.name,
                    "players": len(room.get("players", [])),
                    "online": sum(1 for p in
                                  room.get("players", [])
                                  if p.get("online")),
                    "last_activity":
                        room.get("last_activity")})
    return {"kind": "session_list", "sessions": rooms}


def chatlog(session: str, limit: int = 100) -> dict:
    session = _norm_session(session)
    recs = list(_jsonl(_room(session) / "chat.jsonl") or [])
    return {"kind": "chatlog", "session": session,
            "count": len(recs),
            "messages": recs[-limit:]}


def instance(instance_id: str) -> dict:
    p = INST / (instance_id.replace("/", "_") + ".json")
    if not p.exists():
        return {"error": "instance not found",
                "instance_id": instance_id}
    return _load(p, {})


def status() -> dict:
    sessions = roster().get("sessions", [])
    insts = list(INST.glob("*.json")) if INST.exists() else []
    return {"kind": "multi_state", "ts": time.time(),
            "sessions": len(sessions),
            "players_online": sum(s.get("online", 0)
                                  for s in sessions),
            "instances": len(insts),
            "note": ("duplicates permitted via "
                     "char@timeline_phrase#occurrence; each "
                     "instance carries memory assembled from "
                     "recorded datasets")}
