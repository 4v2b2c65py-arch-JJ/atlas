"""Manga/manwha library — MangaDex API client + page->video assembler.

Search titles, pull chapter page images, and replicate a chapter into
a video outlet (image sequence -> mp4) that flows through the normal
ATLAS identity/store pipeline. Covers other image formats via the
ingest classifier (image/* types).
"""

import json
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.mangadex.org"
UA = {"User-Agent": "ATLAS-manga/1.0"}
MEDIA_IMG = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")


def _get(path: str, params: dict | None = None) -> dict:
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def search(query: str, limit: int = 6) -> list[dict]:
    """Title search -> [{id,title,alt,tags,cover_url,status}]."""
    data = _get("/manga", {
        "title": query, "limit": str(limit),
        "includes[]": ["cover_art"],
        "contentRating[]": ["safe", "suggestive"],
        "hasAvailableChapters": "true",
    })
    out = []
    for m in data.get("data", []):
        a = m["attributes"]
        title = (a.get("title") or {}).get("en") \
            or next(iter((a.get("title") or {}).values()), "untitled")
        cover = None
        for rel in m.get("relationships", []):
            if rel.get("type") == "cover_art":
                fn = rel["attributes"].get("fileName")
                cover = (f"https://uploads.mangadex.org/covers/"
                         f"{m['id']}/{fn}")
        tags = [t["attributes"]["name"]["en"]
                for t in a.get("tags", [])
                if isinstance(t.get("attributes", {}).get("name"), dict)]
        out.append({"id": m["id"], "title": title,
                    "alt": list((a.get("altTitles") or [{}])[0].values())[:2]
                    if a.get("altTitles") else [],
                    "cover_url": cover, "tags": tags[:8],
                    "status": a.get("status"),
                    "year": a.get("year")})
    return out


def chapters(manga_id: str, limit: int = 5) -> list[dict]:
    """First chapters (English, ascending) -> [{id,title,number}]."""
    data = _get(f"/manga/{manga_id}/feed", {
        "translatedLanguage[]": "en", "order[volume]": "asc",
        "order[chapter]": "asc", "limit": str(limit),
        "contentRating[]": ["safe", "suggestive"],
    })
    out = []
    for c in data.get("data", []):
        a = c["attributes"]
        out.append({"id": c["id"],
                    "title": a.get("title") or "",
                    "number": a.get("chapter"),
                    "pages": a.get("pages")})
    return out


def page_urls(chapter_id: str) -> list[str]:
    """Resolve a chapter's page image URLs via the at-home API."""
    meta = _get(f"/at-home/server/{chapter_id}")
    base = meta.get("baseUrl", "")
    ch = (meta.get("chapter") or {})
    h = ch.get("hash", "")
    names = ch.get("data", []) or ch.get("dataSaver", [])
    return [f"{base}/data/{h}/{n}" for n in names]


ASURA = "https://api.asurascans.com/api"


def _asura_get(path: str, params: dict | None = None) -> dict:
    url = ASURA + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def asura_search(query: str, limit: int = 6) -> list[dict]:
    """Asura Scans title search -> common result shape."""
    data = _asura_get("/series", {"search": query})
    out = []
    for s in (data.get("data") or [])[:limit]:
        alts = s.get("alt_titles") or []
        out.append({
            "id": s.get("slug") or str(s.get("id")),
            "source": "asura",
            "title": s.get("title", "untitled"),
            "alt": [str(a)[:60] for a in alts[:2]],
            "cover_url": s.get("cover_url") or s.get("cover"),
            "tags": (s.get("genres") or [])[:8],
            "status": s.get("status"),
            "type": s.get("type"),
            "chapters": s.get("chapter_count"),
        })
    return out


def asura_chapters(slug: str, limit: int = 5) -> list[dict]:
    """Asura chapters (latest first) -> common shape."""
    data = _asura_get(f"/series/{slug}/chapters",
                      {"page": 1, "limit": str(limit)})
    out = []
    for c in data.get("data", []):
        out.append({"id": c.get("slug") or str(c.get("id")),
                    "source": "asura",
                    "series_slug": slug,
                    "title": c.get("title") or "",
                    "number": c.get("number"),
                    "pages": c.get("page_count")})
    return out


