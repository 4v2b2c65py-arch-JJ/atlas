"""memehall — the meme hall: a query space for the
culture layer.

Where namehall indexes every NAME, memehall indexes every
MEME — coined vocabulary, handle stubs, raw vocab streams,
inside jokes, user-injected culture. Each entry carries
dot dimensions (reuses namehall's matrix+dims signature)
plus meme dims: coined flag, category, source count.

Sources swept: termlib coined_* categories, handle
stubs, vocab stream sea blocks, celestial sweep queries.

Store: data/memehall/hall.jsonl + index.json
"""

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "memehall"
HALL = OUT / "hall.jsonl"

import namehall


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


def _harvest() -> dict[str, dict]:
    """meme text -> {sources, categories, raw}."""
    hall = {}

    def add(text, src, cat=None, raw=None):
        t = (text or "").strip()
        if not t or len(t) > 400:
            return
        e = hall.setdefault(t, {"sources": set(),
                                "categories": set(),
                                "raw": None})
        e["sources"].add(src)
        if cat:
            e["categories"].add(cat)
        if raw and not e["raw"]:
            e["raw"] = raw[:2000]

    # coined vocabulary — every coined_* termlib category
    for r in _jsonl(DATA / "termlib" / "terms.jsonl") or []:
        cat = r.get("category") or ""
        if cat.startswith("coined") or cat in (
                "coined_combat", "coined_tech",
                "coined_handle", "coined_meme",
                "coined_parsec", "coined_golb",
                "coined_audio", "coined_celestial"):
            add(r.get("term"), "termlib", cat)

    # handle stubs — @interstellar and friends
    for r in _jsonl(DATA / "handles" / "stubs.jsonl") \
            or []:
        add(r.get("handle"), "handle_stub", "handle")
        add(r.get("slug"), "handle_stub", "handle")

    # raw vocab streams — sea blocks preserving user
    # streams verbatim (the jokes live in the raw)
    for r in _jsonl(DATA / "sea" / "index.jsonl") or []:
        nm = r.get("name") or ""
        if nm.startswith("vocab_stream") or \
                "celestial" in nm:
            blk = DATA / "sea" / (r.get("slug", "") +
                                  ".json")
            raw = None
            if blk.exists():
                try:
                    raw = json.loads(
                        blk.read_text()).get("raw")
                except Exception:
                    pass
            add(nm, "sea_block", "stream", raw=raw)

    # celestial sweep queries — the combination space
    # itself is a meme artifact (73 coined queries)
    for fn in ("sweep.jsonl", "wiki.jsonl"):
        for r in _jsonl(DATA / "celestial" / fn) or []:
            add(r.get("query"), "celestial_" +
                fn.split(".")[0], "query")

    # gaster observation reports — the machine's own
    # deadpan judgments are meme-adjacent
    for p in (DATA / "gaster" / "reports").glob("*.json") \
            if (DATA / "gaster" / "reports").exists() \
            else []:
        try:
            rep = json.loads(p.read_text())
            add(rep.get("gaster_judgment"),
                "gaster_report", "judgment")
        except Exception:
            pass

    # termlib packs — named vocab collections
    for p in (DATA / "termlib").glob("*.json"):
        try:
            pk = json.loads(p.read_text())
            if pk.get("kind") == "termlib_pack":
                add(pk.get("name"), "termlib_pack",
                    "pack")
        except Exception:
            pass

    return hall


def _meme_dims(entry: dict, text: str) -> dict:
    """Meme-specific dims on top of namehall dots."""
    low = text.lower()
    return {
        "coined": any(c.startswith("coined")
                      for c in entry["categories"]),
        "handle": "handle" in
                  " ".join(entry["categories"]),
        "stream": "stream" in
                  " ".join(entry["categories"]),
        "query": "query" in
                 " ".join(entry["categories"]),
        "judgment": "judgment" in
                    " ".join(entry["categories"]),
        "n_sources": len(entry["sources"]),
        "has_symbol": bool(re.search(r"[@=+/]", text)),
        "exclaims": "!" in text or ";)" in text,
        "length_class": ("quip" if len(text) < 15
                         else "line" if len(text) < 60
                         else "block")}


def build() -> dict:
    hall = _harvest()
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    with HALL.open("w") as f:
        for text, e in sorted(hall.items()):
            rec = {"kind": "meme_entry",
                   "text": text,
                   "sources": sorted(e["sources"]),
                   "categories": sorted(e["categories"]),
                   "raw": e["raw"],
                   "dots": namehall.dot_dimensions(text),
                   "meme_dims": _meme_dims(e, text),
                   "device_time": _now()}
            f.write(json.dumps(rec, ensure_ascii=False)
                    + "\n")
            n += 1
    idx = {"kind": "memehall_index",
           "device_time": _now(),
           "space": "meme_hall",
           "alias": "meme_halls",
           "memes": n,
           "out": str(HALL.relative_to(ROOT))}
    (OUT / "index.json").write_text(
        json.dumps(idx, indent=2))
    try:
        import eventbus
        eventbus.emit("memehall_build", {"memes": n})
    except Exception:
        pass
    return {**idx, "built": True}


def query(text: str) -> dict:
    """Meme query — exact + dot-dimension neighbors +
    same-category relatives."""
    q = (text or "").strip()
    recs = list(_jsonl(HALL) or [])
    if not recs:
        return {"error": "hall_empty", "hint": "build()"}
    exact = [r for r in recs
             if r["text"].lower() == q.lower()]
    dots = namehall.dot_dimensions(q)
    neigh = []
    for r in recs:
        if r["text"].lower() == q.lower():
            continue
        rd = r["dots"]["dims"]
        md = r["meme_dims"]
        score = 0
        if rd["prefix"] == dots["dims"]["prefix"]:
            score += 2
        if rd["length_class"] == dots["dims"][
                "length_class"]:
            score += 1
        if abs(rd["vowel_ratio"] -
               dots["dims"]["vowel_ratio"]) < 0.15:
            score += 1
        if set(r["categories"]) & \
                set(exact[0]["categories"]) if exact \
                else False:
            score += 1
        if md["coined"] and score >= 2:
            neigh.append({"text": r["text"],
                          "categories": r["categories"],
                          "score": score})
    neigh.sort(key=lambda x: -x["score"])
    out = {"kind": "memehall_query",
           "device_time": _now(),
           "query": q, "space": "meme_hall",
           "exact": exact[0] if exact else None,
           "dot_dimensions": dots,
           "neighbors": neigh[:10],
           "note": ("meme hall — culture layer indexed "
                    "by dots + categories")}
    try:
        import eventbus
        eventbus.emit("memehall_query",
                      {"query": q, "exact": bool(exact)})
    except Exception:
        pass
    return out


def status() -> dict:
    recs = list(_jsonl(HALL) or [])
    cats = {}
    for r in recs:
        for c in r.get("categories", []):
            cats[c] = cats.get(c, 0) + 1
    return {"kind": "memehall_state",
            "device_time": _now(),
            "space": "meme_hall",
            "memes": len(recs),
            "categories": cats}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "query":
        print(json.dumps(query(" ".join(
            sys.argv[2:])), indent=1)[:2000])
    elif len(sys.argv) > 1 and sys.argv[1] == "build":
        print(json.dumps(build(), indent=1))
    else:
        print(json.dumps(status(), indent=1))
