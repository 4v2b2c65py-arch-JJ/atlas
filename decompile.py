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


def _str_at(data: bytes, ptr: int) -> str:
    """String record: content at ptr, u32 length at ptr-4
    (verified against room_start / obj_ names)."""
    if ptr < 4 or ptr >= len(data):
        return ""
    ln = struct.unpack("<I", data[ptr - 4:ptr])[0]
    if not (0 < ln < 4096):
        return ""
    return data[ptr:ptr + ln].decode("utf-8", "replace")


def _obj_names(data: bytes, chunks: list[dict]) -> dict:
    """OBJT index -> object name (obj_* resources)."""
    by_id = {c["id"]: c for c in chunks}
    if "OBJT" not in by_id:
        return {}
    c = by_id["OBJT"]
    base = c["offset"]
    cnt = struct.unpack("<I", data[base:base + 4])[0]
    names = {}
    for i in range(min(cnt, 20000)):
        o = struct.unpack(
            "<I", data[base + 4 + 4 * i:base + 8 + 4 * i])[0]
        if o + 4 <= len(data):
            nptr = struct.unpack("<I", data[o:o + 4])[0]
            names[i] = _str_at(data, nptr)
    return names


def room_coords() -> dict:
    """Full ROOM chunk parse — real instance x/y.

    Format (verified vs UndertaleModTool UndertaleRoom):
      namePtr,capPtr u32 -> string content (len@ptr-4)
      w,h,speed u32; persistent,bgColor,drawBG,
      creationCodeId,flags u32; bg/views/gameobjs/tiles
      ptrs u32; world,top,left,right,bottom u32;
      gravityX/Y,mpp f32.
    Pointer lists: u32 count + count*u32 offsets; each
    GameObject = 36 bytes: X i32, Y i32, objDef u32,
    instanceID u32, creationCode u32, scaleX/Y f32,
    color u32, rotation u32."""
    if not WIN.exists():
        return {"error": "no_data_win"}
    data = WIN.read_bytes()
    chunks = _chunks(data)
    by_id = {c["id"]: c for c in chunks}
    if "ROOM" not in by_id:
        return {"error": "no_ROOM"}
    names = _obj_names(data, chunks)
    rc = by_id["ROOM"]
    base = rc["offset"]
    rcount = struct.unpack("<I", data[base:base + 4])[0]
    offs = [struct.unpack(
        "<I", data[base + 4 + 4 * i:base + 8 + 4 * i])[0]
        for i in range(min(rcount, 2000))]
    out = OUT / "room_instances.jsonl"
    total_inst = 0
    rooms_out = []
    with out.open("w") as f:
        for i, o in enumerate(sorted(set(offs))):
            try:
                p = o
                nptr, cp = struct.unpack(
                    "<II", data[p:p + 8]); p += 8
                name = _str_at(data, nptr)
                cap = _str_at(data, cp)
                w, h, sp = struct.unpack(
                    "<III", data[p:p + 12]); p += 12
                (pers, bgc, dbg, ccid,
                 flags) = struct.unpack(
                    "<IIIII", data[p:p + 20]); p += 20
                bgp, vp, gop, tp = struct.unpack(
                    "<IIII", data[p:p + 16]); p += 16
                (world, top, left, right,
                 bottom) = struct.unpack(
                    "<IIIII", data[p:p + 20]); p += 20
                gx, gy, mpp = struct.unpack(
                    "<fff", data[p:p + 12])
                insts = []
                if gop and gop + 4 <= len(data):
                    n = struct.unpack(
                        "<I", data[gop:gop + 4])[0]
                    if 0 < n < 5000:
                        for j in range(n):
                            q = struct.unpack(
                                "<I", data[gop + 4 + 4 * j:
                                       gop + 8 + 4 * j])[0]
                            if q + 36 > len(data):
                                continue
                            (X, Y, obj, iid, cc,
                             sx, sy, col, rot) = \
                                struct.unpack(
                                    "<iiIIIffII",
                                    data[q:q + 36])
                            insts.append({
                                "x": X, "y": Y,
                                "obj": obj,
                                "obj_name":
                                    names.get(obj, ""),
                                "iid": iid, "cc": cc,
                                "sx": round(sx, 3),
                                "sy": round(sy, 3)})
                rec = {"room_index": i, "name": name,
                       "caption": cap, "w": w, "h": h,
                       "speed": sp, "flags": flags,
                       "instances": insts,
                       "n_instances": len(insts)}
                f.write(json.dumps(rec,
                                   ensure_ascii=False)
                        + "\n")
                rooms_out.append(rec)
                total_inst += len(insts)
            except Exception:
                continue
    rec = {"kind": "room_instances",
           "device_time": _now(),
           "rooms": len(rooms_out),
           "instances": total_inst,
           "out": str(out.relative_to(ROOT)),
           "sim_uri": "sim://decompile/room_instances",
           "derived": True}
    with (OUT / "decompile_index.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")
    try:
        import eventbus
        eventbus.emit("decompile_room_instances",
                      {"rooms": len(rooms_out),
                       "instances": total_inst})
    except Exception:
        pass
    return {"kind": "room_coords", "rooms": len(rooms_out),
            "instances": total_inst,
            "sample": rooms_out[11] if len(
                rooms_out) > 11 else rooms_out[:1]}


