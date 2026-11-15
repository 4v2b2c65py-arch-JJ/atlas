"""Mod library — unique mechanisms applicable to ALL scraped
pages, discovered/enriched automatically.

Two layers:
  1. Built-in detectors — regex/heuristic mechanisms run over
     every scrap record (platform, region, edition, version, dlc,
     demo, language, normalization key, cross-site duplicate).
  2. Model discovery — llama3.2 proposes additional mechanisms
     from a sample of real titles; proposals are recorded as
     'proposed' until a detector implements them.

Mods enrich data/dlps/scraps.jsonl records in place? NO — mods
write sidecar enrichments to data/mods/enriched.jsonl so the
original scraps stay verbatim.
"""

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "mods"
ENRICHED = OUT / "enriched.jsonl"
DISCOVERED = OUT / "discovered.jsonl"

# ---- built-in mechanisms (each = one mod, applies to all pages)
MODS = {
    "platform_detect": {
        "desc": "platform from site + category + url slug",
        "applies": "all"},
    "region_detect": {
        "desc": "USA/EUR/JAP/ASIA region tags in title",
        "applies": "all"},
    "edition_detect": {
        "desc": "GOTY/Definitive/Enhanced/Complete/Anniversary",
        "applies": "all"},
    "version_detect": {
        "desc": "firmware/update version tokens (v1.xx, update)",
        "applies": "all"},
    "dlc_demo_detect": {
        "desc": "DLC/demo/beta/deluxe flags",
        "applies": "all"},
    "language_detect": {
        "desc": "language tags (multi-language, english, french)",
        "applies": "all"},
    "norm_key": {
        "desc": "normalized title key for dedupe/search",
        "applies": "all"},
    "cross_site_dup": {
        "desc": "same title on multiple DLPS sites = stronger "
                "candidate",
        "applies": "all"},
    "genre_hint": {
        "desc": "genre from pc category slug or title keywords",
        "applies": "all"},
    "series_cluster": {
        "desc": "strip numbered suffix -> series/franchise key",
        "applies": "all"},
}

REGION_RE = re.compile(
    r"(?i)\b(usa|us|eur|europe|jap|japan|asia|asia-palm|pal|"
    r"cib|cia|korea|kr|en|fr|de|es|it|nl|pl|ru|br|latin)\b")
EDITION_RE = re.compile(
    r"(?i)\b(goty|game of the year|definitive|enhanced|complete|"
    r"anniversary|deluxe|premium|ultimate|collection|remastered|"
    r"hd|vr)\b")
VERSION_RE = re.compile(r"(?i)\b(v?\d+\.\d+([\.\d]*)?|update|"
                        r"firmware|patch)\b")
DLC_RE = re.compile(r"(?i)\b(dlc|demo|beta|trial|deluxe|"
                    r"season pass|add-?on)\b")
LANG_RE = re.compile(
    r"(?i)\b(multi-?language|multilang|english|french|german|"
    r"spanish|italian|russian|japanese|korean|chinese|"
    r"pt-?br|latino)\b")
GENRE_WORDS = {
    "action": ("war", "fight", "combat", "shooter", "action"),
    "rpg": ("rpg", "quest", "dragon", "final fantasy", "persona",
            "kingdom hearts", "mass effect", "witcher"),
    "racing": ("racing", "racer", "need for speed", "f1 ", "gt ",
               "gran turismo", "burnout"),
    "sports": ("fifa", "nba", "nfl", "mlb", "nhl", "pes ",
               "pro evolution", "madden", "f1 ", "wwe", "ufc"),
    "horror": ("dead space", "resident evil", "silent hill",
               "evil within", "outlast", "amnesia", "layers"),
    "puzzle": ("puzzle", "portal", "tetris", "monument", "babysit"),
    "indie": ("indie", "celeste", "hollow knight", "stardew",
              "undertale", "deltarune", "dead cells", "hades"),
}