def asura_pages(series_slug: str, chapter_slug: str) -> list[str]:
    """Asura chapter page image URLs (CDN webp)."""
    data = _asura_get(f"/series/{series_slug}/chapters/{chapter_slug}")
    ch = (data.get("data") or {}).get("chapter") or {}
    return [p["url"] for p in ch.get("pages", []) if p.get("url")]


# --- Madara/WordPress scan sites (wp-manga-search-manga + manga_get_chapters
# + chapter page <img> scrape). Register any Madara host here. ---
MADARA_SITES = {
    "mangaforfree": "https://mangaforfree.com",
}


def _madara_post(base: str, action: str, **params) -> str:
    data = urllib.parse.urlencode({"action": action, **params}).encode()
    req = urllib.request.Request(
        base + "/wp-admin/admin-ajax.php", data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def _madara_get(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "replace")


def _madara_detail(base: str, url: str) -> dict:
    """Pull manga_id + cover from a /manga/{slug}/ page."""
    import re
    html = _madara_get(url)
    mid = re.search(r'"manga_id"\s*:\s*"?(\d+)', html)
    cover = re.search(
        r'"image"\s*:\s*\{[^}]*?"url"\s*:\s*"([^"]+)"', html, re.S)
    title = re.search(r'"headline"\s*:\s*"([^"]+)"', html)
    return {"manga_id": mid.group(1) if mid else None,
            "cover_url": cover.group(1) if cover else None,
            "title": title.group(1) if title else None}


def madara_search(query: str, site: str = "mangaforfree",
                  limit: int = 6) -> list[dict]:
    """{site} title search via admin-ajax -> common result shape."""
    base = MADARA_SITES[site]
    raw = _madara_post(base, "wp-manga-search-manga", title=query)
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if not data.get("success"):
        return []
    items = [i for i in (data.get("data") or [])
             if i.get("url") and i.get("title")
             and i.get("type") in (None, "manga", "manhwa", "manhua")]
    out = []
    for it in items[:limit]:
        url = it.get("url") or ""
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        rec = {"id": slug, "source": site,
               "title": it.get("title", "untitled"),
               "url": url, "cover_url": None, "tags": [],
               "status": None}
        try:
            d = _madara_detail(base, url)
            rec["manga_id"] = d["manga_id"]
            rec["cover_url"] = d["cover_url"]
            if d["title"]:
                rec["title"] = d["title"]
        except Exception:
            pass
        out.append(rec)
    return out


def madara_chapters(site: str, slug: str, limit: int = 5) -> list[dict]:
    """Chapter list for a Madara manga slug -> common shape."""
    import re
    base = MADARA_SITES[site]
    detail = _madara_detail(base, f"{base}/manga/{slug}/")
    mid = detail["manga_id"]
    if not mid:
        return []
    html = _madara_post(base, "manga_get_chapters", manga=mid)
    links = re.findall(
        r'href="([^"]*/manga/[^"]+)"[^>]*>(.*?)</a>', html, re.S)
    out = []
    for href, text in links:
        label = re.sub(r"<[^>]+>|\s+", " ", text).strip()
        if not label or "chapter" not in href.lower():
            continue
        num = re.search(r"(\d+)", label) or re.search(r"(\d+)", href)
        out.append({"id": href, "source": site, "series_slug": slug,
                    "title": label[:80],
                    "number": int(num.group(1)) if num else 0})
    out.sort(key=lambda c: c["number"], reverse=True)
    return out[:limit] if limit else out


def madara_pages(site: str, chapter_url: str) -> list[str]:
    """Page image URLs from a Madara chapter page (document order).
    Falls back to the headless browser when the host walls plain HTTP
    (Cloudflare/JS gates)."""
    import re
    try:
        html = _madara_get(chapter_url)
    except Exception:
        html = ""
    if not html:
        try:
            import browser
            r = browser.render(chapter_url, wait_ms=6000)
            imgs = [u for u in (r.get("media") or [])
                    if re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", u, re.I)
                    and re.search(r"/uploads/|/WP-manga/|/chapter", u, re.I)
                    and not re.search(r"logo|icon|avatar|gravatar|"
                                      r"site-icon|banner", u, re.I)]
            imgs = list(dict.fromkeys(imgs))
            # verified page images become img passkeys for this host
            try:
                import passkeys
                for u in imgs[:5]:
                    passkeys.register(u, via=chapter_url, kind="img")
            except Exception:
                pass
            return imgs
        except Exception:
            return []
    imgs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html)
    seen, out = set(), []
    for s in imgs:
        if not re.search(r"\.(jpe?g|png|webp|gif)(\?|$)", s, re.I):
            continue
        if not re.search(r"/uploads/|/WP-manga/|/chapter", s, re.I):
            continue
        if re.search(r"logo|icon|avatar|gravatar|site-icon|banner", s, re.I):
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def search_all(query: str, limit: int = 6) -> list[dict]:
    """Merged multi-source listing: MangaDex + Asura Scans + Madara
    sites. Each hit tagged with its source."""
    providers = [(search, "mangadex"), (asura_search, "asura")]
    providers += [(lambda q, limit=limit, s=s: madara_search(q, s, limit), s)
                  for s in MADARA_SITES]
    try:
        import aniwatch
        providers.append((aniwatch.search, "aniwatch"))
    except Exception:
        pass
    out = []
    for fn, src in providers:
        try:
            for h in fn(query, limit=limit):
                h.setdefault("source", src)
                out.append(h)
        except Exception:
            continue
    return out