def extract_audio() -> dict:
    """AUDO chunk -> real wav files. Each entry =
    u32 length + RIFF wav payload."""
    if not WIN.exists():
        return {"error": "no_data_win"}
    data = WIN.read_bytes()
    chunks = _chunks(data)
    by_id = {c["id"]: c for c in chunks}
    if "AUDO" not in by_id:
        return {"error": "no_AUDO"}
    a = by_id["AUDO"]
    base = a["offset"]
    cnt = struct.unpack("<I", data[base:base + 4])[0]
    adir = OUT / "audio"
    adir.mkdir(parents=True, exist_ok=True)
    written = []
    for i in range(min(cnt, 5000)):
        o = struct.unpack(
            "<I", data[base + 4 + 4 * i:
                   base + 8 + 4 * i])[0]
        if o + 8 > len(data):
            continue
        ln = struct.unpack("<I", data[o:o + 4])[0]
        payload = data[o + 4:o + 4 + ln]
        if payload[:4] != b"RIFF":
            continue
        fp = adir / ("audo_%04d.wav" % i)
        fp.write_bytes(payload)
        written.append({"index": i, "file": fp.name,
                        "bytes": ln})
    rec = {"kind": "audio_extract",
           "device_time": _now(),
           "entries": cnt, "wavs": len(written),
           "out": "data/decompile/audio/",
           "sim_uri": "sim://decompile/audio_extract",
           "note": ("raw AUDO wav payloads — name->audio "
                    "mapping via SOND pending"),
           "derived": True}
    with (OUT / "decompile_index.jsonl").open("a") as f:
        f.write(json.dumps(rec) + "\n")
    try:
        import eventbus
        eventbus.emit("decompile_audio_extract",
                      {"wavs": len(written)})
    except Exception:
        pass
    return {"kind": "audio_extract",
            "entries": cnt, "wavs": len(written)}


