"""RopeCount — RoPE weave depth for deeper-webbed models.

Parses GGUF metadata headers straight off weight files to
extract the rotary-embedding weave: rope.freq_base,
rope.scaling, head counts, context length, block count and
architecture. Models are then CLASSIFIED by era — older
classification = pre-RoPE-era or legacy-rope families
(gpt2/gptneox/gptj/bloom/opt/pythia/mpt/falcon-1/llama-1)
vs modern weave (llama3/qwen2/phi3 with yarn/dynamic
scaling). Every discovered model gets a label.

Honest: non-GGUF formats get format-only labels; truncated
or corrupt headers report parse_error, never invented
values.

Store: data/rope/records.jsonl
"""

import json
import struct
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "rope"
RECORDS = OUT / "records.jsonl"

GGUF_MAGIC = b"GGUF"

# value types per GGUF spec
T_UINT8, T_INT8, T_UINT16, T_INT16, T_UINT32, T_INT32, \
    T_FLOAT32, T_BOOL, T_STRING, T_ARRAY, T_UINT64, \
    T_INT64, T_FLOAT64 = range(13)

OLDER_ARCH = {"gpt2", "gptneox", "gptj", "gpt_neo",
              "bloom", "opt", "pythia", "mpt",
              "falcon", "gpt_bigcode", "gpt_neoX".lower(),
              "recurrent_gemma"}
