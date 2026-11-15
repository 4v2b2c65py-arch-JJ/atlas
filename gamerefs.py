"""GameRefs — match catalog titles to what the loader actually
asks for, via live reference stores.

Sources:
  Steam store API (direct weblink): storesearch -> appid ->
    appdetails = latest official release data
  FitGirl Repacks: site search -> repack page = community build
    reference (version/update info lives on the post)
  Nexus Mods: gated (Cloudflare 403 without API key) — recorded
    honestly as requiring a user-supplied key.

match_title() produces a 'loader_match': the official latest
state + community repack reference + expansion/DLC hints. Stored
to data/gamerefs/matches.jsonl, deduped by norm title.
"""

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "gamerefs"
MATCHES = OUT / "matches.jsonl"

UA = {"User-Agent": "Mozilla/5.0"}


def _get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ",
                  re.sub(r"[^a-z0-9 ]", "", t.lower())).strip()


def steam_search(title: str) -> list[dict]:
    """Steam store search -> appid candidates."""
    try:
        q = urllib.parse.quote(title)
        d = json.loads(_get(
            "https://store.steampowered.com/api/storesearch/"
            f"?term={q}&cc=us&l=en"))
        return [{"appid": i["id"], "name": i["name"],
                 "url": f"https://store.steampowered.com/app/{i['id']}/"}
                for i in d.get("items", [])[:5]]
    except Exception as e:
        return [{"error": str(e)[:120]}]


def steam_app(appid: int) -> dict:
    """appdetails = the loader's official ask: latest release,
    platforms, genres, price state."""
    try:
        d = json.loads(_get(
            "https://store.steampowered.com/api/appdetails/"
            f"?appids={appid}&cc=us&l=en&filters=name,"
            "release_date,genres,platforms,is_free,header_image,"
            "type,dlc"))
        app = d.get(str(appid), {}).get("data", {})
        return {"appid": appid, "name": app.get("name"),
                "release_date": app.get("release_date", {})
                .get("date"),
                "coming_soon": app.get("release_date", {})
                .get("coming_soon"),
                "genres": [g["description"] for g in
                           app.get("genres", [])],
                "platforms": app.get("platforms"),
                "is_free": app.get("is_free"),
                "header": app.get("header_image"),
                "dlc_count_hint": len(app.get("dlc", []) or []),
                "type": app.get("type")}
    except Exception as e:
        return {"appid": appid, "error": str(e)[:120]}


def fitgirl_search(title: str) -> list[dict]:
    """FitGirl repack reference — community build pages."""
    try:
        q = urllib.parse.quote(title)
        html = _get(
            f"https://fitgirl-repacks.site/?s={q}").decode(
            "utf-8", "replace")
        out = []
        for m in re.finditer(
                r'<h[12][^>]*class="[^"]*entry-title[^"]*"[^>]*>'
                r'\s*<a href="([^"]+)"[^>]*>([^<]+)</a>', html):
            out.append({"title": m.group(2).strip(),
                        "url": m.group(1)})
        return out[:5]
    except Exception as e:
        return [{"error": str(e)[:120]}]


def nexus_status() -> dict:
    """Nexus Mods — gated. The official API needs a user key;
    the site 403s plain fetches. Honest status, no fake data."""
    return {"status": "gated", "reason": "HTTP 403 — requires "
            "Nexus API key (user-supplied) or browser session",
            "api": "https://api.nexusmods.com/v2/"}


def match_title(title: str) -> dict:
    """Full match: steam official latest + fitgirl repack ref +
    nexus status + loader requirement notes."""
    t0 = time.time()
    steam_hits = steam_search(title)
    best = None
    if steam_hits and "appid" in steam_hits[0]:
        # pick the closest name match
        nt = _norm(title)
        best = min(steam_hits,
                   key=lambda h: abs(len(_norm(h["name"])) -
                                     len(nt)))
        best = steam_app(best["appid"])
    fg = fitgirl_search(title)
    nexus = nexus_status()
    # loader ask: what the loader needs = official version state +
    # repack reference + content hints
    loader = {
        "official_release": (best or {}).get("release_date"),
        "coming_soon": (best or {}).get("coming_soon"),
        "platforms": (best or {}).get("platforms"),
        "genres": (best or {}).get("genres"),
        "repack_refs": [f["title"] for f in fg
                        if "title" in f],
        "expansions_hint": "see steam app page dlc/ost "
                           "listings + repack post updates",
        "nexus": nexus["status"],
    }
    rec = {"ts": time.time(), "title": title, "norm": _norm(title),
           "steam": best, "fitgirl": fg, "nexus": nexus,
           "loader_match": loader,
           "elapsed_s": round(time.time() - t0, 2)}
    OUT.mkdir(parents=True, exist_ok=True)
    # dedupe on norm title — latest wins
    rows = []
    if MATCHES.exists():
        rows = [json.loads(l) for l in MATCHES.read_text().splitlines()
                if l.strip() and json.loads(l).get("norm")
                != rec["norm"]]
    rows.append(rec)
    MATCHES.write_text("".join(json.dumps(r, ensure_ascii=False)
                               + "\n" for r in rows))
    return rec


def batch_match(titles: list[str],
                delay: float = 1.2) -> dict:
    """Match a list of titles with polite rate limiting."""
    done = []
    for t in titles:
        done.append(match_title(t))
        time.sleep(delay)
    return {"matched": len(done),
            "with_steam": sum(1 for d in done
                              if d.get("steam", {}).get("name")),
            "with_fitgirl": sum(1 for d in done
                                if any("title" in f
                                       for f in d.get("fitgirl",
                                                       [])))}


def match_dlps(limit: int = 25) -> dict:
    """Match the DLPS catalog top titles — the 'given' side."""
    import dlps
    scraps = dlps.titles()
    # unique titles, prefer ps4/ps5 first
    seen, titles = set(), []
    for r in scraps:
        t = r["title"]
        if t not in seen:
            seen.add(t)
            titles.append(t)
        if len(titles) >= limit:
            break
    return batch_match(titles)


def status() -> dict:
    rows = []
    if MATCHES.exists():
        rows = [json.loads(l) for l in MATCHES.read_text().splitlines()
                if l.strip()]
    return {"matches": len(rows),
            "with_steam": sum(1 for r in rows
                              if r.get("steam", {}).get("name")),
            "with_fitgirl": sum(1 for r in rows
                                if any("title" in f for f in
                                       r.get("fitgirl", []))),
            "nexus": nexus_status()["status"],
            "out": str(MATCHES)}