def sound_map() -> dict:
    """SOND -> AUDO binding. Entry = 9 u32 (36B):
    namePtr, flags, typePtr, filePtr, effects,
    volume f32, pitch f32, preload, audioID.
    Embedded sounds (flags&1) index AUDO entries;
    audio_id -> data/decompile/audio/audo_%04d.wav.
    Verified: 439 sounds, 223 embedded = 223 AUDO."""
    if not WIN.exists():
        return {"error": "no_data_win"}
    data = WIN.read_bytes()
    chunks = _chunks(data)
    by_id = {c["id"]: c for c in chunks}
    if "SOND" not in by_id:
        return {"error": "no_SOND"}
    snd = by_id["SOND"]
    base = snd["offset"]
    cnt = struct.unpack("<I", data[base:base + 4])[0]
    out = OUT / "sound_map.jsonl"
    n_emb = 0
    with out.open("w") as f:
        for i in range(min(cnt, 5000)):
            o = struct.unpack(
                "<I", data[base + 4 + 4 * i:
                       base + 8 + 4 * i])[0]
            if o + 36 > len(data):
                continue
            (np_, fl, tp, fp, fx, vol, pit,
             pre, aud) = struct.unpack(
                "<9I", data[o:o + 36])
            rec = {"index": i,
                   "name": _str_at(data, np_),
                   "flags": fl,
                   "type": _str_at(data, tp),
                   "file": _str_at(data, fp),
                   "volume": round(struct.unpack(
                       "<f", struct.pack(
                           "<I", vol))[0], 4),
                   "pitch": round(struct.unpack(
                       "<f", struct.pack(
                           "<I", pit))[0], 4),
                   "preload": pre,
                   "audio_id": struct.unpack(
                       "<i", struct.pack(
                           "<I", aud))[0],
                   "embedded": bool(fl & 1),
                   "wav": ("data/decompile/audio/"
                           "audo_%04d.wav" % aud
                           if (fl & 1) and 0 <= aud
                           < 5000 else None)}
            if rec["embedded"]:
                n_emb += 1
            f.write(json.dumps(rec, ensure_ascii=False)
                    + "\n")
    idx = {"kind": "sound_map",
           "device_time": _now(),
           "sounds": cnt, "embedded": n_emb,
           "out": str(out.relative_to(ROOT)),
           "sim_uri": "sim://decompile/sound_map",
           "derived": True}
    with (OUT / "decompile_index.jsonl").open("a") as f:
        f.write(json.dumps(idx) + "\n")
    try:
        import eventbus
        eventbus.emit("decompile_sound_map",
                      {"sounds": cnt,
                       "embedded": n_emb})
    except Exception:
        pass
    return {"kind": "sound_map", "sounds": cnt,
            "embedded": n_emb}


def _chunk_entries(data: bytes, chunk: dict) -> list:
    """GEN8 chunk layout: u32 count + count*u32 absolute
    offsets to per-entry blobs (verified: SOND=439,
    AUDO=223, SPRT=2472, TPAG=6390, TXTR=16)."""
    base = chunk["offset"]
    cnt = struct.unpack("<I", data[base:base + 4])[0]
    return [struct.unpack(
        "<I", data[base + 4 + 4 * i:base + 8 + 4 * i])[0]
        for i in range(min(cnt, 100000))]


def _png_end(data: bytes, start: int) -> int:
    """PNG: 8B sig + chunks [len u32BE][type 4B][data][crc4]
    until IEND. Returns offset just past IEND."""
    p = start + 8
    while p + 12 <= len(data):
        ln = struct.unpack(">I", data[p:p + 4])[0]
        typ = data[p + 4:p + 8]
        p += 12 + ln
        if typ == b"IEND":
            return p
        if ln > 0x7FFFFFFF:
            break
    return -1


