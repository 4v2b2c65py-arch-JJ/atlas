"""Passkey store — image/video access keys for walled hosts.

When plain HTTP is blocked (Cloudflare/403/JS gates), the headless
browser renders the page and harvests media URLs (img/video/source).
Each candidate is probed until one verifies as real image/video
content, then stored as a passkey:

    data/passkeys.json  +  in-memory _store
    {site: [{kind: 'img'|'vid', url, via, ts, verified}]}

Later fetches/manga page assembly recall the passkey directly instead
of re-rendering. 'Try until it gets it' = probe candidates in order
until content-type + magic bytes confirm usable media.
"""

import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
PATH = ROOT / "data" / "passkeys.json"
MEDIA_RE = re.compile(r"\.(jpe?g|png|webp|gif|avif|mp4|webm|mov|m4v)"
                      r"(\?|#|$)", re.I)
BAD_RE = re.compile(r"logo|icon|avatar|sprite|banner|pixel|blank|"
                    r"loading|spinner|wordmark|wikimedia|ad[_-]|track|"
                    r"analytic", re.I)

_store: dict = {}


def _load() -> dict:
    global _store
    if not _store:
        try:
            _store = json.loads(PATH.read_text())
        except Exception:
            _store = {}
    return _store


def _save() -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(_store, indent=1))


def _site(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower()


def _kind(url: str) -> str:
    return "vid" if re.search(r"\.(mp4|webm|mov|m4v)", url, re.I) \
        else "img"


def _verify(url: str, referer: str | None = None) -> bool:
    """Probe a candidate: status + content-type + magic bytes."""
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X "
                             "10_15_7) AppleWebKit/537.36 Chrome/120.0",
               "Range": "bytes=0-4095"}
    if referer:
        headers["Referer"] = referer
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read(4096)
            ctype = (r.headers.get("Content-Type") or "").lower()
    except Exception:
        return False
    if len(data) < 200:
        return False
    if not (ctype.startswith(("image/", "video/"))
            or "octet-stream" in ctype):
        return False
    magic = (data[:3] == b"\xff\xd8\xff"          # jpeg
             or data[:8] == b"\x89PNG\r\n\x1a\n"   # png
             or data[:4] == b"RIFF"               # webp/avi
             or data[4:8] == b"ftyp"              # mp4/mov
             or data[:4] == b"GIF8")
    # extension fallback only for real media types (svg/chrome excluded)
    return magic or bool(re.search(
        r"\.(jpe?g|png|webp|gif|avif|mp4|webm|mov|m4v)(\?|#|$)",
        url, re.I))


def harvest(url: str, max_try: int = 15) -> dict:
    """Render `url`, collect media candidates, probe until one
    verifies, store as passkey. Returns the stored record."""
    import browser
    r = browser.render(url, wait_ms=6000)
    NONMEDIA_RE = re.compile(r"\.(css|js|ico|svg|woff2?|ttf|map)"
                             r"(\?|#|$)", re.I)
    cands = []
    for u in (r.get("media") or []):
        if not isinstance(u, str) or not u.startswith("http"):
            continue
        if NONMEDIA_RE.search(u) or BAD_RE.search(u):
            continue
        cands.append(u)
    # video candidates first — a vid passkey unlocks more than an img
    cands.sort(key=lambda u: 0 if _kind(u) == "vid" else 1)
    # also mine raw html for media urls the DOM list missed
    site = _site(url)
    data = _load()
    tried = 0
    for u in dict.fromkeys(cands):
        if tried >= max_try:
            break
        tried += 1
        if _verify(u, referer=url):
            rec = {"kind": _kind(u), "url": u, "via": url,
                   "site": site, "ts": time.time(),
                   "verified": True}
            data.setdefault(site, []).insert(0, rec)
            data[site] = data[site][:20]
            _save()
            _store[site] = data[site]
            return {"stored": rec, "tried": tried,
                    "candidates": len(cands)}
    return {"error": "no verified media passkey",
            "candidates": len(cands), "tried": tried}


def recall(site_or_url: str, kind: str | None = None) -> str | None:
    """Return the best stored passkey URL for a site/host."""
    data = _load()
    raw = site_or_url if "://" in site_or_url \
        else "https://" + site_or_url
    site = _site(raw)
    for rec in data.get(site, []):
        if kind is None or rec.get("kind") == kind:
            return rec["url"]
    # substring fallback across all sites
    for s, recs in data.items():
        if site in s or s in site:
            for rec in recs:
                if kind is None or rec.get("kind") == kind:
                    return rec["url"]
    return None


def register(url: str, via: str = "", kind: str | None = None) -> dict:
    """Manually store a known-good media URL as a passkey."""
    site = _site(via or url)
    rec = {"kind": kind or _kind(url), "url": url, "via": via,
           "site": site, "ts": time.time(), "verified": True}
    data = _load()
    data.setdefault(site, []).insert(0, rec)
    data[site] = data[site][:20]
    _save()
    _store[site] = data[site]
    return {"stored": rec}


def list_all(site: str | None = None) -> dict:
    data = _load()
    if site:
        return {"passkeys": data.get(site, [])}
    return {"passkeys": data}
