"""privatepkg — the private package.

Builds private/golb_package.bin — a binary container that
is NEVER committed (private/ is gitignored). Contents per
the spec:

  seed        — adventure_prismo_spec raw block
  instances   — the same room instance stuffed full
                (repeated instance records = 'same
                instance' fill)
  notions     — coined golb terms (notions)
  voice       — voice-model repeater manifest + wav
  tiers       — gaster_sort tiering per item (consistent
                tiering of flow)
  dimension   — the gapless dimension record
  graphel     — cast-time graph element node
  matrix      — dot-matrix code of the package hash
                ('matrix code box dots')

restore() reads the .bin back, verifies per-section
sha256, and rewrites private/restored/* — the package
is self-verifying.

Binary layout: magic 'ATPG' u32 ver u32 nsec u32,
then per section: name[16] u32 len u32 sha256[32]
u32 payload_len u64 payload_off — payloads appended.
"""

import hashlib
import json
import struct
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
PRIV = ROOT / "private"
PKG = PRIV / "golb_package.bin"
RST = PRIV / "restored"


def _now() -> dict:
    n = datetime.now().astimezone()
    return {"ts": n.timestamp(),
            "iso": n.isoformat(timespec="milliseconds"),
            "timezone": n.tzname() or "local",
            "date": n.date().isoformat()}


def _j(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False,
                      separators=(",", ":")).encode()