def extract_visuals() -> dict:
    """SPRT/TPAG/TXTR -> sprites + real PNG textures.

    Verified layouts (vs UndertaleModTool source):
      TXTR: 16 embedded-texture entries {u32, u32 texPtr};
            texPtr -> 0x80-aligned GMImage blob; Undertale
            v1.001 blobs are plain PNG (sig 89 50 4E 47).
            Entries 0-7 are scaled/alias refs that point
            through a {0, ptr} pair to the same 8 PNGs.
      TPAG: 6390 items x 22B: u32 unk + 8xu16 geometry +
            i16 textureIndex (0-15 into TXTR entries).
            First u32 does not resolve as a string ptr —
            recorded raw, honestly unmapped.
      SPRT: 2472 sprites: namePtr, W,H u32, margins i32x4,
            3 bools u32, bboxMode u32, sepMasks u32,
            origin i32x2, then i32 marker: -1 = special
            type (recorded, not deep-parsed); else the
            marker position IS a SimpleList<u32>: count +
            count*u32 pointers to TPAG items. Collision
            mask data follows — skipped (not needed for
            the visual census)."""
    if not WIN.exists():
        return {"error": "no_data_win"}
    data = WIN.read_bytes()
    chunks = _chunks(data)
    by_id = {c["id"]: c for c in chunks}
    tdir = OUT / "textures"
    tdir.mkdir(parents=True, exist_ok=True)

    # ---- TXTR: entry table + PNG extraction ----
    tex_entries = []
    png_at = {}          # file offset -> png index
    if "TXTR" in by_id:
        tx = by_id["TXTR"]
        offs = _chunk_entries(data, tx)
        for i, o in enumerate(offs):
            if o + 8 > len(data):
                continue
            a, b = struct.unpack("<II", data[o:o + 8])
            # resolve blob: direct PNG, or one hop
            # through a {0, ptr} pair (scaled aliases)
            blob = b
            if blob + 8 <= len(data) and not \
                    data[blob:blob + 8].startswith(
                        b"\x89PNG"):
                z, b2 = struct.unpack(
                    "<II", data[blob:blob + 8])
                if z == 0 and b2 < len(data):
                    blob = b2
            end = _png_end(data, blob) \
                if data[blob:blob + 8].startswith(
                    b"\x89PNG") else -1
            tex_entries.append({
                "index": i, "entry_off": o,
                "field_a": a, "blob_off": blob,
                "png": end > 0})
            if end > 0 and blob not in png_at:
                png_at[blob] = len(png_at)
        # write each unique PNG once
        for off, idx in sorted(png_at.items()):
            end = _png_end(data, off)
            fp = tdir / ("txtr_%02d.png" % idx)
            fp.write_bytes(data[off:end])
        # annotate every entry with its resolved png idx
        for e in tex_entries:
            e["png_index"] = png_at.get(e["blob_off"])
            e["png_file"] = ("txtr_%02d.png" % e["png_index"]
                             if e["png_index"] is not None
                             else None)

    # ---- TPAG: item census ----
    tpag_items = []
    tpag_by_off = {}
    if "TPAG" in by_id:
        tp = by_id["TPAG"]
        offs = _chunk_entries(data, tp)
        for i, o in enumerate(offs):
            if o + 22 > len(data):
                continue
            e = data[o:o + 22]
            u32 = struct.unpack("<I", e[:4])[0]
            g = struct.unpack("<8H", e[4:20])
            tidx = struct.unpack("<h", e[20:22])[0]
            rec = {"index": i, "off": o, "unk": u32,
                   "g": list(g), "tex_index": tidx}
            tpag_items.append(rec)
            tpag_by_off[o] = i

    # ---- SPRT: sprite census ----
    sprites = []
    n_special = 0
    if "SPRT" in by_id:
        sp = by_id["SPRT"]
        offs = _chunk_entries(data, sp)
        for i, o in enumerate(offs):
            try:
                p = o
                nptr = struct.unpack("<I",
                                     data[p:p + 4])[0]
                name = _str_at(data, nptr)
                p += 4
                w, h = struct.unpack("<II",
                                     data[p:p + 8]); p += 8
                margins = struct.unpack(
                    "<iiii", data[p:p + 16]); p += 16
                tb, sb, pb = struct.unpack(
                    "<III", data[p:p + 12]); p += 12
                bbox, sep = struct.unpack(
                    "<II", data[p:p + 8]); p += 8
                ox, oy = struct.unpack(
                    "<ii", data[p:p + 8]); p += 8
                marker = struct.unpack(
                    "<i", data[p:p + 4])[0]
                rec = {"index": i, "name": name,
                       "w": w, "h": h,
                       "margins": list(margins),
                       "transparent": bool(tb),
                       "smooth": bool(sb),
                       "preload": bool(pb),
                       "bbox_mode": bbox,
                       "sep_masks": sep,
                       "origin": [ox, oy]}
                if marker == -1:
                    n_special += 1
                    rec["kind"] = "special_sprite"
                    rec["note"] = ("SVersion/SSpriteType "
                                   "follow — not deep-"
                                   "parsed")
                    sprites.append(rec)
                    continue
                # normal: marker pos = SimpleList start
                # (count u32 + count*u32 TPAG ptrs)
                cnt = marker
                texrefs = []
                if 0 < cnt < 500:
                    for j in range(cnt):
                        q = p + 4 + 4 * j
                        if q + 4 > len(data):
                            break
                        tp = struct.unpack(
                            "<I", data[q:q + 4])[0]
                        texrefs.append({
                            "tpag_off": tp,
                            "tpag_index":
                                tpag_by_off.get(tp)})
                rec["kind"] = "sprite"
                rec["textures"] = texrefs
                sprites.append(rec)
            except Exception:
                continue

    # ---- write + emit ----
    sp_out = OUT / "sprites.jsonl"
    with sp_out.open("w") as f:
        for s in sprites:
            f.write(json.dumps(s, ensure_ascii=False)
                    + "\n")
    tp_out = OUT / "tpag_items.jsonl"
    with tp_out.open("w") as f:
        for t in tpag_items:
            f.write(json.dumps(t, ensure_ascii=False)
                    + "\n")

    idx = OUT / "decompile_index.jsonl"
    recs = [
        {"kind": "texture_extract",
         "device_time": _now(),
         "txtr_entries": len(tex_entries),
         "pngs": len(png_at),
         "entries": tex_entries,
         "out": "data/decompile/textures/",
         "sim_uri":
             "sim://decompile/texture_extract",
         "derived": True},
        {"kind": "tpag_census",
         "device_time": _now(),
         "items": len(tpag_items),
         "out": str(tp_out.relative_to(ROOT)),
         "sim_uri": "sim://decompile/tpag_census",
         "note": ("22B items: u32 unk + 8xu16 geom + "
                  "i16 texIndex(0-15); first u32 unmapped"),
         "derived": True},
        {"kind": "sprite_census",
         "device_time": _now(),
         "sprites": len(sprites),
         "special": n_special,
         "out": str(sp_out.relative_to(ROOT)),
         "sim_uri": "sim://decompile/sprite_census",
         "derived": True},
    ]
    with idx.open("a") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False)
                    + "\n")
    try:
        import eventbus
        eventbus.emit("decompile_visuals",
                      {"sprites": len(sprites),
                       "pngs": len(png_at),
                       "tpag": len(tpag_items)})
    except Exception:
        pass
    return {"kind": "visual_extract",
            "device_time": _now(),
            "txtr_entries": len(tex_entries),
            "pngs": len(png_at),
            "tpag_items": len(tpag_items),
            "sprites": len(sprites),
            "special": n_special,
            "png_files": sorted(png_at.values())}