def chapters_for(source: str, manga_id: str, limit: int = 5) -> list[dict]:
    if source == "asura":
        return asura_chapters(manga_id, limit)
    if source in MADARA_SITES:
        return madara_chapters(source, manga_id, limit)
    return chapters(manga_id, limit)


def pages_for(source: str, manga_id: str, chapter_id: str) -> list[str]:
    if source == "asura":
        return asura_pages(manga_id, chapter_id)
    if source in MADARA_SITES:
        return madara_pages(source, chapter_id)
    return page_urls(chapter_id)


def assemble_video(manga_id: str, out_dir: Path, max_pages: int = 40,
                   fps: float = 2.0, source: str = "mangadex",
                   chapter_index: int = -1) -> Path | None:
    """Download first-chapter pages and render them into an mp4
    slideshow — replicates the chapter as a video outlet."""
    import cv2
    import numpy as np
    chs = chapters_for(source, manga_id, limit=max(5, abs(chapter_index) + 1))
    if not chs:
        return None
    ch = chs[chapter_index] if chapter_index < len(chs) else chs[-1]
    urls = pages_for(source, manga_id, ch["id"])[:max_pages]
    if not urls:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    title_slug = "".join(c if c.isalnum() or c in "-_" else "_"
                         for c in manga_id)[:24]
    out = out_dir / f"manga_{title_slug}.mp4"
    if out.exists():
        out.unlink()

    writer, canvas = None, None
    count = 0
    for u in urls:
        try:
            req = urllib.request.Request(u, headers=UA)
            data = urllib.request.urlopen(req, timeout=20).read()
            arr = np.frombuffer(data, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            # very tall strip pages -> slice into portrait segments so
            # the outlet lands in a sane aspect instead of rejected
            segments = [img]
            if img.shape[0] > img.shape[1] * 2.0:
                seg_h = int(img.shape[1] * 1.5)
                segments = [img[y:y + seg_h]
                            for y in range(0, img.shape[0], seg_h)
                            if img[y:y + seg_h].shape[0] > 40]
            for seg in segments:
                if canvas is None:
                    ch, cw = seg.shape[:2]
                    aspect = min(1.6, max(0.45, cw / ch))
                    h = 960
                    w = int(min(1280, max(640, h * aspect)))
                    canvas = (h, w)
                    writer = cv2.VideoWriter(str(out),
                                             cv2.VideoWriter_fourcc(*"avc1"),
                                             fps, (w, h))
                # fit segment into canvas preserving aspect, letterbox
                ch, cw = seg.shape[:2]
                scale = min(w / cw, h / ch)
                nw, nh = max(1, int(cw * scale)), max(1, int(ch * scale))
                rs = cv2.resize(seg, (nw, nh))
                frame = np.zeros((h, w, 3), np.uint8)
                y0, x0 = (h - nh) // 2, (w - nw) // 2
                frame[y0:y0 + nh, x0:x0 + nw] = rs
                for _ in range(max(1, int(fps * 1.2))):
                    writer.write(frame)
                count += 1
        except Exception:
            continue
    if writer:
        writer.release()
    if not out.exists() or out.stat().st_size < 20000 or count < 3:
        out.unlink(missing_ok=True)
        return None
    return out
