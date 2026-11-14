"""TOA-01 algo — preserved-structure parser.

The spec lives VERBATIM in toa_algo.txt. This module only reads it:
no rewriting, no reformatting. It extracts the indentation/section
skeleton, token pools, oo-delimited pairs, and resolves the barcode
binding back to the original source (archive.org ToA book item /
MangaDex title) for dataset expansion.
"""

import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "toa_algo.txt"

SECTIONS = ["Toa^", "Tom", "Math", "Completer", "Skill book",
            "Modifier"]


def load_raw() -> str:
    """The spec, byte-for-byte. Never edited by this module."""
    return SRC.read_text()


def parse() -> dict:
    """Indentation/section tree + token extraction. Read-only."""
    lines = load_raw().splitlines()
    tree = []
    current = None
    for i, ln in enumerate(lines):
        node = {"line": i + 1, "text": ln,
                "indent": len(ln) - len(ln.lstrip(" ")),
                "section": None, "tokens": []}
        stripped = ln.strip()
        if stripped in SECTIONS:
            node["section"] = stripped
            current = node
            tree.append(node)
            continue
        if current is not None:
            node["section"] = current["section"]
        # token pools: maximal runs of pool chars
        node["tokens"] = re.findall(r"[A-Za-z0-9^|@_>\-—]+", stripped)
        tree.append(node)
    return {"spec": "TOA-01", "source": "toa_algo.txt",
            "lines": tree,
            "sha256": hashlib.sha256(
                load_raw().encode()).hexdigest()[:16]}


def pools() -> dict:
    """Letter pools per oo-pair line — the cipher material."""
    out = []
    for ln in load_raw().splitlines():
        s = ln.strip()
        if "oo" not in s or not re.search(r"[—\-|_^]", s):
            continue
        left, _, right = s.partition("oo")
        # normalize the visual separators — keep raw, add cleaned
        clean_l = re.sub(r"[^A-Za-z0-9^|@_]", "", left)
        clean_r = re.sub(r"[^A-Za-z0-9^|@_]", "", right)
        out.append({"raw": s, "left": clean_l, "right": clean_r})
    return {"pairs": out, "count": len(out)}


def barcode_candidates() -> list[str]:
    """Tokens that could be the source-binding barcode.

    Candidates: the spec root 'Toa^', every distinct pool-left token,
    and the spec hash. Resolution tries each against the original
    sources (archive.org item, MangaDex title search)."""
    cands = {"Toa^", parse()["sha256"]}
    for p in pools()["pairs"]:
        for t in re.split(r"[^A-Za-z0-9^]+", p["left"]):
            if len(t) >= 3:
                cands.add(t)
    return sorted(cands)


def _http_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "atlas-toa/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def resolve_barcode(term: str) -> dict:
    """Resolve a barcode candidate to its original source.

    Checks: archive.org advancedsearch (the ToA book item),
    MangaDex title search. Returns the first verified hit —
    the barcode's 'original source' for the bigger dataset."""
    q = urllib.parse.quote(term)
    hits = []
    try:
        d = _http_json(
            "https://archive.org/advancedsearch.php?q="
            + q + "&fl[]=identifier&fl[]=title&rows=3"
            "&output=json")
        for doc in d.get("response", {}).get("docs", []):
            hits.append({"source": "archive.org",
                         "id": doc.get("identifier"),
                         "title": doc.get("title")})
    except Exception:
        pass
    try:
        d = _http_json(
            "https://api.mangadex.org/manga?title=" + q + "&limit=3")
        for m in d.get("data", []):
            hits.append({"source": "mangadex",
                         "id": m.get("id"),
                         "title": (m.get("attributes") or {})
                                  .get("title", {}).get("en")})
    except Exception:
        pass
    return {"term": term, "hits": hits}


def packet() -> dict:
    """Full TOA-01 packet: structure + pools + barcode resolution."""
    tree = parse()
    bc = []
    for c in barcode_candidates():
        r = resolve_barcode(c)
        if r["hits"]:
            bc.append(r)
    return {"spec": "TOA-01", "sha256": tree["sha256"],
            "sections": [n.get("section") for n in tree["lines"]
                         if n.get("section")],
            "line_count": len(tree["lines"]),
            "pool_pairs": pools()["count"],
            "barcode": bc[0] if bc else None,
            "barcode_candidates": barcode_candidates(),
            "raw_preserved": str(SRC)}


if __name__ == "__main__":
    print(json.dumps(packet(), indent=1)[:3000])
