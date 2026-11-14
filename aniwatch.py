"""AniWatch (api.ouranime.to) — anime catalog + stream resolver.

Catalog endpoints are open JSON. Streams are embed pages; MegaPlay
embeds are resolved by scraping the player page for its data-id, calling
stream/getSourcesNew, and AES-CBC-decrypting the `enc` payload (key/iv
are the player's published trust constants). Disguised HLS segments
(.jpg/.js) are fetched and concatenated into a local .ts for ffmpeg.
"""

import base64
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.ouranime.to/api"
UA = {"User-Agent": "ATLAS-aniwatch/1.0"}
WEB_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# MegaPlay player trust constants (from lib/newclient.min.js)
_MP_KEY = b"i?LMTAx0Q6,:}50U".ljust(32, b"\x00")
_MP_IV = b"W0;27ToaUpl_P%'c"[:16].ljust(16, b"\x00")


def _get(url: str, params: dict | None = None, raw: bool = False,
         referer: str | None = None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = dict(UA)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    return data if raw else json.loads(data)


def _web_get(url: str, referer: str | None = None) -> str:
    headers = {"User-Agent": WEB_UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def search(query: str, limit: int = 6) -> list[dict]:
    """Catalog search -> common manga.py result shape."""
    data = _get(f"{API}/catalog/search", {"query": query})
    out = []
    for a in (data.get("results") or [])[:limit]:
        t = a.get("title") or {}
        cover = (a.get("coverImage") or {})
        cover_url = (cover.get("extraLarge") or cover.get("large")
                     or cover.get("medium"))
        out.append({
            "id": str(a.get("id")),
            "source": "aniwatch",
            "title": t.get("userPreferred") or t.get("english")
                     or t.get("romaji") or "untitled",
            "alt": [t.get("romaji"), t.get("native")][:2],
            "cover_url": cover_url,
            "tags": (a.get("genres") or [])[:8],
            "status": a.get("status"),
            "type": a.get("format"),
            "chapters": a.get("episodes"),
            "year": a.get("seasonYear"),
        })
    return out


def info(anilist_id: str) -> dict | None:
    try:
        return _get(f"{API}/catalog/info/{anilist_id}")
    except Exception:
        return None


def episodes(anilist_id: str, limit: int = 30) -> list[dict]:
    """Episode numbers 1..N (catalog has no per-episode metadata)."""
    d = info(anilist_id) or {}
    total = int(d.get("episodes") or 0)
    n = min(total, limit) if limit else total
    return [{"id": str(i), "source": "aniwatch",
             "series_slug": str(anilist_id),
             "title": f"Episode {i}", "number": i}
            for i in range(1, n + 1)]


def stream_sources(anilist_id: str, episode: int,
                   mode: str = "sub") -> list[dict]:
    data = _get(f"{API}/stream/sources",
                {"anilistId": anilist_id, "episodeNumber": episode})
    srcs = [s for s in (data.get("sources") or [])
            if (s.get("mode") or "sub") == mode] or data.get("sources") or []
    return srcs


def _megaplay_file(embed_url: str) -> str | None:
    """Resolve a megaplay.buzz embed page to its m3u8 URL."""
    from Crypto.Cipher import AES
    html = _web_get(embed_url, referer="https://www.aniwatchtv.com/")
    m = re.search(r'data-id="(\d+)"', html)
    if not m:
        return None
    base = urllib.parse.urlsplit(embed_url)
    origin = f"{base.scheme}://{base.netloc}"
    src = _get(f"{origin}/stream/getSourcesNew",
               {"id": m.group(1), "platform": "OTHER", "st": "other"},
               referer=embed_url)
    enc = src.get("enc") or src.get("p")
    if not enc:
        return None
    raw = base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4))
    pt = AES.new(_MP_KEY, AES.MODE_CBC, _MP_IV).decrypt(raw)
    pad = pt[-1]
    if 0 < pad <= 16:
        pt = pt[:-pad]
    try:
        return json.loads(pt).get("file")
    except ValueError:
        m = re.search(rb'"file"\s*:\s*"([^"]+)"', pt)
        return m.group(1).decode() if m else None


def resolve_stream(anilist_id: str, episode: int,
                   mode: str = "sub") -> dict | None:
    """Pick a playable source; resolve MegaPlay embeds to m3u8."""
    for s in stream_sources(anilist_id, episode, mode):
        url = s.get("sourceUrl") or ""
        host = (s.get("host") or urllib.parse.urlsplit(url).netloc)
        if "megaplay" in host:
            try:
                m3u8 = _megaplay_file(url)
                if m3u8:
                    b = urllib.parse.urlsplit(url)
                    return {"m3u8": m3u8, "provider": s.get("provider"),
                            "embed": url, "mode": mode,
                            "referer": f"{b.scheme}://{b.netloc}/"}
            except Exception:
                continue
        elif re.search(r"\.m3u8|\.mp4", url, re.I):
            return {"m3u8": url, "provider": s.get("provider"),
                    "embed": url, "mode": mode}
        # unresolvable embed -> try next provider
    return None


def _variant_playlist(m3u8_url: str, referer: str | None) -> tuple[str, list[str], list[float]]:
    """Fetch playlist; if master, follow first variant. Returns
    (variant_base_url, segment_urls, durations)."""
    text = _web_get(m3u8_url) if not referer else _get(
        m3u8_url, raw=True, referer=referer).decode("utf-8", "replace")
    if "#EXT-X-STREAM-INF" in text:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        var = next((l for l in lines if not l.startswith("#")), None)
        if not var:
            return "", [], []
        var_url = urllib.parse.urljoin(m3u8_url, var)
        return _variant_playlist(var_url, referer)
    segs, durs = [], []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            try:
                durs.append(float(line.split(":", 1)[1].split(",")[0]))
            except ValueError:
                durs.append(4.0)
        elif line and not line.startswith("#"):
            segs.append(urllib.parse.urljoin(m3u8_url, line))
    return m3u8_url, segs, durs


def download_clip(m3u8_url: str, out: Path, seconds: float = 60,
                  referer: str | None = None) -> Path | None:
    """Fetch ~`seconds` of HLS into a local mp4 via segment concat +
    imageio-ffmpeg. Disguised .jpg/.js segments are concatenated as TS."""
    import subprocess
    import imageio_ffmpeg
    _, segs, durs = _variant_playlist(m3u8_url, referer)
    if not segs:
        return None
    ts_path = out.with_suffix(".ts")
    n = 0
    acc = 0.0
    with open(ts_path, "wb") as f:
        for u, d in zip(segs, durs + [4.0] * len(segs)):
            if acc >= seconds:
                break
            try:
                data = _get(u, raw=True, referer=referer
                            or "https://megaplay.buzz/")
                if len(data) < 5000 or not data.startswith(b"\x47"):
                    continue
                f.write(data)
                acc += d
                n += 1
            except Exception:
                continue
    if n < 3:
        ts_path.unlink(missing_ok=True)
        return None
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    r = subprocess.run(
        [ff, "-y", "-i", str(ts_path), "-t", str(int(seconds) + 5),
         "-c", "copy", str(out)],
        capture_output=True, timeout=300)
    ts_path.unlink(missing_ok=True)
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 50000:
        out.unlink(missing_ok=True)
        return None
    return out
