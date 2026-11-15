"""Name consensus — parallel-reality mirror voting over name data.

Mirrors ("parallel realities") each observe the same name and vote
equal weight on its up-to-date status:

  us-ssa        SSA given-name records (data/names/ssa.jsonl)
  us-census     Census 2010 surname records
  wikidata      Wikidata given-name entities
  ddg-suggest   DuckDuckGo autocomplete hit-rate (live web)
  wiki-suggest  Wikipedia opensearch hit-rate (live web)

World-population scaling: hit-rate is normalized against scope
population proxies (census prop100k / sitelinks / suggestion rank)
to estimate global bearer counts.

Consensus timeline: every vote appends to
data/names/consensus_timeline.jsonl — metrics accumulate across
time so status is reinstated/up-to-date per name.

Nameplates: new combination entries land in
data/names/nameplates.new.jsonl — append-only, existing datasets
are never modified in place; GGUF repack merges them as weights.
"""

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import namescrape

ROOT = Path(__file__).parent
NAMES = ROOT / "data" / "names"
TIMELINE = NAMES / "consensus_timeline.jsonl"
PLATES = NAMES / "nameplates.new.jsonl"
UA = {"User-Agent": "atlas-consensus/1.0"}
WORLD_POP = 8_100_000_000

MIRRORS = ["us-ssa", "us-census", "wikidata", "ddg-suggest",
           "wiki-suggest"]


def _get_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ---------- live suggestion hit-rates ----------

def ddg_suggest(name: str) -> dict:
    """DuckDuckGo autocomplete — webengine suggestion hit-rate."""
    q = urllib.parse.quote(name)
    url = (f"https://duckduckgo.com/ac/?q={q}&type=list")
    try:
        data = _get_json(url)
    except Exception as e:
        return {"mirror": "ddg-suggest", "ok": False,
                "detail": str(e)[:120]}
    sugg = [s.get("phrase", "") for s in data if isinstance(s, dict)]
    hit = any(name.lower() in s.lower() for s in sugg)
    rank = next((i + 1 for i, s in enumerate(sugg)
                 if name.lower() in s.lower()), None)
    score = (1.0 / rank) if rank else 0.0
    return {"mirror": "ddg-suggest", "ok": True, "hit": hit,
            "rank": rank, "score": round(score, 4),
            "suggestions": sugg[:8]}


def wiki_suggest(name: str) -> dict:
    """Wikipedia opensearch — encyclopedia suggestion hit-rate."""
    q = urllib.parse.quote(name)
    url = ("https://en.wikipedia.org/w/api.php?action=opensearch"
           f"&search={q}&limit=8&namespace=0&format=json")
    try:
        data = _get_json(url)
    except Exception as e:
        return {"mirror": "wiki-suggest", "ok": False,
                "detail": str(e)[:120]}
    titles = data[1] if isinstance(data, list) and len(data) > 1 else []
    hit = any(name.lower() in t.lower() for t in titles)
    rank = next((i + 1 for i, t in enumerate(titles)
                 if name.lower() in t.lower()), None)
    score = (1.0 / rank) if rank else 0.0
    return {"mirror": "wiki-suggest", "ok": True, "hit": hit,
            "rank": rank, "score": round(score, 4),
            "suggestions": titles[:8]}


# ---------- dataset mirrors ----------

def _load_jsonl(name: str) -> list:
    p = NAMES / name
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines()
            if l.strip()]


def _find(recs: list, name: str) -> dict | None:
    n = name.lower()
    best = None
    for r in recs:
        rn = (r.get("name") or "").lower()
        if rn == n or n in rn or rn in n:
            if best is None or (r.get("count") or 0) > \
                    (best.get("count") or 0):
                best = r
    return best


def _scale(rec: dict | None, prop_key: str = "prop100k") -> float:
    """World-population scaling: local frequency -> estimated
    global bearers."""
    if not rec:
        return 0.0
    if rec.get("prop100k"):
        try:
            return float(rec["prop100k"]) / 1e5 * WORLD_POP / 100
        except Exception:
            pass
    if rec.get("count"):
        return float(rec["count"])
    if rec.get("sitelinks"):
        return float(rec["sitelinks"]) * 1e6
    return 0.0


