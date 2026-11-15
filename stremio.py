"""Stremio layer — Torrentio magnets, Cinemeta teasers, upcoming
title discovery.

  torrentio.strem.fun  — stream addon aggregators (TPB, YTS,
                         Knaben...): imdb/tmdb id -> magnet streams
  apibay.org           — ThePirateBay public API: text query ->
                         magnets (games category is empty on
                         apibay — honest gap, non-game media works)
  v3-cinemeta.strem.io — catalog/meta: search + trailers
                         (trailerStreams = the teasers)

Magnets produced here feed partition.live_magnets as the live
host basis. Cinemeta covers movies/series — games aren't in its
catalog (honest scope note).
"""

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "stremio"
UA = {"User-Agent": "Mozilla/5.0"}


def _get_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def torrentio_streams(media_id: str, mtype: str = "movie",
                      config: str = "") -> list[dict]:
    """Torrentio stream lookup -> magnet links. media_id = imdb
    (tt...) or tmdb numeric or infoHash."""
    cfg = f"{config}/" if config else ""
    url = (f"https://torrentio.strem.fun/{cfg}stream/"
           f"{mtype}/{media_id}.json")
    try:
        d = _get_json(url)
    except Exception as e:
        return [{"error": str(e)[:120]}]
    out = []
    for s in d.get("streams", []):
        ih = s.get("infoHash")
        if not ih:
            continue
        out.append({
            "name": s.get("name", ""),
            "info_hash": ih,
            "magnet": f"magnet:?xt=urn:btih:{ih}",
            "title": s.get("title") or s.get("name", ""),
            "size": s.get("size"),
            "sources": [x.get("name") for x in
                        s.get("sources", []) if isinstance(x, dict)],
            "behavior": s.get("behaviorHints", {}),
        })
    return out[:100]


def apibay_search(query: str, cat: int = 0,
                  limit: int = 50) -> list[dict]:
    """ThePirateBay public API — real magnets for non-game media.
    Games return empty on apibay (category purged upstream)."""
    q = urllib.parse.quote(query)
    url = f"https://apibay.org/q.php?q={q}&cat={cat}"
    try:
        d = _get_json(url)
    except Exception as e:
        return [{"error": str(e)[:120]}]
    out = []
    for r in d:
        if r.get("name") == "No results returned":
            continue
        magnet = r.get("magnet_link") or ""
        if not magnet:
            continue
        out.append({
            "name": r.get("name", ""),
            "magnet": magnet,
            "seeders": int(r.get("seeders") or 0),
            "leechers": int(r.get("leechers") or 0),
            "size": int(r.get("size") or 0),
            "category": r.get("category"),
            "added": r.get("added"),
        })
        if len(out) >= limit:
            break
    return out


def cinemeta_search(query: str, mtype: str = "movie",
                    limit: int = 10) -> list[dict]:
    """Cinemeta catalog search — movies/series only."""
    q = urllib.parse.quote(query)
    url = (f"https://v3-cinemeta.strem.io/catalog/{mtype}/top/"
           f"search={q}.json")
    try:
        d = _get_json(url)
    except Exception as e:
        return [{"error": str(e)[:120]}]
    out = []
    for m in d.get("metas", [])[:limit]:
        out.append({
            "id": m.get("id"), "type": m.get("type"),
            "name": m.get("name"), "year": m.get("year"),
            "genre": m.get("genre"), "imdb": m.get("imdb_id"),
            "poster": m.get("poster"),
            "slug": m.get("slug"),
        })
    return out


def teasers(media_id: str, mtype: str = "movie") -> dict:
    """Cinemeta meta -> trailers + trailerStreams = the teasers."""
    try:
        d = _get_json(
            f"https://v3-cinemeta.strem.io/meta/{mtype}/"
            f"{media_id}.json")
    except Exception as e:
        return {"error": str(e)[:120]}
    m = d.get("meta", {})
    return {
        "name": m.get("name"), "year": m.get("year"),
        "trailers": m.get("trailers") or [],
        "trailer_streams": m.get("trailerStreams") or [],
        "videos": m.get("videos") or [],
        "poster": m.get("poster"),
        "imdb": m.get("imdb_id"),
        "released": m.get("released"),
    }


def magnets_for_title(title: str) -> list[dict]:
    """All magnet sources for a title: cinemeta id -> torrentio,
    plus direct apibay. Returns unified magnet records."""
    out = []
    # cinemeta gives imdb ids for movies/series
    metas = cinemeta_search(title, limit=2)
    for m in metas:
        mid = m.get("imdb") or m.get("id")
        if not mid:
            continue
        for s in torrentio_streams(mid, m.get("type") or "movie"):
            if "error" not in s:
                out.append({**s, "via": "torrentio",
                            "meta": m.get("name")})
    # apibay direct (non-game content)
    for r in apibay_search(title, limit=25):
        if "error" not in r:
            out.append({**r, "via": "apibay"})
    return out


def upcoming_titles(query: str = "upcoming game releases",
                    limit: int = 10) -> list[dict]:
    """Upcoming titles via web search (DDG lite) — game releases,
    expansions, expansions news."""
    q = urllib.parse.quote(query)
    url = ("https://lite.duckduckgo.com/lite/?q=" + q)
    try:
        req = urllib.request.Request(url, headers=UA)
        html = urllib.request.urlopen(req, timeout=25).read() \
            .decode("utf-8", "replace")
    except Exception as e:
        return [{"error": str(e)[:120]}]
    out = []
    for m in re.finditer(
            r"<a\b[^>]*class=['\"]result-link['\"][^>]*>"
            r"(.*?)</a>", html, re.S):
        tag = m.group(0)
        txt = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        hm = re.search(r'href="([^"]+)"', tag)
        link = hm.group(1) if hm else ""
        # duckduckgo wraps targets in uddg=
        um = re.search(r"uddg=([^&\"']+)", link)
        if um:
            link = urllib.parse.unquote(um.group(1))
        if txt:
            out.append({"title": txt, "url": link})
        if len(out) >= limit:
            break
    return out


def store_magnts(magnets: list[dict]) -> int:
    """Append magnets to the stremio magnet pool (partition reads
    live nodes from here too)."""
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "magnets.jsonl"
    seen = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                seen.add(json.loads(line).get("magnet"))
            except Exception:
                pass
    n = 0
    with path.open("a") as f:
        for m in magnets:
            mg = m.get("magnet")
            if not mg or mg in seen:
                continue
            seen.add(mg)
            f.write(json.dumps({"ts": time.time(), **m},
                               ensure_ascii=False) + "\n")
            n += 1
    return n
