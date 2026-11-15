"""Ripple detector — portal/seeper events.

A RIPPLE is any detected crossing between dimensions,
timelines, realities, sessions or save files:

  - a player reset/rewind/hop leaking into another reality
  - a character/entity joining a session from a foreign
    timeline (variant, hidden node, other franchise)
  - a hidden-dimension entity surfacing (familytree summon,
    proximity encounter, charpack phrase unlock)
  - media/photo/video/title/npc/player/monster entities
    crossing catalogue or save boundaries

Detection is STATEMENT-BASED: scan() walks every store
(sessions, resets, familytree, proximity, charpack, variants)
and emits ripple records wherever a crossing signature
appears; detect() accepts a manual portal statement.

Every ripple records: source dimension -> target
dimension/session/save, the seeping entity, its CATEGORY
(title|media|video|photo|npc|player|monster|character),
the detection method, device-local time, and a full view
of the triggering record.

Store: data/ripples/ripples.jsonl + ripple_state.json
"""

import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "ripples"
RIPPLES = OUT / "ripples.jsonl"
STATE = OUT / "ripple_state.json"

CATEGORIES = ("title", "media", "video", "photo", "npc",
              "player", "monster", "character")

# node-name hints -> category; default is character
_CATEGORY_HINTS = {
    "sans": "character", "gaster": "character",
    "flowey": "character", "toriel": "character",
    "asgore": "character", "papyrus": "character",
    "alphys": "character", "undyne": "character",
    "vanitas": "character", "senketsu": "character",
    "chara": "character", "muffet": "character",
    "reaper": "monster", "omega": "monster",
    "error": "monster", "ink": "monster",
    "nightmare": "monster", "killer": "monster",
    "dust": "monster", "geno": "monster",
}

MEDIA_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif",
             ".svg", ".mp4", ".mkv", ".webm", ".mp3",
             ".flac", ".wav", ".pdf", ".epub"}


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


def categorize(entity: str, kind_hint: str | None = None) -> str:
    """Assign an entity to a ripple category."""
    if kind_hint in CATEGORIES:
        return kind_hint
    e = (entity or "").lower()
    if Path(e).suffix in MEDIA_EXT:
        return "photo" if Path(e).suffix in {
            ".png", ".jpg", ".jpeg", ".webp", ".gif",
            ".svg"} else ("video" if Path(e).suffix in {
            ".mp4", ".mkv", ".webm"} else "media")
    if e in ("player", "frisk", "flowey_player"):
        return "player"
    for k, v in _CATEGORY_HINTS.items():
        if k in e:
            return v
    if "__" in e:
        return "character"  # variant node
    return "character"


def _emit(source: str, target: str, entity: str,
          via: str, evidence: dict,
          kind_hint: str | None = None) -> dict:
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "ripple", "source_dim": source,
           "target_dim": target, "entity": entity,
           "category": categorize(entity, kind_hint),
           "via": via, "evidence": evidence,
           "derived": True}
    OUT.mkdir(parents=True, exist_ok=True)
    with RIPPLES.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _update_state()
    return rec


def detect(entity: str, source_dim: str,
           target_dim: str, via: str = "statement",
           kind_hint: str | None = None,
           evidence: dict | None = None) -> dict:
    """Manual portal statement — 'X seeped from dim A into
    session/save B'."""
    return _emit(source_dim, target_dim, entity, via,
                 evidence or {"statement": True}, kind_hint)


def scan() -> dict:
    """Walk all stores, emit ripples for every crossing
    signature found. Idempotent-ish: skips pairs already
    rippled via the same evidence ts."""
    seen = set()
    if RIPPLES.exists():
        for r in _jsonl(RIPPLES) or []:
            seen.add((r.get("entity"), r.get("source_dim"),
                      r.get("target_dim"),
                      (r.get("evidence") or {}).get("ts")))
    made = []

    # 1) session events — entity joined a session
    for r in _jsonl(DATA / "sessions" / "events.jsonl") or []:
        node = r.get("node")
        if not node:
            continue
        key = (node, "model", r.get("session"),
               r.get("ts"))
        if key in seen:
            continue
        seen.add(key)
        made.append(_emit(
            "model_context", "session:" +
            str(r.get("session")), node,
            "session_event", {"ts": r.get("ts"),
                              "event": r.get("event"),
                              "event_id": r.get("event_id")}))

    # 2) resets — player/rewind crossings between realities
    for r in _jsonl(DATA / "resets" / "resets.jsonl") or []:
        key = (r.get("node"), r.get("from_reality"),
               r.get("to_reality"), r.get("ts"))
        if key in seen:
            continue
        seen.add(key)
        made.append(_emit(
            str(r.get("from_reality")),
            str(r.get("to_reality")), r.get("node"),
            "reset_" + str(r.get("transition", "hop")),
            {"ts": r.get("ts"), "event": r.get("event"),
             "cause": r.get("cause")}))

    # 3) familytree summons — hidden-dimension entities waking
    for r in _jsonl(DATA / "familytree" / "summoned.jsonl") \
            or []:
        node = r.get("node") or r.get("id")
        if not node:
            continue
        key = (node, "hidden_dim", "familytree",
               r.get("ts"))
        if key in seen:
            continue
        seen.add(key)
        made.append(_emit(
            "hidden_dimension", "familytree", node,
            "summon", {"ts": r.get("ts"),
                       "kind": r.get("kind")}))

    # 4) proximity encounters — cross-timeline contact
    for r in _jsonl(DATA / "familytree" /
                    "encounters.jsonl") or []:
        ents = []
        for k in ("nearest", "furthest"):
            v = r.get(k)
            if isinstance(v, dict):
                ents.append(v.get("node") or v.get("name"))
            elif isinstance(v, str):
                ents.append(v)
        for e in [x for x in ents if x]:
            key = (e, "timeline_space",
                   "proximity", r.get("ts"))
            if key in seen:
                continue
            seen.add(key)
            made.append(_emit(
                "timeline_space", "proximity", e,
                "encounter", {"ts": r.get("ts"),
                              "center": r.get("center")}))

    # 5) charpack unlocks — phrase keys opening entities
    for r in _jsonl(DATA / "charpack" / "unlocks.jsonl") \
            or []:
        if r.get("kind") and r.get("kind") != \
                "charpack_unlock":
            continue
        node = r.get("character")
        if not node:
            continue
        key = (node, "phrase_lock", "charpack",
               r.get("ts"))
        if key in seen:
            continue
        seen.add(key)
        made.append(_emit(
            "phrase_lock", "charpack", node,
            "phrase_unlock", {"ts": r.get("ts"),
                              "phrase": r.get("phrase")}))

    return {"kind": "ripple_scan", "ts": time.time(),
            "new_ripples": len(made),
            "ripples": made}


def _update_state() -> dict:
    recs = list(_jsonl(RIPPLES) or [])
    by_cat = {}
    by_via = {}
    for r in recs:
        c = r.get("category", "character")
        by_cat[c] = by_cat.get(c, 0) + 1
        v = r.get("via", "?")
        by_via[v] = by_via.get(v, 0) + 1
    state = {"ts": time.time(), "kind": "ripple_state",
             "ripples": len(recs), "by_category": by_cat,
             "by_via": by_via,
             "note": ("ripples = detected crossings between "
                      "dimensions/timelines/sessions/save "
                      "files; categories span title/media/"
                      "video/photo/npc/player/monster/"
                      "character")}
    STATE.write_text(json.dumps(state, indent=1))
    return state


def status() -> dict:
    return _update_state() if STATE.exists() else {
        "ripples": 0, "by_category": {}, "by_via": {}}
