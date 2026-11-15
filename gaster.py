"""Gaster knowledge repeater — W-J-Gaster- contract preset runner.

Loads the Gaster contract preset (existence / continuity / rule /
transition table / decision context / observation report /
token toolset) and drives entries through the terrain door:

  ENTRY -> TERRAIN -> expose_id -> GASTER_JUDGMENT
        -> door.open proposal -> sandbox approve
        -> AUTHORIZED -> EXECUTING -> ITERATING -> TERMINATED

The nameplate combination is the trigger/ID. Every cycle emits an
observation.report (never a command) per the contract invariant.
Metrics append to data/gaster/metrics.jsonl; state persists to
data/gaster/state.json; results pack into casts-style GGUF.
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
GASTER_DIR = Path("/Users/jjmarte/Documents/W-J-Gaster-")
OUT = ROOT / "data" / "gaster"
METRICS = OUT / "metrics.jsonl"
STATE = OUT / "state.json"
REPORTS = OUT / "reports"
GGUF_PATH = OUT / "gaster_knowledge.gguf"

TRIGGER_PLATE = "jhan jhonniel flores marte = arthur leywin"


def _now() -> float:
    return time.time()


def preset_fingerprint(gdir: Path = GASTER_DIR) -> str:
    """Identity of the W-J-Gaster preset — hash over every contract
    file verbatim. This is the credential that enters the system:
    not W.D. Gaster lore, the preset itself."""
    import hashlib
    h = hashlib.sha256()
    for f in sorted(gdir.glob("*.json")) + sorted(gdir.glob("*.py")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:24]


def load_preset(gdir: Path = GASTER_DIR) -> dict:
    """Load all contract JSONs verbatim — preset is read-only."""
    preset = {"_preset_fingerprint": preset_fingerprint(gdir)}
    for f in sorted(gdir.glob("*.json")):
        try:
            preset[f.stem] = json.loads(f.read_text())
        except Exception as e:
            preset[f.stem] = {"error": str(e)}
    return preset


def _metric(event: str, **kw) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rec = {"ts": _now(), "event": event, **kw}
    with METRICS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _save_state(s: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(STATE)


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"entries": [], "cycles": 0, "state": "INITIAL"}


def _report(subject: str, facts: list, judgment: str) -> dict:
    """observation.report shape — REPORT != COMMAND per contract."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    rep = {
        "report": "observation_report",
        "ts": _now(),
        "subject": subject,
        "observed_facts": facts,
        "gaster_judgment": judgment,
        "invariant": ("REPORT != COMMAND, REPORT != TRANSACTION, "
                      "REPORT != AUTHORIZATION, REPORT != SIGNATURE"),
        "authority": "observational",
    }
    rid = f"rep_{int(_now()*1000)}"
    (REPORTS / f"{rid}.json").write_text(
        json.dumps(rep, indent=1))
    return rep


