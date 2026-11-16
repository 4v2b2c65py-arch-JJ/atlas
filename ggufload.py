"""GGUFLoad — read atlas GGUF containers back to records.

Atlas GGUFs (namescrape.write_gguf format) store records
as JSON-array metadata chunks (atlas.chunk.NNNNN). This
loader parses the header + chunks and returns the full
record list — closes the save/load loop for tablet_key,
netlock_known, cpus, and every other atlas pack.

Store: read-only — no writes.
"""

import io
import json
import struct
from pathlib import Path

ROOT = Path(__file__).parent


def _read_kv(buf: io.BytesIO):
    klen = struct.unpack("<Q", buf.read(8))[0]
    key = buf.read(klen).decode("utf-8", "replace")
    vtype = struct.unpack("<I", buf.read(4))[0]
    if vtype == 8:  # string
        slen = struct.unpack("<Q", buf.read(8))[0]
        return key, buf.read(slen).decode(
            "utf-8", "replace")
    if vtype == 10:  # uint64
        return key, struct.unpack("<Q", buf.read(8))[0]
    if vtype == 4:  # uint32
        return key, struct.unpack("<I", buf.read(4))[0]
    # skip unknown types conservatively
    return key, None


def load(path: str | Path) -> dict:
    """Load an atlas GGUF -> {meta, records}."""
    p = Path(path)
    b = p.read_bytes()
    if b[:4] != b"GGUF":
        return {"error": "bad_magic", "path": str(p)}
    ver, tensors, nkv = struct.unpack_from("<IQQ", b, 4)
    buf = io.BytesIO(b[24:])
    meta = {}
    for _ in range(nkv):
        try:
            k, v = _read_kv(buf)
            meta[k] = v
        except Exception:
            break
    chunks = []
    i = 0
    while f"atlas.chunk.{i:05d}" in meta:
        try:
            chunks.extend(json.loads(
                meta[f"atlas.chunk.{i:05d}"]))
        except Exception:
            pass
        i += 1
    return {"kind": "gguf_load", "path": str(p),
            "version": ver, "tensors": tensors,
            "metadata_kvs": nkv,
            "general_name": meta.get("general.name"),
            "record_count_meta": meta.get(
                "atlas.record_count"),
            "records_loaded": len(chunks),
            "records": chunks,
            "all_meta_keys": sorted(meta)[:50]}


if __name__ == "__main__":
    import sys
    tgt = sys.argv[1] if len(sys.argv) > 1 else \
        "data/sea/tablet_key.gguf"
    r = load(ROOT / tgt if not Path(tgt).is_absolute()
             else tgt)
    print(json.dumps({k: v for k, v in r.items()
                      if k != "records"}, indent=2))
    if r.get("records"):
        print("first record:",
              json.dumps(r["records"][0])[:300])