MODERN_ARCH = {"llama", "llama3", "qwen2", "qwen3",
               "phi3", "phi4", "gemma2", "gemma3",
               "mistral", "mixtral", "command-r",
               "aya", "smollm3"}


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def _append(rec: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with RECORDS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def parse_gguf_header(path: Path, max_kv: int = 400) -> dict:
    """Read the GGUF metadata KV section only — tensors
    never touched. Returns {} + error on non-GGUF."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            if magic != GGUF_MAGIC:
                return {"error": "not_gguf",
                        "format": path.suffix.lstrip(".")}
            version, n_tensors, n_kv = struct.unpack(
                "<IQQ", f.read(20))
            meta = {}
            for _ in range(min(n_kv, max_kv)):
                klen = struct.unpack("<Q", f.read(8))[0]
                if klen > 4096:
                    break
                key = f.read(klen).decode("utf-8", "replace")
                vtype = struct.unpack("<I", f.read(4))[0]
                if vtype == T_UINT8:
                    v = struct.unpack("<B", f.read(1))[0]
                elif vtype == T_INT8:
                    v = struct.unpack("<b", f.read(1))[0]
                elif vtype == T_UINT16:
                    v = struct.unpack("<H", f.read(2))[0]
                elif vtype == T_INT16:
                    v = struct.unpack("<h", f.read(2))[0]
                elif vtype == T_UINT32:
                    v = struct.unpack("<I", f.read(4))[0]
                elif vtype == T_INT32:
                    v = struct.unpack("<i", f.read(4))[0]
                elif vtype == T_FLOAT32:
                    v = struct.unpack("<f", f.read(4))[0]
                elif vtype == T_BOOL:
                    v = bool(struct.unpack("<B", f.read(1))[0])
                elif vtype == T_STRING:
                    slen = struct.unpack("<Q", f.read(8))[0]
                    v = f.read(min(slen, 8192)).decode(
                        "utf-8", "replace")
                    if slen > 8192:
                        f.seek(slen - 8192, 1)
                elif vtype == T_ARRAY:
                    atype = struct.unpack("<I", f.read(4))[0]
                    alen = struct.unpack("<Q", f.read(8))[0]
                    # skip arrays — keep count only
                    skip = {T_UINT8: 1, T_INT8: 1,
                            T_UINT16: 2, T_INT16: 2,
                            T_UINT32: 4, T_INT32: 4,
                            T_FLOAT32: 4, T_BOOL: 1,
                            T_UINT64: 8, T_INT64: 8,
                            T_FLOAT64: 8}.get(atype, 0)
                    if atype == T_STRING:
                        for _ in range(min(alen, 64)):
                            sl = struct.unpack("<Q",
                                               f.read(8))[0]
                            f.seek(sl, 1)
                    else:
                        f.seek(skip * alen, 1)
                    v = f"<array[{alen}]>"
                elif vtype == T_UINT64:
                    v = struct.unpack("<Q", f.read(8))[0]
                elif vtype == T_INT64:
                    v = struct.unpack("<q", f.read(8))[0]
                elif vtype == T_FLOAT64:
                    v = struct.unpack("<d", f.read(8))[0]
                else:
                    break
                meta[key] = v
            return {"ok": True, "version": version,
                    "tensors": n_tensors, "meta": meta}
    except Exception as e:
        return {"error": f"parse_error: {e}"}


def classify(meta: dict) -> dict:
    """Era + rope weave classification from metadata."""
    arch = str(meta.get("general.architecture", "unknown"))
    a = arch.lower()
    rope_base = meta.get(
        f"{a}.rope.freq_base") or meta.get(
        "llama.rope.freq_base")
    rope_scaling = meta.get(
        f"{a}.rope.scaling.type") or meta.get(
        "llama.rope.scaling.type")
    ctx = meta.get(f"{a}.context_length")
    blocks = meta.get(f"{a}.block_count")
    heads = meta.get(f"{a}.attention.head_count")
    kv_heads = meta.get(f"{a}.attention.head_count_kv")
    # era call: legacy families OR default rope base
    # (10000) with no scaling = older weave
    if a in OLDER_ARCH or (a == "llama" and
                           not rope_scaling and
                           ctx and ctx <= 4096):
        era = "older_classification"
    elif a in MODERN_ARCH or rope_scaling:
        era = "modern_weave"
    else:
        era = "unclassified"
    weave = ("scaling" if rope_scaling else
             "legacy_base" if rope_base == 10000 else
             "custom_base" if rope_base else "none")
    label = f"{a}_{era}_{weave}"
    return {"arch": arch, "era": era, "weave": weave,
            "label": label,
            "rope": {"freq_base": rope_base,
                     "scaling": rope_scaling},
            "context_length": ctx, "block_count": blocks,
            "heads": heads, "kv_heads": kv_heads}


def scan(paths=None) -> dict:
    """Discover + label every weight file reachable."""
    cands = []
    # pull file lists from modelscan nodes if present
    for n in _jsonl(DATA / "modelscan" / "nodes.jsonl") or []:
        for f in n.get("files", []):
            p = Path(n["path"]) / f["file"]
            if p.exists():
                cands.append(p)
    # plus explicit paths / common dirs
    for p in (paths or []):
        cands.append(Path(p))
    for d in (DATA / "names", DATA / "gaster",
              DATA / "souls", DATA / "spectrum"):
        if d.exists():
            cands += [p for p in d.rglob("*")
                      if p.suffix.lower() == ".gguf"]
    seen, recs = set(), []
    for p in cands:
        if not p.is_file() or str(p) in seen:
            continue
        seen.add(str(p))
        if p.suffix.lower() != ".gguf":
            rec = {"file": str(p), "device_time":
                   _device_time(), "kind": "rope_record",
                   "format": p.suffix.lstrip("."),
                   "label": f"{p.suffix.lstrip('.')}"
                            "_non_gguf",
                   "derived": True,
                   "note": "rope params need gguf header"}
        else:
            h = parse_gguf_header(p)
            if h.get("ok"):
                c = classify(h["meta"])
                rec = {"file": str(p),
                       "device_time": _device_time(),
                       "kind": "rope_record",
                       "size": p.stat().st_size,
                       "gguf_version": h["version"],
                       "tensors": h["tensors"], **c,
                       "derived": True}
            else:
                rec = {"file": str(p),
                       "device_time": _device_time(),
                       "kind": "rope_record",
                       "error": h.get("error"),
                       "derived": True}
        _append(rec)
        recs.append(rec)
    return {"kind": "rope_scan", "scanned": len(recs),
            "records": recs[:60]}


def status() -> dict:
    recs = list(_jsonl(RECORDS) or [])
    latest = {}
    for r in recs:
        latest[r["file"]] = r
    labels = {}
    for r in latest.values():
        labels[r.get("label", "?")] = \
            labels.get(r.get("label", "?"), 0) + 1
    return {"kind": "rope_state",
            "device_time": _device_time(),
            "models": len(latest), "labels": labels,
            "older": sum(1 for r in latest.values()
                         if r.get("era") ==
                         "older_classification")}
