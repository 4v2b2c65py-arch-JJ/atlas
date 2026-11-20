"""namehall — the Library of Babel / memory hall of
memories: a name query space.

Every name the simulation knows gets indexed into one
queryable hall. Each name carries DOT DIMENSIONS — a
21x21 binary matrix hashed from its name plus structural
dimensions (length class, vowel ratio, prefix family) —
so names can be identified and bridged ('namebriger')
by their dot signature, not just string match.

Sources swept: familytree nodes, termlib terms, sea
blocks, decompile rooms + object names, audio census,
voice refs, truename records.

Store: data/namehall/hall.jsonl + index.json
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "namehall"
HALL = OUT / "hall.jsonl"


def _now() -> dict:
    n = datetime.now().astimezone()
    return {"ts": n.timestamp(),
            "iso": n.isoformat(timespec="milliseconds"),
            "timezone": n.tzname() or "local",
            "date": n.date().isoformat()}


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def dot_dimensions(name: str) -> dict:
    """The name's dot signature: 21x21 matrix from sha256
    + structural dims. Identifies names beyond string
    equality — near-names share dimensional neighborhoods.
    """
    sha = hashlib.sha256(name.lower().encode()).hexdigest()
    bits = []
    for ch in sha:
        v = int(ch, 16)
        bits += [(v >> i) & 1 for i in (3, 2, 1, 0)]
    rows = []
    for r in range(21):
        rows.append("".join(
            "1" if bits[(r * 21 + c) % len(bits)] else "0"
            for c in range(21)))
    low = name.lower()
    vowels = sum(c in "aeiou" for c in low)
    return {"matrix": rows,
            "matrix_sha": sha[:16],
            "dims": {
                "length": len(name),
                "length_class": ("short" if len(name) < 6
                                 else "mid" if len(name) < 12
                                 else "long"),
                "vowel_ratio": round(
                    vowels / max(len(low), 1), 3),
                "prefix": re.split(r"[_\-\d]", low)[0],
                "has_digits": any(c.isdigit()
                                  for c in name),
                "underscore": "_" in name,
                "uppercase": sum(c.isupper()
                                 for c in name)}}


def _harvest() -> dict[str, set]:
    """name -> set of sources it appears in."""
    hall = {}

    def add(name, src):
        n = (name or "").strip()
        if n and len(n) < 200:
            hall.setdefault(n, set()).add(src)

    # familytree nodes + labels
    try:
        import familytree
        for k, lab in familytree.LABEL.items():
            add(k, "familytree")
            add(lab, "familytree")
    except Exception:
        pass
    # termlib terms
    for r in _jsonl(DATA / "termlib" / "terms.jsonl") or []:
        add(r.get("term"), "termlib")
    # sea blocks
    for r in _jsonl(DATA / "sea" / "index.jsonl") or []:
        add(r.get("name"), "sea")
    # decompile: rooms + object names
    for r in _jsonl(DATA / "decompile" /
                    "room_instances.jsonl") or []:
        add(r.get("name"), "decompile_room")
        for inst in r.get("instances", []):
            add(inst.get("obj_name"), "decompile_obj")
    # audio census names
    for r in _jsonl(DATA / "decompile" /
                    "decompile_index.jsonl") or []:
        if r.get("kind") == "audio_census":
            for n in r.get("all_names", []):
                add(n, "audio_census")
    # sprite names + texture files (visual layer)
    for r in _jsonl(DATA / "decompile" /
                    "sprites.jsonl") or []:
        add(r.get("name"), "sprite")
    tdir = DATA / "decompile" / "textures"
    if tdir.exists():
        for p in tdir.glob("*.png"):
            add(p.stem, "texture")
    # voice refs + wavs
    vr = DATA / "voice_refs"
    if vr.exists():
        for p in vr.iterdir():
            add(p.stem, "voice_ref")
    sv = DATA / "sea" / "voice"
    if sv.exists():
        for p in sv.glob("*.wav"):
            add(p.stem, "voice_out")
    # truename records
    for r in _jsonl(DATA / "truename" / "names.jsonl") \
            or []:
        for k in ("name", "true_name", "key"):
            add(r.get(k), "truename")
    # handle stubs (@interstellar etc)
    for r in _jsonl(DATA / "handles" / "stubs.jsonl")             or []:
        add(r.get("handle"), "handle_stub")
        add(r.get("slug"), "handle_stub")
    # alias registry — equivalence pairs
    for r in _jsonl(DATA / "aliases.jsonl") or []:
        add(r.get("canonical"), "alias")
        add(r.get("alias"), "alias")
    # external clones/pages registry
    for r in _jsonl(DATA / "external" /
                    "registry.jsonl") or []:
        add(r.get("name"), "external")
        u = r.get("url") or ""
        if "/" in u:
            add(u.rstrip("/").split("/")[-1],
                "external")
    # modelchars roster
    for r in _jsonl(DATA / "modelchars" /
                    "roster.jsonl") or []:
        add(r.get("canonical"), "modelchar")
        for v in r.get("variants", []):
            add(v, "modelchar")
    # bankweb: world banking + emerging fintech pages
    for r in _jsonl(DATA / "bankweb" / "pages.jsonl") \
            or []:
        if r.get("ok"):
            add(r.get("name"), "bankweb")
            add(r.get("system"), "bankweb")
            add(r.get("region"), "bankweb")
            add(r.get("title"), "bankweb")
    # celestial sweep: queries + hit titles/domains
    for fn in ("sweep.jsonl", "wiki.jsonl"):
        for r in _jsonl(DATA / "celestial" / fn) or []:
            add(r.get("query"), "celestial_" +
                fn.split(".")[0])
            for h in r.get("results", []):
                add(h.get("title"), "celestial_" +
                    fn.split(".")[0])
                u = h.get("url") or ""
                m = re.match(r"https?://([^/]+)", u)
                if m:
                    add(m.group(1), "celestial_" +
                        fn.split(".")[0])
    return hall


def build() -> dict:
    hall = _harvest()
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    with HALL.open("w") as f:
        for name, srcs in sorted(hall.items()):
            rec = {"kind": "hall_name",
                   "name": name,
                   "sources": sorted(srcs),
                   "n_sources": len(srcs),
                   "dots": dot_dimensions(name),
                   "device_time": _now()}
            f.write(json.dumps(rec, ensure_ascii=False)
                    + "\n")
            n += 1
    idx = {"kind": "namehall_index",
           "device_time": _now(),
           "space": "library_of_babel",
           "alias": "memory_hall_of_memories",
           "names": n,
           "out": str(HALL.relative_to(ROOT))}
    (OUT / "index.json").write_text(
        json.dumps(idx, indent=2))
    try:
        import eventbus
        eventbus.emit("namehall_build",
                      {"names": n})
    except Exception:
        pass
    return {**idx, "built": True}


def query(name: str) -> dict:
    """Name query — exact match + dot-dimension
    neighbors (names sharing matrix_sha prefix or
    structural dims)."""
    q = (name or "").strip()
    recs = list(_jsonl(HALL) or [])
    if not recs:
        return {"error": "hall_empty", "hint": "build()"}
    exact = [r for r in recs if r["name"].lower()
             == q.lower()]
    dots = dot_dimensions(q)
    # neighbors: same prefix dim + similar length/vowel
    neigh = []
    for r in recs:
        rd = r["dots"]["dims"]
        if r["name"].lower() == q.lower():
            continue
        score = 0
        if rd["prefix"] == dots["dims"]["prefix"]:
            score += 2
        if rd["length_class"] == dots["dims"]["length_class"]:
            score += 1
        if abs(rd["vowel_ratio"] -
               dots["dims"]["vowel_ratio"]) < 0.15:
            score += 1
        if score >= 3:
            neigh.append({"name": r["name"],
                          "sources": r["sources"],
                          "score": score})
    neigh.sort(key=lambda x: -x["score"])
    out = {"kind": "namehall_query",
           "device_time": _now(),
           "query": q, "space": "library_of_babel",
           "exact": exact[0] if exact else None,
           "dot_dimensions": dots,
           "neighbors": neigh[:10],
           "namebriger": ("dot-dimension bridge — names "
                          "identified by matrix+dims, "
                          "not just strings")}
    try:
        import eventbus
        eventbus.emit("namehall_query",
                      {"query": q,
                       "exact": bool(exact)})
    except Exception:
        pass
    return out


def status() -> dict:
    recs = list(_jsonl(HALL) or [])
    return {"kind": "namehall_state",
            "device_time": _now(),
            "space": "library_of_babel",
            "alias": "memory_hall_of_memories",
            "names": len(recs),
            "note": ("name query space — every known name "
                     "indexed with dot dimensions for "
                     "identification")}
