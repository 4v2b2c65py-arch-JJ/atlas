"""Partition — derive titles from live host magnet nodes, sort by
the Gaster playbook, store important pieces per profile.

Pipeline:
  live magnets (resolved/torrent_jobs/peer_urls/peer_hubs)
    -> dn= decode -> series base -> derive related titles
    -> gaster_sort: rarity tier via playbook judgment + grimware
       autonomy boost (grimware terms promote the piece)
    -> store into data/profiles/<profile>/partition/ — deduped,
       tier-sorted; puzzle/ holds the important pieces.

Rarity ladder (low->high): common, rare, unique, epic, legendary,
extraordinary, ex, sss. 'other' = unclassed, kept not dropped.
"""

import hashlib
import json
import re
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"

TIERS = ["common", "rare", "unique", "epic", "legendary",
         "extraordinary", "ex", "sss", "other"]
TIER_RANK = {t: i for i, t in enumerate(TIERS)}

GRIMWARE_TERMS = (
    "gaster", "flowey", "score", "miasma", "grimoire", "vanitas",
    "abyss", "toa", "determination", "soul", "door", "terrain",
    "undertale", "deltarune", "sans", "papyrus", "asriel",
    "true name", "blue moon", "clockwork", "spell", "mana",
    "magic", "fate", "destiny", "apocalypse", "genocide",
)

EP_TAG = re.compile(
    r"(?i)[.\s\-_]*(ep|episode|ch|chapter|part|pt|vol|volume|"
    r"season|s\d|arc)\s*\d+.*$")
QUALITY_TAG = re.compile(
    r"(?i)[.\s\-_]*(1080p|720p|480p|2160p|4k|hd|sd|bluray|"
    r"web-?dl|webrip|hdtv|x264|x265|hevc|aac|mp3|dual|sub|"
    r"eng|multi|complete|batch|uncensored).*$")