def _sections() -> list[tuple[str, bytes]]:
    out = []
    # seed — the raw spec block
    try:
        seed = json.loads((ROOT / "data" / "termlib" /
                           "adventure_prismo_spec.json")
                          .read_text())
        out.append(("seed", _j(seed)))
    except Exception:
        pass
    # instances — same instance stuffed full: repeat the
    # richest room's instance set until ~256KB
    try:
        rooms = [json.loads(l) for l in
                 (ROOT / "data" / "decompile" /
                  "room_instances.jsonl").open()]
        rich = max(rooms, key=lambda r:
                   r.get("n_instances", 0))
        blob = _j(rich)
        reps = max(1, 262144 // max(len(blob), 1))
        payload = b"".join(
            blob + b"\n" for _ in range(min(reps, 500)))
        out.append(("instances", payload))
    except Exception:
        pass
    # notions — coined golb terms
    try:
        terms = [json.loads(l) for l in
                 (ROOT / "data" / "termlib" / "terms.jsonl")
                 .open()
                 if '"coined_golb"' in l]
        out.append(("notions", _j(terms)))
    except Exception:
        pass
    # voice — repeater manifest + wav bytes
    try:
        vf = ROOT / "data" / "sea" / "voice" / \
            "svm_test.wav"
        manifest = {"repeater": "smallvoice",
                    "runs": "data/voice_library/"
                            "svm_runs.jsonl",
                    "wav": vf.name if vf.exists() else None,
                    "note": ("voice model repeater keeps "
                             "consistent tiering of flow")}
        payload = _j(manifest)
        if vf.exists():
            payload += b"\n--WAV--\n" + vf.read_bytes()
        out.append(("voice", payload))
    except Exception:
        pass
    # tiers — consistent tiering of flow: gaster_sort on
    # the package's own items
    try:
        import partition
        items = [{"title": "gulb prism spectral",
                  "count": 3, "sources": ["sea",
                                           "termlib"]},
                 {"title": "enchiridion deep storage",
                  "count": 2, "sources": ["sea"]},
                 {"title": "void prism cube",
                  "count": 4, "sources": ["sea",
                                           "decompile"]}]
        out.append(("tiers", _j(
            [partition.gaster_sort(i) for i in items])))
    except Exception:
        pass
    # dimension — gapless dimension record
    out.append(("dimension", _j({
        "kind": "dimension_record",
        "name": "gapless",
        "gap_arisen": False,
        "flow": "seeping",
        "note": ("the dimension where the gap hasnt "
                 "arrisen — flow seeps without loss"),
        "device_time": _now()})))
    # graphel — cast-time graph element
    out.append(("graphel", _j({
        "kind": "graphel_node",
        "name": "graphel",
        "cast_time": True,
        "note": ("graph element under cast time — "
                 "package node in the private graph"),
        "device_time": _now()})))
    return out


def _matrix_code(sha_hex: str) -> list[str]:
    """21x21 dot-matrix render of the package hash —
    'matrix code box dots'. Deterministic from sha."""
    bits = []
    for ch in sha_hex[:42]:
        v = int(ch, 16)
        bits += [(v >> i) & 1 for i in (3, 2, 1, 0)]
    rows = []
    for r in range(21):
        row = ""
        for c in range(21):
            row += "██" if bits[(r * 21 + c) % len(bits)] \
                else "  "
        rows.append(row)
    return rows


def build() -> dict:
    PRIV.mkdir(parents=True, exist_ok=True)
    secs = _sections()
    # first pass: hash all payloads, compute package sha
    meta = []
    for name, payload in secs:
        meta.append({"name": name[:16],
                     "len": len(payload),
                     "sha": hashlib.sha256(
                         payload).hexdigest()})
    pkg_sha = hashlib.sha256(
        _j(meta)).hexdigest()
    matrix = _matrix_code(pkg_sha)
    # append matrix section
    secs.append(("matrix", ("\n".join(matrix)).encode()))
    meta.append({"name": "matrix", "len": len(
        secs[-1][1]), "sha": hashlib.sha256(
        secs[-1][1]).hexdigest()})
    pkg_sha = hashlib.sha256(_j(meta)).hexdigest()

    with PKG.open("wb") as f:
        f.write(b"ATPG")
        f.write(struct.pack("<II", 1, len(secs)))
        toc = f.tell()
        f.write(b"\x00" * 60 * len(secs))
        offs = []
        for name, payload in secs:
            offs.append(f.tell())
            f.write(payload)
        f.seek(toc)
        for (name, payload), m, off in zip(
                secs, meta, offs):
            f.write(name.encode()[:16].ljust(16, b"\x00"))
            f.write(struct.pack("<IQ", m["len"], off))
            f.write(bytes.fromhex(m["sha"]))
    rec = {"kind": "private_package",
           "device_time": _now(),
           "file": str(PKG.relative_to(ROOT)),
           "bytes": PKG.stat().st_size,
           "sha256": pkg_sha,
           "sections": [m["name"] for m in meta],
           "gitignored": True,
           "matrix": matrix,
           "note": ("private package — never committed; "
                    "seed+instances+notions+voice+tiers+"
                    "dimension+graphel+matrix(.bin)")}
    (PRIV / "package_manifest.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=2))
    try:
        import eventbus
        eventbus.emit("private_package",
                      {"bytes": rec["bytes"],
                       "sha": pkg_sha[:12]})
    except Exception:
        pass
    return rec


def restore() -> dict:
    """Read the .bin back, verify every section sha,
    rewrite private/restored/*.json."""
    if not PKG.exists():
        return {"error": "no_package"}
    data = PKG.read_bytes()
    if data[:4] != b"ATPG":
        return {"error": "bad_magic"}
    ver, nsec = struct.unpack("<II", data[4:12])
    RST.mkdir(parents=True, exist_ok=True)
    out = []
    p = 12
    for _ in range(nsec):
        name = data[p:p + 16].rstrip(b"\x00").decode()
        ln, off = struct.unpack("<IQ", data[p + 16:p + 28])
        sha = data[p + 28:p + 60]
        payload = data[off:off + ln]
        ok = hashlib.sha256(payload).digest() == sha
        (RST / (name + ".bin")).write_bytes(payload)
        try:
            (RST / (name + ".json")).write_text(
                payload.decode())
        except Exception:
            pass
        out.append({"name": name, "len": ln,
                    "sha_ok": ok})
        p += 60
    bad = [s["name"] for s in out if not s["sha_ok"]]
    return {"kind": "package_restore",
            "device_time": _now(),
            "sections": out,
            "verified": not bad, "failed": bad,
            "restored_to": "private/restored/"}


def status() -> dict:
    return {"kind": "privatepkg_state",
            "device_time": _now(),
            "package": str(PKG.relative_to(ROOT))
            if PKG.exists() else None,
            "bytes": PKG.stat().st_size
            if PKG.exists() else 0,
            "gitignored": "private/" in
            (ROOT / ".gitignore").read_text(),
            "note": ("private package — local only, "
                     "never pushed")}
