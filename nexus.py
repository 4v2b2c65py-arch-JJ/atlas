"""Nexus Mods — pure page-URL weblink loading, no API key.

The site is a Next.js app; its public GraphQL endpoint
(next.nexusmods.com/api/graphql) serves game + mod data once the
Cloudflare cookies (__cf_bm/__cflb) are warmed by fetching any
nexusmods.com page first. No auth, no key — just weblinks.
"""

import http.cookiejar
import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "nexus"
REFS = OUT / "refs.jsonl"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")
GQL = "https://next.nexusmods.com/api/graphql"

_opener = None


def _session():
    """Warm a cookie session — Cloudflare cookies unlock GraphQL.
    Retries with backoff; CF throttles bursts."""
    global _opener
    hdrs = {"User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9"}
    for attempt in range(4):
        cj = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cj))
        try:
            op.open(urllib.request.Request(
                "https://www.nexusmods.com/", headers=hdrs),
                timeout=20).read()
            _opener = op
            return op
        except Exception:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("nexus session warmup failed — CF throttled")


def gql(query: str) -> dict:
    hdrs = {"User-Agent": UA, "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": "https://www.nexusmods.com",
            "Referer": "https://www.nexusmods.com/"}
    req = urllib.request.Request(
        GQL, data=json.dumps({"query": query}).encode(),
        headers=hdrs)
    for attempt in range(3):
        try:
            return json.loads(_session().open(req, timeout=25)
                              .read())
        except Exception:
            global _opener
            _opener = None      # force re-warm next attempt
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-",
                  title.lower()).strip("-")


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ",
                  re.sub(r"[^a-z0-9 ]", "", t.lower())).strip()


def game_lookup(title: str) -> dict | None:
    """Resolve a title to a Nexus game — domainName slug first,
    then games name-filter search."""
    slug = _slug(title)
    r = gql('{game(domainName:"%s"){id name domainName modCount '
            'downloadCount supportsVortex forumUrl genre}}' % slug)
    g = (r.get("data") or {}).get("game")
    if g:
        return g
    # fallback: name-filtered games search
    safe = title.replace('"', "'")[:60]
    r = gql('{games(count:5,filter:{name:[{value:"%s"}]})'
            '{nodes{name domainName modCount}}}' % safe)
    nodes = ((r.get("data") or {}).get("games") or {}) \
        .get("nodes") or []
    if not nodes:
        return None
    nt = _norm(title)
    best = min(nodes, key=lambda n: abs(
        len(_norm(n["name"])) - len(nt)))
    return game_lookup(best["domainName"]) or best


def game_mods(domain: str, count: int = 10,
              sort: str = "downloads") -> list[dict]:
    """Top mods for a game domain — name, modId, downloads,
    endorsements, version, page URL."""
    gid = None
    r = gql('{game(domainName:"%s"){id}}' % domain)
    g = (r.get("data") or {}).get("game") or {}
    gid = g.get("id")
    if not gid:
        return []
    q = ('{mods(count:%d,filter:{gameId:[{value:"%s"}]})'
         '{nodes{name modId downloads endorsements version '
         'pictureUrl updatedAt}}}' % (count, gid))
    r = gql(q)
    nodes = ((r.get("data") or {}).get("mods") or {}) \
        .get("nodes") or []
    out = []
    for m in nodes:
        mid = m.get("modId")
        out.append({**m,
                    "url": f"https://www.nexusmods.com/{domain}"
                           f"/mods/{mid}"})
    return out


def nexus_refs(title: str, mod_count: int = 10) -> dict:
    """Full reference record for a title: game + top mods."""
    t0 = time.time()
    rec = {"ts": time.time(), "title": title,
           "norm": _norm(title), "via": "nexus-gql"}
    try:
        g = game_lookup(title)
    except Exception as e:
        rec["game"] = None
        rec["mods"] = []
        rec["note"] = f"nexus error: {str(e)[:120]}"
        g = None
    if not g:
        rec.setdefault("game", None)
        rec.setdefault("mods", [])
        rec.setdefault("note", "no nexus game match")
    else:
        rec["game"] = g
        try:
            rec["mods"] = game_mods(g["domainName"],
                                    count=mod_count)
        except Exception as e:
            rec["mods"] = []
            rec["mods_error"] = str(e)[:120]
        rec["top_mod"] = (rec["mods"][0]["name"]
                          if rec["mods"] else None)
    rec["elapsed_s"] = round(time.time() - t0, 2)
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    if REFS.exists():
        rows = [json.loads(l) for l in
                REFS.read_text().splitlines() if l.strip()
                and json.loads(l).get("norm") != rec["norm"]]
    rows.append(rec)
    REFS.write_text("".join(json.dumps(r, ensure_ascii=False)
                            + "\n" for r in rows))
    return rec


def status() -> dict:
    rows = []
    if REFS.exists():
        rows = [json.loads(l) for l in REFS.read_text().splitlines()
                if l.strip()]
    return {"refs": len(rows),
            "with_game": sum(1 for r in rows if r.get("game")),
            "total_mods_seen": sum(len(r.get("mods") or [])
                                   for r in rows),
            "out": str(REFS)}
