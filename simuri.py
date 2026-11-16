"""SimURI — URIs inside the simulation.

Resources created by the simulation get sim:// addresses —
the pull happens AT the simulation, not at external source
URIs. The image's canonical reference is its sim URI;
external URLs are provenance, not identity.

  sim://sea/<slug>          -> sea block (raw preserved)
  sim://voice/<slug>        -> latest voice run + audio
  sim://ocr/<slug>          -> ocr/formation record
  sim://event/<kind>        -> eventbus audit records
  sim://exp/<slug>          -> voice experience records

pull(uri) resolves the resource AND attaches every
simulation event tagged with the same name — the event
tag is the join key, so a pull returns the result where
the image was created, not where it was downloaded.

Store: data/simuri/pulls.jsonl
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SEA = DATA / "sea"
OUT = DATA / "simuri"
PULLS = OUT / "pulls.jsonl"


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _append(rec: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with PULLS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def mint(kind: str, slug: str) -> str:
    return "sim://%s/%s" % (kind, slug)


def _resolve(uri: str) -> dict:
    m = re.match(r"sim://([a-z]+)/(.+)", uri or "")
    if not m:
        return {"error": "bad_uri", "uri": uri,
                "hint": "sim://sea|voice|ocr|event|exp/<slug>"}
    kind, ref = m.group(1), m.group(2)
    slug = _slug(ref)
    if kind == "sea":
        p = SEA / (slug + ".json")
        if p.exists():
            return {"uri": uri, "kind": "sea_block",
                    "resource": json.loads(p.read_text())}
        # media passthrough
        mp = SEA / "media" / ref
        if mp.exists():
            return {"uri": uri, "kind": "sea_media",
                    "resource": {"file": str(mp),
                                 "bytes": mp.stat().st_size}}
        return {"error": "not_found", "uri": uri}
    if kind == "voice":
        runs = [r for r in _jsonl(
            SEA / "voice" / "voice_runs.jsonl") or []
            if r.get("slug") == slug]
        if runs:
            return {"uri": uri, "kind": "voice_run",
                    "resource": runs[-1]}
        return {"error": "not_found", "uri": uri}
    if kind == "ocr":
        p = SEA / "ocr_tablet.json"
        if p.exists():
            return {"uri": uri, "kind": "ocr_record",
                    "resource": json.loads(p.read_text())}
        return {"error": "not_found", "uri": uri}
    if kind == "exp":
        exps = [r for r in _jsonl(
            SEA / "voice" / "experiences.jsonl") or []
            if slug in str(r.get("instance", "")) or
            slug in str(r.get("block", ""))]
        if exps:
            return {"uri": uri, "kind": "voice_experience",
                    "resource": exps[-1], "all": exps}
        return {"error": "not_found", "uri": uri}
    if kind == "event":
        evs = [r for r in _jsonl(
            DATA / "events" / "bus.jsonl") or []
            if r.get("kind") == ref or ref in
            str(r.get("data", {}))]
        if evs:
            return {"uri": uri, "kind": "event_tag",
                    "resource": evs[-1], "all": evs}
        return {"error": "not_found", "uri": uri}
    return {"error": "unknown_kind", "kind": kind}


def _tagged_events(slug: str) -> list[dict]:
    """Every simulation event carrying this block's tag —
    the join key across voice/ripple/experience/qbit."""
    hits = []
    for r in _jsonl(DATA / "events" / "bus.jsonl") or []:
        if slug in json.dumps(r.get("data", {})):
            hits.append(r)
    for r in _jsonl(SEA / "voice" / "experiences.jsonl") \
            or []:
        if slug in str(r.get("block", "")) or \
                slug in str(r.get("instance", "")):
            hits.append({"kind": "voice_experience_log",
                         "data": r})
    for r in _jsonl(SEA / "voice" / "voice_runs.jsonl") \
            or []:
        if r.get("slug") == slug:
            hits.append({"kind": "voice_run_log",
                         "data": r})
    return hits


def pull(uri: str) -> dict:
    """Pull a simulation resource — resolution happens at
    the sim URI; provenance (external URLs) stays attached
    as metadata, never as identity."""
    res = _resolve(uri)
    if res.get("error"):
        return res
    slug = _slug(uri.rsplit("/", 1)[-1])
    tagged = _tagged_events(slug)
    out = {"kind": "sim_pull",
           "device_time": _device_time(),
           "uri": uri, "resolved_kind": res["kind"],
           "resource": res["resource"],
           "tagged_events": len(tagged),
           "events": tagged[-20:],
           "note": ("resolved at simulation URI — external "
                    "source URIs are provenance only"),
           "derived": True}
    _append({"kind": "sim_pull_log", "uri": uri,
             "resolved": res["kind"],
             "device_time": _device_time()})
    try:
        import eventbus
        eventbus.emit("sim_pull", {"uri": uri,
                                   "kind": res["kind"]})
    except Exception:
        pass
    return out


def status() -> dict:
    pulls = list(_jsonl(PULLS) or [])
    return {"kind": "simuri_state",
            "device_time": _device_time(),
            "pulls": len(pulls),
            "scheme": "sim://sea|voice|ocr|event|exp/<slug>",
            "note": ("URIs live inside the simulation; "
                     "external URLs are provenance")}