def _jload(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def live_magnets() -> list[dict]:
    """Harvest live magnet links + dn names from every local pool
    the mesh writes — resolved jobs, torrent jobs, peer URL pool,
    peer hub registry."""
    out = []
    seen = set()

    def add(uri: str, src: str, extra: dict | None = None):
        if not uri or not uri.startswith("magnet:") or uri in seen:
            return
        seen.add(uri)
        dn = ""
        m = re.search(r"[?&]dn=([^&]+)", uri)
        if m:
            dn = urllib.parse.unquote(m.group(1))
        out.append({"uri": uri, "dn": dn, "source": src,
                    **(extra or {})})

    for path, src in [
            (DATA / "resolved.json", "resolved"),
            (DATA / "torrent_jobs.json", "torrent_jobs")]:
        obj = _jload(path, {})
        items = obj.items() if isinstance(obj, dict) else []
        for k, v in items:
            add(k, src, {"meta": v if isinstance(v, dict) else {}})
    pool = _jload(DATA / "peer_urls.json", {})
    if isinstance(pool, dict):
        for k, v in pool.items():
            if isinstance(v, str):
                add(v, "peer_urls")
            elif isinstance(v, dict):
                add(v.get("url") or v.get("uri") or "",
                    "peer_urls", {"meta": v})
    elif isinstance(pool, list):
        for v in pool:
            add(v if isinstance(v, str) else
                (v.get("url") or v.get("uri") or ""), "peer_urls")
    hubs = _jload(DATA / "peer_hubs.json", [])
    if isinstance(hubs, list):
        for h in hubs:
            if isinstance(h, dict):
                for u in h.get("urls") or h.get("magnets") or []:
                    add(u, "peer_hub:" + str(h.get("name") or
                                              h.get("url", "?")))
    return out


def series_base(dn: str) -> str:
    """Reduce a magnet dn to its series/title base — strip episode,
    quality, language and release-group tags."""
    t = dn.strip()
    t = re.sub(r"\[[^\]]*\]", " ", t)          # [group] tags
    t = EP_TAG.sub("", t)
    t = QUALITY_TAG.sub("", t)
    t = re.sub(r"[._\-]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .-")
    return t


def live_nodes() -> list[dict]:
    """All live host nodes: magnet links AND peer-URL media
    entries — every node is a title basis for derivation."""
    nodes = live_magnets()
    pool = _jload(DATA / "peer_urls.json", {})
    if isinstance(pool, dict):
        for u, meta in pool.items():
            title = ""
            if isinstance(meta, dict):
                title = meta.get("title") or ""
            if title:
                nodes.append({"uri": u, "dn": title,
                              "source": "peer_urls:" +
                              str(meta.get("source", "?"))
                              if isinstance(meta, dict) else
                              "peer_urls"})
    # DLPS store scraps — the cusa-registered catalog is a live
    # title basis for derivation
    try:
        import dlps
        for r in dlps.titles():
            nodes.append({"uri": r["url"], "dn": r["title"],
                          "source": "dlps:" + r.get("cat", "?")})
    except Exception:
        pass
    return nodes


def derive_titles(magnets: list[dict] | None = None,
                  limit: int = 300) -> list[dict]:
    """Decode other titles from the live nodes: each dn/title
    reduces to a series base; bases cluster into derived titles."""
    mags = magnets if magnets is not None else live_nodes()
    bases = {}
    for m in mags:
        base = series_base(m.get("dn") or "")
        if len(base) < 3:
            continue
        bases.setdefault(base.lower(), {"title": base,
                                        "sources": [],
                                        "count": 0})
        bases[base.lower()]["sources"].append(m["source"])
        bases[base.lower()]["count"] += 1
    out = sorted(bases.values(), key=lambda b: -b["count"])
    return out[:limit]


def gaster_sort(item: dict) -> dict:
    """Gaster playbook judgment -> rarity tier. Grimware autonomy:
    grimware terms in the title promote the piece one tier; live
    swarm presence (multiple sources) adds evidence weight."""
    title = (item.get("title") or "").lower()
    score = 0
    # evidence: swarm count + source diversity
    score += min(item.get("count", 1), 5)
    score += min(len(set(item.get("sources", []))), 3)
    # grimware autonomy boost
    hits = [t for t in GRIMWARE_TERMS if t in title]
    score += len(hits) * 2
    # naming prestige markers
    if re.search(r"\b(the|dark|shadow|blood|abyss|origin|final|"
                 r"last|secret|hidden|lost)\b", title):
        score += 1
    # tier thresholds
    if score >= 12:
        tier = "sss"
    elif score >= 9:
        tier = "ex"
    elif score >= 7:
        tier = "extraordinary"
    elif score >= 5:
        tier = "legendary"
    elif score >= 4:
        tier = "epic"
    elif score >= 3:
        tier = "unique"
    elif score >= 2:
        tier = "rare"
    elif score >= 1:
        tier = "common"
    else:
        tier = "other"
    # grimware pieces never fall below 'epic' — they're puzzle pieces
    if hits and TIER_RANK[tier] < TIER_RANK["epic"]:
        tier = "epic"
    return {**item, "tier": tier, "score": score,
            "grimware_hits": hits,
            "puzzle_piece": bool(hits),
            "judgment": "gaster.playbook"}


def _pdir(profile: str) -> Path:
    d = DATA / "profiles" / profile / "partition"
    (d / "puzzle").mkdir(parents=True, exist_ok=True)
    return d


def _hash(title: str) -> str:
    return hashlib.sha256(title.strip().lower().encode()
                          ).hexdigest()[:16]


def store_partition(items: list[dict], profile: str = "default",
                    source: str = "magnet-derive") -> dict:
    """Store derived titles into the profile's local partition —
    deduped by title hash, tier-sorted, puzzle pieces split out."""
    d = _pdir(profile)
    store = d / "titles.jsonl"
    seen = set()
    if store.exists():
        for line in store.read_text().splitlines():
            try:
                seen.add(json.loads(line).get("hash"))
            except Exception:
                pass
    written = pieces = 0
    rows = []
    for it in items:
        judged = gaster_sort(it)
        h = _hash(judged["title"])
        if h in seen:
            continue
        seen.add(h)
        rec = {"hash": h, "ts": time.time(), "source": source,
               **judged}
        rows.append(rec)
        with store.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        written += 1
        if rec["puzzle_piece"]:
            pieces += 1
            with (d / "puzzle" / f"{h}.json").open("w") as f:
                json.dump(rec, f, indent=1,
                          ensure_ascii=False)
    rows.sort(key=lambda r: -TIER_RANK.get(r["tier"], 0))
    return {"profile": profile, "written": written,
            "puzzle_pieces": pieces,
            "partition": str(d),
            "top": [{"title": r["title"], "tier": r["tier"],
                     "score": r["score"]} for r in rows[:10]]}


def run(profile: str = "default") -> dict:
    nodes = live_nodes()
    titles = derive_titles(nodes)
    stored = store_partition(titles, profile)
    return {"nodes": len(nodes), "magnets": len(live_magnets()),
            "derived": len(titles), **stored}


def partition_status(profile: str = "default") -> dict:
    d = _pdir(profile)
    rows = []
    f = d / "titles.jsonl"
    if f.exists():
        rows = [json.loads(l) for l in f.read_text().splitlines()
                if l.strip()]
    by_tier = {}
    for r in rows:
        by_tier[r.get("tier", "?")] = \
            by_tier.get(r.get("tier", "?"), 0) + 1
    return {"profile": profile, "titles": len(rows),
            "by_tier": by_tier,
            "puzzle_pieces": len(list((d / "puzzle").glob("*.json"))),
            "magnets_live": len(live_magnets()),
            "nodes_live": len(live_nodes())}