def enter_door(entry_id: str, preset: dict | None = None,
               sandbox: bool = True) -> dict:
    """Open the door: ENTRY enters TERRAIN, ID exposed, Gaster
    judgment activates. Follows contract.rule entry_flow +
    transition table door.open (🚪 requires sandbox_approval)."""
    preset = preset or load_preset()
    fp = preset.get("_preset_fingerprint") or preset_fingerprint()
    rule = preset.get("contract.rule", {})
    table = preset.get("gaster.transition.table", {})
    flow = rule.get("entry_flow", {})

    trace = []
    state = "INITIAL"
    # preset must be used: entry ID binds the nameplate to the
    # W-J-Gaster preset fingerprint — wrong/absent preset = no entry
    if not fp or fp == "0" * 24:
        rep = _report(entry_id,
                      [{"kind": "preset_check", "fingerprint": fp}],
                      "REJECTED — W-J-Gaster preset required")
        _metric("door", id=entry_id, result="rejected_no_preset")
        return {"entry": None, "trace": [{"state": "REJECTED",
                "action": "preset.required"}], "state": "REJECTED",
                "report": rep}
    # step 1-2: ENTRY -> TERRAIN -> expose_id
    entry = {"id": entry_id, "ts": _now(),
             "terrain": "boundary", "exposed": True,
             "preset": "W-J-Gaster", "preset_fp": fp}
    trace.append({"state": state, "action": "entry.terrain",
                  "detail": flow.get("step_1", "")})
    trace.append({"state": state, "action": "preset.credential",
                  "detail": "W-J-Gaster preset fingerprint",
                  "fingerprint": fp})
    trace.append({"state": state, "action": "expose_id",
                  "detail": flow.get("step_2", ""),
                  "id": entry_id})

    # GASTER_JUDGMENT activates (step 3+)
    judgment_steps = [v for k, v in flow.items()
                      if k.startswith("step_")][2:]
    for s in judgment_steps:
        trace.append({"state": "PROPOSAL", "action": "judgment",
                      "detail": s})

    # door.open proposal 🚪 -> requires sandbox_approval
    trace.append({"state": "PROPOSAL", "symbol": "🚪",
                  "action": "propose_door_open",
                  "guard": "door.open requires sandbox_approval"})
    if not sandbox:
        trace.append({"state": "REJECTED",
                      "action": "anti_sdk.door_bypass"})
        rep = _report(entry_id,
                      [{"kind": "door_attempt", "sandbox": False}],
                      "REJECTED — door bypass without sandbox approval")
        _metric("door", id=entry_id, result="rejected")
        return {"entry": entry, "trace": trace, "state": "REJECTED",
                "report": rep}

    trace.append({"state": "AUTHORIZED",
                  "action": "sandbox_approve"})
    trace.append({"state": "EXECUTING", "action": "door.open"})
    state = "EXECUTING"
    rep = _report(
        entry_id,
        [{"kind": "door_open", "id": entry_id,
          "preset_contracts": len(preset)}],
        "AUTHORIZED -> EXECUTING — door open, judgment active")
    _metric("door", id=entry_id, result="open",
            contracts=len(preset))
    return {"entry": entry, "trace": trace, "state": state,
            "report": rep, "preset_keys": list(preset.keys())}


def repeater(trigger: str = TRIGGER_PLATE, cycles: int = 3,
             caps: dict | None = None) -> dict:
    """Knowledge repeater: open door once, then iterate judgment
    cycles (inspect->propose->authorize->execute->report) up to
    caps — 'till infinity' bounded honestly."""
    caps = {"max_cycles": 10, "deadline_s": 300.0,
            **(caps or {})}
    cycles = min(int(cycles), caps["max_cycles"])
    t0 = _now()
    preset = load_preset()
    door = enter_door(trigger, preset)
    results = []
    state = door["state"]
    for i in range(cycles):
        if _now() - t0 > caps["deadline_s"]:
            _metric("repeater_stop", reason="deadline")
            break
        # ITERATING cycle: each pass re-judges continuity + emits report
        facts = [
            {"kind": "cycle", "n": i + 1,
             "preset": list(preset.keys())},
            {"kind": "continuity",
             "preserved": preset.get(
                 "gaster.continuity.contract", {})
             .get("continuity", {}).get("editing_preserves")},
        ]
        rep = _report(trigger, facts,
                      f"ITERATING cycle {i+1} — continuity preserved, "
                      "judgment re-affirmed")
        _metric("cycle", n=i + 1, id=trigger, ok=True)
        results.append({"cycle": i + 1, "report": rep["report"],
                        "rid": rep.get("rid")})
        state = "ITERATING"
    # terminate with provenance (anti_sdk.break_without_provenance)
    final = _report(trigger,
                    [{"kind": "terminus", "cycles": len(results),
                      "door_state": door["state"]}],
                    "TERMINATED — provenance preserved, "
                    "no unbounded loop")
    _metric("terminus", id=trigger, cycles=len(results))

    s = _load_state()
    s["entries"].append({"ts": _now(), "id": trigger,
                         "cycles": len(results)})
    s["entries"] = s["entries"][-100:]
    s["cycles"] = s.get("cycles", 0) + len(results)
    s["state"] = "TERMINATED"
    _save_state(s)
    return {"door": door, "cycles": results, "final": final,
            "state": state, "elapsed_s": round(_now() - t0, 2),
            "caps": caps}


