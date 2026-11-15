"""Term library — ingest power/descriptor terms from the web
(datamuse thesaurus API, no key), categorize them, and apply
selections as variant nodes across the family tree.

Categories: power, divine, combat, mental, mood, archetype.
apply_selection() registers <node>__<term> hidden variant nodes
so truename/proximity/branches pick them up — 'different
selection' = choose terms per node/branch.

Store: data/termlib/terms.jsonl + applied.jsonl
"""

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "termlib"
TERMS = OUT / "terms.jsonl"
APPLIED = OUT / "applied.jsonl"
UA = {"User-Agent": "atlas-termlib/1.0"}

# seed terms — user's list + expansions; each seeds a datamuse
# related-terms pull. category guides classification.
SEEDS = {
    "power": ["extreme", "impossible", "absolute", "unbeatable",
              "unstoppable", "omnipotent", "invincible",
              "transcendent", "supreme", "apex", "primordial",
              "infinite", "eternal", "ultimate"],
    "divine": ["god", "lord", "deity", "divine", "sovereign",
               "celestial", "seraph", "cherub", "numinous",
               "holy", "sacred", "blessed"],
    "combat": ["blade", "works", "archer", "sword", "lance",
               "knife", "spear", "bow", "dagger", "saber",
               "katana", "weapon", "warrior", "hunter"],
    "mental": ["omniscient", "skills", "copy", "mimic",
               "hyperfixation", "obsession", "maniac",
               "savant", "genius", "prophecy", "foresight",
               "intuition", "psion"],
    "mood": ["moods", "melancholy", "euphoria", "rage",
             "serenity", "dread", "awe", "ecstasy", "fury",
             "tranquility", "mania", "despair"],
    "archetype": ["monarch", "emperor", "king", "tyrant",
                  "warlord", "overlord", "regent", "czar",
                  "shogun", "kaiser", "sultan", "shah"],
}

CATEGORIES = {"power", "divine", "combat", "mental", "mood",
              "archetype"}


def _datamuse(seed: str, max_results: int = 30) -> list[str]:
    """Datamuse related-terms pull (free, no key)."""
    out = []
    for rel in ("rel_trg", "ml"):
        try:
            u = ("https://api.datamuse.com/words?"
                 f"{rel}={urllib.parse.quote(seed)}"
                 f"&max={max_results}")
            req = urllib.request.Request(u, headers=UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                for w in json.loads(r.read()):
                    t = w.get("word")
                    if t and t.isalpha() and 3 <= len(t) <= 20:
                        out.append(t.lower())
        except Exception:
            continue
    return out


def ingest(per_seed: int = 25) -> dict:
    """Pull web term lists for every seed; dedupe + categorize."""
    OUT.mkdir(parents=True, exist_ok=True)
    seen = set()
    if TERMS.exists():
        for line in TERMS.open():
            try:
                seen.add(json.loads(line)["term"])
            except Exception:
                pass
    added = 0
    cats = {}
    with TERMS.open("a") as f:
        for cat, seeds in SEEDS.items():
            for seed in seeds:
                for t in _datamuse(seed, per_seed):
                    if t in seen:
                        continue
                    seen.add(t)
                    rec = {"ts": time.time(), "kind": "term",
                           "term": t, "seed": seed,
                           "category": cat, "derived": True}
                    f.write(json.dumps(rec) + "\n")
                    added += 1
                    cats[cat] = cats.get(cat, 0) + 1
    return {"added": added, "total": len(seen),
            "by_category": cats}


def list_terms(category: str | None = None,
               limit: int = 0) -> list[dict]:
    recs = []
    if TERMS.exists():
        for line in TERMS.open():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if category and r.get("category") != category:
                continue
            recs.append(r)
    if limit:
        recs = recs[:limit]
    return recs


def _slug(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_")


def apply_selection(node: str, terms: list[str]) -> dict:
    """Apply a term selection to a node: registers
    <node>__<term> hidden variant nodes + applied records."""
    import familytree
    node = node.lower().replace(" ", "_")
    if node not in familytree.LABEL:
        return {"node": node, "applied": False,
                "error": "unknown node"}
    base = node.split("__")[0]
    OUT.mkdir(parents=True, exist_ok=True)
    added = []
    known = {r["term"]: r for r in list_terms()}
    with APPLIED.open("a") as f:
        for t in terms:
            t = t.lower().strip()
            if not t or not t.isalpha():
                continue
            vname = f"{node}__{_slug(t)}"
            cat = known.get(t, {}).get("category", "custom")
            rec = {"ts": time.time(), "kind": "term_variant",
                   "node": vname, "base": node,
                   "root_base": base, "term": t,
                   "category": cat, "derived": True}
            f.write(json.dumps(rec) + "\n")
            familytree.LABEL[vname] = \
                f"{familytree.LABEL.get(node, node)} [{t}]"
            familytree.EMOJI[vname] = familytree.EMOJI.get(
                base, "🔤")
            familytree.PARENT[vname] = node
            familytree.HIDDEN[vname] = {
                "emoji": familytree.EMOJI.get(base, "🔤"),
                "label": f"{t} variant",
                "note": f"term:{t} category:{cat}"}
            added.append(vname)
    return {"node": node, "applied": len(added),
            "variants": added[:10],
            "terms": terms}


def apply_branch(branch: str, terms: list[str],
                limit: int = 5) -> dict:
    """Apply the same term selection across a whole branch —
    first `limit` nodes of the branch."""
    import familytree
    kids = [k for k in familytree.TREE.get(branch, [])
            if "__" not in k]
    out = []
    for n in kids[:limit]:
        out.append(apply_selection(n, terms))
    return {"branch": branch, "nodes": len(out),
            "results": out}


def status() -> dict:
    def count(p):
        return sum(1 for _ in p.open()) if p.exists() else 0
    cats = {}
    for r in list_terms():
        cats[r["category"]] = cats.get(r["category"], 0) + 1
    return {"terms": count(TERMS), "applied": count(APPLIED),
            "categories": cats, "seeds": sum(
                len(v) for v in SEEDS.values())}
