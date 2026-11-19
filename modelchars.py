"""modelchars — roster of new model characters.

User-supplied character names with spelling variants,
preserved verbatim in sea (model_chars_001) and parsed
here into a roster. TILDA SYNTAX: each character's name
set is expressed as ~Tok1~Tok2~...~ — every ~...~ segment
is isalpha()-verified before acceptance, so the tilda
form IS the alphabetic check. Non-alpha tokens are
rejected and recorded, never silently dropped.

  build()   — parse roster, write data/modelchars/
  query(n)  — exact + tilda-variant lookup
  status()  — census
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "modelchars"
ROSTER = OUT / "roster.jsonl"

# raw user stream — preserved verbatim, one character
# per line; '-'/space separated tokens after the first
# are spelling variants of the same character.
RAW = """Mitiore
Moonshine
Caprion
Capricon
Tetrion
Vastro
Limeos
Diestro
Stratos
Pantheon
Histra
Inkun
Tetrinome
Gnome
Baskils
Marbles
Skipstone
Bouncer
Random
Ranter
Silabus
Pluribius
Asoteric
Mithra
Mythos
Sliderin
Nimia
Enigma
Saturus - satoros-saturos
Mentman
Mentwam
Magma-Magmi
Sygils-sagilis
Saturans-zantus
Bibliac - T
Prosian-prospectus
Enteisla-enteiasla-enteislam
Zaket-zekey-zaky-sakaid-sacaidas
Hobitius
Zakren
Satens
Emiras -Emidas
Quasta-qsteair
Hesti-hastra
Archivium
Nounstram
Damtiz
Aston-Ishadment
Damions
Momentous
Compodium-compadite-compound
Ezqil
Xguild"""


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


def _alpha(tok: str) -> bool:
    """Proper alphabetic check — unicode letters only,
    no digits/symbols/whitespace."""
    return bool(tok) and tok.isalpha()


def tilda(tokens: list[str]) -> str:
    """Tilda syntax: ~Tok1~Tok2~... — each segment is an
    isalpha-verified alphabetic token."""
    return "".join("~" + t for t in tokens if _alpha(t))


def parse(raw: str = RAW) -> dict:
    """Line -> character. Tokens after the canonical
    name are variants. Every token must pass isalpha —
    rejects are recorded with their line."""
    chars = []
    rejected = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # tokens: split on whitespace and hyphens
        toks = [t for t in re.split(r"[\s\-]+", line)
                if t]
        good = [t for t in toks if _alpha(t)]
        bad = [t for t in toks if not _alpha(t)]
        for b in bad:
            rejected.append({"line": line,
                             "token": b})
        if not good:
            continue
        chars.append({
            "canonical": good[0],
            "names": good,
            "variants": good[1:],
            "n_variants": len(good) - 1,
            "tilda": tilda(good),
            "raw_line": line})
    return {"characters": chars,
            "rejected": rejected,
            "n_characters": len(chars),
            "n_names": sum(len(c["names"])
                           for c in chars)}


def build() -> dict:
    p = parse()
    OUT.mkdir(parents=True, exist_ok=True)
    with ROSTER.open("w") as f:
        for c in p["characters"]:
            f.write(json.dumps({
                "kind": "model_char",
                "device_time": _now(),
                **c}, ensure_ascii=False) + "\n")
    idx = {"kind": "modelchars_index",
           "device_time": _now(),
           "space": "model_characters",
           "syntax": "tilda",
           "characters": p["n_characters"],
           "names": p["n_names"],
           "rejected": len(p["rejected"]),
           "out": str(ROSTER.relative_to(ROOT))}
    (OUT / "index.json").write_text(
        json.dumps(idx, indent=2))
    # termlib: every alphabetic name token, coined_chars
    import time as _t
    existing = set()
    for r in _jsonl(DATA / "termlib" / "terms.jsonl") \
            or []:
        existing.add(r.get("term"))
    n = 0
    with open(DATA / "termlib" / "terms.jsonl", "a") \
            as f:
        for c in p["characters"]:
            for nm in c["names"]:
                key = nm.lower()
                if key in existing:
                    continue
                f.write(json.dumps({
                    "ts": _t.time(), "kind": "term",
                    "term": key,
                    "seed": "model_chars_001",
                    "category": "coined_chars",
                    "canonical": c["canonical"],
                    "derived": True},
                    ensure_ascii=False) + "\n")
                existing.add(key)
                n += 1
    try:
        import eventbus
        eventbus.emit("modelchars_build",
                      {"characters": p["n_characters"]})
    except Exception:
        pass
    return {**idx, "terms_added": n,
            "built": True}


def query(name: str) -> dict:
    q = (name or "").strip().lower()
    recs = list(_jsonl(ROSTER) or [])
    if not recs:
        return {"error": "roster_empty",
                "hint": "build()"}
    exact = [r for r in recs
             if r["canonical"].lower() == q
             or q in [v.lower()
                      for v in r["variants"]]]
    # tilda-form neighbors: same-length or shared
    # prefix families
    neigh = []
    for r in recs:
        if r in exact:
            continue
        score = 0
        if r["canonical"][0].lower() == q[:1].lower():
            score += 1
        if abs(len(r["canonical"]) - len(q)) <= 2:
            score += 1
        shared = set(r["canonical"].lower()) & \
            set(q)
        if len(shared) >= 3:
            score += 1
        if score >= 2:
            neigh.append({"canonical": r["canonical"],
                          "tilda": r["tilda"],
                          "score": score})
    neigh.sort(key=lambda x: -x["score"])
    return {"kind": "modelchars_query",
            "device_time": _now(),
            "query": q,
            "syntax": "tilda",
            "exact": exact[0] if exact else None,
            "neighbors": neigh[:10]}


def status() -> dict:
    recs = list(_jsonl(ROSTER) or [])
    variants = sum(r.get("n_variants", 0)
                   for r in recs)
    return {"kind": "modelchars_state",
            "device_time": _now(),
            "space": "model_characters",
            "syntax": "tilda ~Tok~Tok~",
            "characters": len(recs),
            "variant_names": variants,
            "note": ("new model character roster — "
                     "isalpha-verified tilda forms")}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        print(json.dumps(build(), indent=1))
    elif len(sys.argv) > 1 and sys.argv[1] == "query":
        print(json.dumps(query(" ".join(
            sys.argv[2:])), indent=1)[:2000])
    else:
        print(json.dumps(status(), indent=1))
