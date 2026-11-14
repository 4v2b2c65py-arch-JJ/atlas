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


def search_all(query: str, limit: int = 6) -> list[dict]:
    """Merged multi-source listing: MangaDex + Asura Scans (+ any
    future providers). Each hit tagged with its source."""
    out = []
    for fn in (search, asura_search):
        try:
            for h in fn(query, limit=limit):
                h.setdefault("source", "mangadex" if fn is search else "asura")
                out.append(h)
        except Exception:
            continue
    return out


def chapters_for(source: str, manga_id: str, limit: int = 5) -> list[dict]:
    if source == "asura":
        return asura_chapters(manga_id, limit)
    return chapters(manga_id, limit)


def pages_for(source: str, manga_id: str, chapter_id: str) -> list[str]:
    if source == "asura":
        return asura_pages(manga_id, chapter_id)
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

    writer, w, h = None, None, None
    count = 0
    for u in urls:
        try:
            req = urllib.request.Request(u, headers=UA)
            data = urllib.request.urlopen(req, timeout=20).read()
            arr = np.frombuffer(data, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            if img.shape[1] > img.shape[0] * 1.2:  # wide page -> pad
                nw = int(img.shape[0] * 0.75)
                pad = np.zeros((img.shape[0], nw, 3), np.uint8)
                pad[:, :img.shape[1]] = img
                img = pad
            if writer is None:
                h, w = img.shape[:2]
                w = int(w * 0.8); h = int(h * 0.8)
                writer = cv2.VideoWriter(str(out),
                                         cv2.VideoWriter_fourcc(*"avc1"),
                                         fps, (w, h))
            img = cv2.resize(img, (w, h))
            frames = max(1, int(fps * 1.6))
            for _ in range(frames):
                writer.write(img)
            count += 1
        except Exception:
            continue
    if writer:
        writer.release()
    if not out.exists() or out.stat().st_size < 20000 or count < 3:
        out.unlink(missing_ok=True)
        return None
    return out