def _norm(title: str) -> str:
    t = re.sub(r"[^a-z0-9 ]", "", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def _series(title: str) -> str:
    """Series/franchise key — drop trailing numbers/volumes."""
    t = _norm(title)
    t = re.sub(r"\b(v?\d+|part\s*\d+|chapter\s*\d+|"
               r"episode\s*\d+|s\d+e\d+|\d{4})\b.*$", "", t)
    return t.strip()


def apply_mods(records: list[dict]) -> list[dict]:
    """Run every built-in mod over the records; returns enriched
    copies (originals untouched)."""
    # cross-site duplicate index first
    title_sites = {}
    for r in records:
        k = _norm(r.get("title", ""))
        title_sites.setdefault(k, set()).add(r.get("site", "?"))
    out = []
    for r in records:
        t = r.get("title", "")
        tl = t.lower()
        e = dict(r)
        # platform
        e["platform"] = (r.get("cat") or "").split("-")[0] or \
            r.get("site", "?")
        # region
        e["region"] = sorted(set(m.group(0).lower() for m in
                                 REGION_RE.finditer(t)))
        # edition
        e["edition"] = sorted(set(m.group(0).lower() for m in
                                  EDITION_RE.finditer(t)))
        # version
        e["version"] = sorted(set(m.group(0) for m in
                                  VERSION_RE.finditer(t)))[:4]
        # dlc/demo
        e["dlc_flags"] = sorted(set(m.group(0).lower() for m in
                                    DLC_RE.finditer(t)))
        # language
        e["languages"] = sorted(set(m.group(0).lower() for m in
                                    LANG_RE.finditer(t)))
        # norm + series keys
        e["norm_key"] = _norm(t)
        e["series_key"] = _series(t)
        # cross-site dup
        e["sites_count"] = len(title_sites.get(e["norm_key"],
                                               {r.get("site")}))
        e["cross_site_dup"] = e["sites_count"] > 1
        # genre hint
        e["genre"] = next(
            (g for g, words in GENRE_WORDS.items()
             if any(w in tl for w in words)), None)
        # mod coverage marker
        e["mods_applied"] = sorted(MODS)
        e["enriched_ts"] = time.time()
        out.append(e)
    return out


def enrich_file() -> dict:
    """Enrich all DLPS scraps -> enriched.jsonl (append new only)."""
    import dlps
    scraps = dlps.titles()
    seen = set()
    if ENRICHED.exists():
        for line in ENRICHED.read_text().splitlines():
            try:
                seen.add(json.loads(line).get("url"))
            except Exception:
                pass
    fresh = [r for r in scraps if r.get("url") not in seen]
    OUT.mkdir(parents=True, exist_ok=True)
    n = 0
    for e in apply_mods(fresh):
        with ENRICHED.open("a") as f:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
        n += 1
    return {"scraps": len(scraps), "enriched_new": n,
            "total_enriched": len(scraps) - len(fresh) + n}


def model_discover(sample: int = 40) -> dict:
    """Auto-discovery via the local model: show it real titles and
    ask for unique mechanisms applicable to ALL pages. Proposals
    are recorded, not auto-implemented."""
    import dlps
    import llm
    scraps = dlps.titles()[:sample]
    titles = [r["title"] for r in scraps]
    prompt = (
        "You are analyzing a game-title catalog scraper. Given "
        "these real titles, list UNIQUE detection mechanisms that "
        "could apply to EVERY title (not just these). One per "
        "line, format 'MOD: <name> — <what it detects>'. Max 12.\n"
        "Titles:\n" + "\n".join(titles[:sample]))
    try:
        out = llm._chat("llama3.2:latest", prompt, timeout=270)
    except Exception as e:
        out = f"MOD: detection_failed — {str(e)[:100]}"
    proposals = []
    for line in out.splitlines():
        line = line.strip().lstrip("-*•0123456789. ")
        if line.upper().startswith("MOD:"):
            proposals.append(line[4:].strip())
    OUT.mkdir(parents=True, exist_ok=True)
    for p in proposals:
        with DISCOVERED.open("a") as f:
            f.write(json.dumps({"ts": time.time(),
                                "proposal": p,
                                "status": "proposed",
                                "model": "llama3.2:latest"},
                               ensure_ascii=False) + "\n")
    return {"proposals": proposals, "count": len(proposals)}


def list_mods() -> dict:
    """The mod library: built-ins + model proposals."""
    props = []
    if DISCOVERED.exists():
        props = [json.loads(l) for l in DISCOVERED.read_text().splitlines()
                 if l.strip()]
    return {"built_in": [{"name": k, **v}
                         for k, v in MODS.items()],
            "built_in_count": len(MODS),
            "model_proposals": props,
            "model_proposal_count": len(props)}