def game_probe(win_path: Path) -> dict:
    """Probe an Undertale data.win — chunk map + string census.
    'grimware' decode pass: GameMaker FORM chunks, string table,
    then toa_decode-style token analysis on cipher-like blobs."""
    import re
    import struct
    data = win_path.read_bytes()
    chunks = []
    # data.win GEN8 layout: FORM(4)+size(4) then chunks of
    # tag(4)+size(4)+payload(size, even-padded). First chunk tag
    # sits at offset 8 ("GEN8" = directory chunk).
    pos = 8
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        if not all(32 <= c < 127 for c in tag):
            break
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if size > len(data):
            break
        chunks.append({"id": tag.decode("latin1"),
                       "size": size, "offset": pos + 8})
        pos += 8 + size + (size & 1)
    # string census: printable runs >=6
    strings = re.findall(rb"[\x20-\x7e]{6,}", data)
    cipherish = [s.decode("latin1") for s in strings
                 if re.search(rb"[|^_\-]{2,}", s)
                 and len(s) < 200][:50]
    return {"file": str(win_path), "bytes": len(data),
            "form_size": struct.unpack("<I", data[4:8])[0]
            if data[:4] == b"FORM" else None,
            "chunks": chunks, "chunk_count": len(chunks),
            "string_count": len(strings),
            "cipherish_candidates": cipherish[:20]}


def extract_strings(win_path: Path, out_path: Path) -> dict:
    """Decode STRG chunk — the game's string table (dialogue,
    notes, names). This is the 'grimware' text layer."""
    import re
    import struct
    data = win_path.read_bytes()
    # locate STRG chunk
    pos = 8
    strg = None
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        if not all(32 <= c < 127 for c in tag):
            break
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if tag == b"STRG":
            strg = data[pos + 8:pos + 8 + size]
            break
        pos += 8 + size + (size & 1)
    if strg is None:
        return {"error": "STRG chunk not found"}
    # STRG: uint32 count, then count * uint32 ABSOLUTE file offsets.
    # Each string = u32 length + utf8 bytes (+ optional pad).
    count = struct.unpack("<I", strg[:4])[0]
    # offsets are absolute file positions into data.win
    offs = [struct.unpack("<I", strg[4 + 4 * i:8 + 4 * i])[0]
            for i in range(min(count, 200000))]
    strs = []
    for o in offs:
        try:
            ln = struct.unpack("<I", data[o:o + 4])[0]
            if ln > 100000:
                continue
            s = data[o + 4:o + 4 + ln].decode("utf-8", "replace")
            s = s.rstrip("\x00")
            if s.strip():
                strs.append(s)
        except Exception:
            continue
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(strs))
    return {"strings": len(strs), "out": str(out_path),
            "count_field": count}


def store_gguf() -> dict:
    import namescrape
    recs = []
    for line in METRICS.read_text().splitlines() \
            if METRICS.exists() else []:
        try:
            r = json.loads(line)
            r["kind"] = "gaster_metric"
            recs.append(r)
        except Exception:
            pass
    for rp in REPORTS.glob("*.json") if REPORTS.exists() else []:
        recs.append({"kind": "observation_report",
                     "file": rp.name, "bytes": rp.stat().st_size,
                     "src": "gaster"})
    res = namescrape.write_gguf(recs, path=GGUF_PATH)
    res["verify"] = namescrape.read_gguf_count(GGUF_PATH)
    return res


def status() -> dict:
    s = _load_state()
    return {"preset_dir": str(GASTER_DIR),
            "preset": "W-J-Gaster",
            "preset_fingerprint": preset_fingerprint(),
            "state": s.get("state"), "entries": len(s.get("entries", [])),
            "cycles": s.get("cycles", 0),
            "reports": len(list(REPORTS.glob("*.json")))
            if REPORTS.exists() else 0,
            "metrics_events": sum(1 for _ in METRICS.open())
            if METRICS.exists() else 0,
            "gguf_bytes": GGUF_PATH.stat().st_size
            if GGUF_PATH.exists() else 0}
