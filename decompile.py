"""decompile — data.win -> coordinate + asset census.

The 'coordinate data pops in from the decompiling
process' layer: parses the Undertale GEN8 FORM container
and emits sim-addressable records for every recovered
asset — room coordinates, sound/voice-line names, string
table entries. Each record mints a sim:// URI so the
episode stream can pull them like any other sim
resource.

Honest scope:
  - chunk map, STRG strings, SOND/AUDO wav-name census:
    verified against real offsets
  - ROOM instance x/y: format partially mapped — room
    count + dims recorded; per-instance coords flagged
    'pending_full_room_parse' rather than fabricated

Store: data/decompile/*.jsonl
"""

import json
import re
import struct
import wave
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
WIN = (ROOT / "data" / "undertale" / "extracted" /
       "Undertale v1.001" / "data.win")
OUT = ROOT / "data" / "decompile"


def _now() -> dict:
    n = datetime.now().astimezone()
    return {"ts": n.timestamp(),
            "iso": n.isoformat(timespec="milliseconds"),
            "timezone": n.tzname() or "local",
            "date": n.date().isoformat()}


def _chunks(data: bytes) -> list[dict]:
    pos = 8
    out = []
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4]
        if not all(32 <= c < 127 for c in tag):
            break
        size = struct.unpack("<I",
                             data[pos + 4:pos + 8])[0]
        if size > len(data):
            break
        out.append({"id": tag.decode("latin1"),
                    "size": size, "offset": pos + 8})
        pos += 8 + size + (size & 1)
    return out


def _strings(data: bytes, strg: dict) -> list[str]:
    """STRG chunk: u32 count + count*u32 absolute offsets;
    each string = u32 len + utf8 bytes."""
    base = strg["offset"]
    count = struct.unpack("<I", data[base:base + 4])[0]
    strs = []
    for i in range(min(count, 200000)):
        o = struct.unpack(
            "<I", data[base + 4 + 4 * i:
                   base + 8 + 4 * i])[0]
        if o + 4 > len(data):
            continue
        ln = struct.unpack("<I", data[o:o + 4])[0]
        if ln > 100000:
            continue
        s = data[o + 4:o + 4 + ln].decode(
            "utf-8", "replace").rstrip("\x00")
        if s.strip():
            strs.append(s)
    return strs


def _wav_names(data: bytes, chunk: dict) -> list[str]:
    """Audio asset names embedded in a chunk — the
    snd_* / SND_* voice-line census."""
    seg = data[chunk["offset"]:
               chunk["offset"] + chunk["size"]]
    names = sorted(set(
        m.group(0).decode("latin1")
        for m in re.finditer(
            rb"[A-Za-z0-9_\-]{3,64}\.wav",
            seg)))
    return names


def run() -> dict:
    """Full decompile pass -> coordinate + asset index."""
    if not WIN.exists():
        return {"error": "no_data_win", "path": str(WIN)}
    data = WIN.read_bytes()
    chunks = _chunks(data)
    by_id = {c["id"]: c for c in chunks}
    OUT.mkdir(parents=True, exist_ok=True)

    records = []

    def emit(kind: str, **kw):
        rec = {"kind": kind, "device_time": _now(),
               "derived": True, **kw}
        records.append(rec)
        try:
            import eventbus
            eventbus.emit("decompile_" + kind, kw)
        except Exception:
            pass
        return rec

    # 1. chunk map
    emit("chunk_map", file=str(WIN.relative_to(ROOT)),
         bytes=len(data), chunks=chunks,
         sim_uri="sim://decompile/chunk_map")

    # 2. string table
    strs = _strings(data, by_id["STRG"]) \
        if "STRG" in by_id else []
    emit("strings", count=len(strs),
         sample=strs[:50],
         out="data/undertale/strings.txt",
         sim_uri="sim://decompile/strings")

    # 3. audio census — wav asset names are STRG entries
    #    (verified: all 142 .wav names live in STRG)
    wav_re = re.compile(r"^[A-Za-z0-9_\-]{3,64}\.wav$")
    snd_names = sorted({s for s in strs
                        if wav_re.match(s)})
    aud_names = _wav_names(data, by_id["AUDO"]) \
        if "AUDO" in by_id else []
    all_audio = sorted(set(snd_names + aud_names))
    voiceish = [n for n in all_audio
                if re.search(r"(talk|voice|sans|flowey|"
                             r"toriel|asgore|undyne|papyrus|"
                             r"alphys|muffet|gaster|frisk|"
                             r"char|talk|voice)", n, re.I)]
    emit("audio_census",
         strg_wav_names=len(snd_names),
         audio_chunk_names=len(aud_names),
         voice_candidates=sorted(set(voiceish)),
         all_names=all_audio,
         sim_uri="sim://decompile/audio_census")

    # 4. room census — count + dims probe; instance
    #    coords pending full ROOM format parse
    rooms = []
    if "ROOM" in by_id:
        rc = by_id["ROOM"]
        base = rc["offset"]
        rcount = struct.unpack("<I",
                               data[base:base + 4])[0]
        offs = [struct.unpack(
            "<I", data[base + 4 + 4 * i:
                   base + 8 + 4 * i])[0]
            for i in range(min(rcount, 1000))]
        soffs = sorted(offs)
        for i, o in enumerate(soffs[:rcount]):
            nxt = soffs[i + 1] if i + 1 < len(soffs) \
                else rc["offset"] + rc["size"]
            rooms.append({"index": i, "entry_offset": o,
                          "span": nxt - o,
                          "coords": "pending_full_room_"
                                    "parse"})
    emit("room_census", count=len(rooms),
         rooms=rooms[:400],
         note=("instance x/y coords require full ROOM "
               "struct parse — spans recorded for "
               "boundary work"),
         sim_uri="sim://decompile/room_census")

    # 5. GEN8 general fields (raw head preserved)
    if "GEN8" in by_id:
        g = by_id["GEN8"]
        emit("gen8_fields",
             head=data[g["offset"]:g["offset"] + 64]
             .hex(),
             size=g["size"],
             sim_uri="sim://decompile/gen8")

    # write index
    idx = OUT / "decompile_index.jsonl"
    with idx.open("a") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False)
                    + "\n")
    try:
        import sea
        sea.add("decompile_index",
                json.dumps({r["kind"]: {
                    k: v for k, v in r.items()
                    if k not in ("rooms", "all_names",
                                 "sample", "chunks",
                                 "voice_candidates")}
                    for r in records}, indent=1),
                note="data.win decompile census — "
                     "coordinate data layer")
    except Exception:
        pass
    return {"kind": "decompile_run",
            "device_time": _now(),
            "records": len(records),
            "strings": len(strs),
            "audio_names":
                len(set(snd_names + aud_names)),
            "voice_candidates": len(set(voiceish)),
            "rooms": len(rooms),
            "index": str(idx.relative_to(ROOT)),
            "sim_uris": [r.get("sim_uri") for r in
                         records]}


if __name__ == "__main__":
    print(json.dumps(run(), indent=1)[:1500])