def vote(name: str) -> dict:
    """All mirrors observe the name; equal-weight vote decides
    up-to-date status. Appends to the consensus timeline."""
    name = name.strip()
    votes = {}
    ssa = _find(_load_jsonl("ssa.jsonl"), name)
    cen = _find(_load_jsonl("census.jsonl"), name)
    wd = _find(_load_jsonl("wikidata.jsonl"), name)
    votes["us-ssa"] = {"status": "present" if ssa else "absent",
                       "record": ssa, "est_global": _scale(ssa)}
    votes["us-census"] = {"status": "present" if cen else "absent",
                          "record": cen, "est_global": _scale(cen)}
    votes["wikidata"] = {"status": "present" if wd else "absent",
                         "record": wd, "est_global": _scale(wd)}
    votes.update({})
    votes["ddg-suggest"] = ddg_suggest(name)
    votes["wiki-suggest"] = wiki_suggest(name)

    present = sum(1 for k, v in votes.items()
                  if v.get("status") == "present"
                  or v.get("hit"))
    consensus = ("established" if present >= 3 else
                 "emerging" if present >= 1 else "unseen")
    est = max((v.get("est_global") or 0.0 for v in votes.values()),
              default=0.0)
    rec = {"ts": time.time(), "name": name, "votes": votes,
           "present_mirrors": present, "consensus": consensus,
           "est_global_bearers": round(est)}
    TIMELINE.parent.mkdir(exist_ok=True)
    with TIMELINE.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def timeline_metrics() -> dict:
    """Consensus metrics across the timeline — per-name latest
    status plus aggregate counts."""
    if not TIMELINE.exists():
        return {"entries": 0, "names": {}}
    latest = {}
    n = 0
    for line in TIMELINE.read_text().splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        n += 1
        latest[r["name"]] = r
    by_consensus = {}
    for r in latest.values():
        by_consensus[r["consensus"]] = \
            by_consensus.get(r["consensus"], 0) + 1
    return {"entries": n, "names_tracked": len(latest),
            "by_consensus": by_consensus,
            "latest": list(latest.values())[-20:]}


def add_plate(plate: str, alias: str = "", combination: bool = True,
              note: str = "") -> dict:
    """Append a nameplate combination entry — .new file only,
    existing structures untouched."""
    entry = {"ts": time.time(), "plate": plate.strip().lower(),
             "alias": alias.strip().lower(), "combination":
             combination, "note": note, "src": "nameplate.new"}
    NAMES.mkdir(exist_ok=True)
    with PLATES.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # mirror into timeline as a vote so consensus sees it
    vote(entry["plate"] + (" " + entry["alias"]
                           if entry["alias"] else ""))
    return entry


def update_weights() -> dict:
    """Repack the GGUF dataset including nameplate.new entries —
    the name model's weights stay up-to-date. LLM weights are
    untouched (honest boundary: datasets, not network weights)."""
    recs = []
    for f in NAMES.glob("*.jsonl"):
        for line in f.read_text().splitlines():
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    # nameplates become weight entries
    for p in _load_jsonl("nameplates.new.jsonl"):
        recs.append({"name": p.get("plate", ""),
                     "alias": p.get("alias", ""),
                     "kind": "nameplate", "combination":
                     p.get("combination", True),
                     "src": "nameplate", "count": 1})
    res = namescrape.write_gguf(recs)
    res["verify"] = namescrape.read_gguf_count()
    return res


def status() -> dict:
    plates = _load_jsonl("nameplates.new.jsonl")
    return {"mirrors": MIRRORS,
            "timeline": timeline_metrics(),
            "plates": plates[-20:],
            "plates_total": len(plates),
            "gguf_records": namescrape.status().get("report", {})
            .get("sources", {}).get("deduped")}
