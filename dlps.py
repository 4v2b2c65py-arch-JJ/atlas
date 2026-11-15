"""DLPS store scraper — real-time title scraps from the dlpsgame
network (the 'Dlps' source registered under the cusa lore key).

Catalog metadata only: category listings -> game titles + post
URLs. Titles feed unlock.candidates (cusa key) and partition
live_nodes as a title basis. No download automation — the store
is a discovery index, not a fetch target.

Sites (from the cusa registry block):
  dlpsgame.com     PS5/PS4/PS3/PS2
  downloadgamepsp.org  PSP/PSVita
  dlxbgame.com     Xbox
  nswgame.com      Switch/Wii/WiiU/3DS
  gamepciso.com    PC
"""

import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "dlps"
SCRAPS = OUT / "scraps.jsonl"

SITES = {
    "dlpsgame.com": ["ps5", "ps4", "ps3", "ps2"],
    "downloadgamepsp.org": ["psp", "psvita-game-free-new"],
    "dlxbgame.com": ["iso", "jtag-rgh"],
    "nswgame.com": ["switch", "game-wii", "game-wii-u", "3ds"],
    "gamepciso.com": ["action-pc-full-iso", "adventure-pc-full-iso",
                      "rpg-pc-full-iso", "shooter-pc-full-iso"],
}

# cat label -> URL slug overrides (sister sites use different slugs)
SLUG = {
    ("downloadgamepsp.org", "psvita-game-free-new"): "psvita",
    ("nswgame.com", "game-wii"): "wii",
    ("nswgame.com", "game-wii-u"): "wiiu",
}

UA = {"User-Agent": "Mozilla/5.0"}  # dlpsgame blocks custom UAs
POST_RE = re.compile(
    r'<a[^>]+href="(https://[^"]+)"[^>]*>([^<]{3,90})</a>')
SKIP = ("category/", "list-all-game", "list-game", "guide",
        "dmca", "daily-update", "notice", "notification",
        "contact", "youtube.com", "page/")


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def _seen() -> set:
    seen = set()
    if SCRAPS.exists():
        for line in SCRAPS.read_text().splitlines():
            try:
                seen.add(json.loads(line)["url"])
            except Exception:
                pass
    return seen


def scrape(site: str, cat: str, pages: int = 1) -> dict:
    """Scrape a category listing — each page yields ~20 titles.
    Dedupes on post URL; appends to scraps.jsonl."""
    base = f"https://{site}/category/{cat}/"
    seen = _seen()
    new = []
    errors = []
    for p in range(1, pages + 1):
        url = base if p == 1 else f"{base}page/{p}/"
        try:
            html = _fetch(url)
        except Exception as e:
            errors.append({"page": p, "err": str(e)[:120]})
            continue
        for m in POST_RE.finditer(html):
            link, txt = m.groups()
            txt = txt.strip()
            if not txt or any(s in link for s in SKIP):
                continue
            if link in seen:
                continue
            seen.add(link)
            new.append({"ts": time.time(), "site": site,
                        "cat": SLUG.get((site, cat), cat), "page": p,
                        "title": txt, "url": link})
    OUT.mkdir(parents=True, exist_ok=True)
    with SCRAPS.open("a") as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"site": site, "cat": cat, "pages": pages,
            "new": len(new), "errors": errors}


def scrape_network(max_pages: int = 1,
                   cats: list | None = None) -> dict:
    """Sweep the DLPS network — bounded by max_pages per category."""
    results = []
    total = 0
    for site, site_cats in SITES.items():
        for cat in (cats or site_cats):
            if cats and cat not in cats:
                continue
            r = scrape(site, cat, pages=max_pages)
            total += r["new"]
            results.append(r)
            time.sleep(0.5)      # polite crawl delay
    return {"sites": len(SITES), "new_titles": total,
            "results": results}


def titles(cat: str | None = None,
           site: str | None = None) -> list[dict]:
    """All scraped titles, newest first."""
    out = []
    if not SCRAPS.exists():
        return out
    for line in SCRAPS.read_text().splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if cat and r.get("cat") != cat:
            continue
        if site and r.get("site") != site:
            continue
        out.append(r)
    return out


def search(q: str) -> list[dict]:
    """Title search across all scraps — feeds unlock candidates."""
    ql = q.lower()
    return [r for r in titles() if ql in r["title"].lower()]


def status() -> dict:
    t = titles()
    by_cat = {}
    for r in t:
        by_cat[r.get("cat", "?")] = by_cat.get(r.get("cat", "?"), 0) + 1
    return {"scraps": len(t), "by_cat": by_cat,
            "sites": list(SITES), "out": str(SCRAPS)}