EPISODES = OUT / "episodes.jsonl"


def stream() -> dict:
    """Stream decompile records as episodes — each census
    record crosses decompile -> episode domain under a
    qbit tag, mints a sim://episode/<slug> URI, and emits
    episode_stream events. The vid-source layer: episodes
    are pullable sim resources, not raw dumps."""
    idx = OUT / "decompile_index.jsonl"
    if not idx.exists():
        return {"error": "no_index", "hint": "run() first"}
    recs = [json.loads(l) for l in idx.open()
            if l.strip()]
    # latest run only — a run starts at its chunk_map
    starts = [i for i, r in enumerate(recs)
              if r.get("kind") == "chunk_map"]
    if not starts:
        return {"error": "empty_index"}
    recs = recs[starts[-1]:]
    import qbits
    import simuri
    eps = []
    for r in recs:
        kind = r["kind"]
        slug = _slug(kind)
        sim_uri = simuri.mint("episode", slug)
        x = qbits.cross("decompile_" + kind,
                        "decompile", "episode_stream",
                        reality="sim",
                        via="episode_stream")
        qbits.packet(x.get("qbit") or "",
                     "out", "episode",
                     summary="decompile:%s" % kind,
                     nbytes=r.get("size", 0) or 0,
                     node="decompile")
        ep = {"kind": "episode", "slug": slug,
              "device_time": _now(),
              "source_record": kind,
              "sim_uri": sim_uri,
              "qbit": x.get("qbit"),
              "payload_keys": sorted(r.keys()),
              "derived": True}
        eps.append(ep)
        try:
            import eventbus
            eventbus.emit("episode_stream",
                          {"slug": slug,
                           "source": kind,
                           "sim_uri": sim_uri})
        except Exception:
            pass
    with EPISODES.open("a") as f:
        for e in eps:
            f.write(json.dumps(e, ensure_ascii=False)
                    + "\n")
    return {"kind": "episode_stream",
            "device_time": _now(),
            "episodes": len(eps),
            "slugs": [e["slug"] for e in eps],
            "sim_uris": [e["sim_uri"] for e in eps],
            "qbits": [e["qbit"] for e in eps],
            "note": ("each decompile record streams as an "
                     "episode under its own qbit — pull via "
                     "sim://episode/<slug>")}


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


if __name__ == "__main__":
    print(json.dumps(run(), indent=1)[:1500])
