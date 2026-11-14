"""Stream intake — accept outlet descriptors of any format and file
them into an organised catalogue.d/ tree by transport type.

Supported entry shapes (POST /api/ingest or drop a .json in catalogue.d/):
  {"title": "...", "url"|"uri"|"magnet"|"m3u8"|"mpd": "...",
   "tags": [...], "source": "...", "node": "..."}

Transport types: direct (http file), web (watch page), hls (.m3u8),
dash (.mpd), magnet, peer. Fetchable types route through the normal
fetch pipeline; magnet/peer are catalogued for nodes that can resolve
them (e.g. a peer hub running a torrent client).
"""

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).parent
CATALOGUE_D = ROOT / "catalogue.d"

MEDIA_EXT = re.compile(r"\.(mp4|webm|mkv|mov|avi|m4v|ts)(\?|#|$)", re.I)
IMAGE_EXT = re.compile(r"\.(jpe?g|png|webp|gif|avif)(\?|#|$)", re.I)
COMIC_EXT = re.compile(r"\.(cbz|cbr|cb7|cbt)(\?|#|$)", re.I)


def classify(uri: str) -> str:
    u = (uri or "").strip().lower()
    if u.startswith("magnet:"):
        return "magnet"
    if u.startswith("peer://") or u.startswith("tribler"):
        return "peer"
    if ".m3u8" in u or "m3u8" in u.split("?")[0]:
        return "hls"
    if ".mpd" in u or "dash" in u.split("?")[0]:
        return "dash"
    if IMAGE_EXT.search(u):
        return "image"
    if COMIC_EXT.search(u):
        return "comic"
    if u.startswith("http"):
        return "direct" if MEDIA_EXT.search(u) else "web"
    return "unknown"


def _slug(title: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_"
                   for c in (title or "outlet"))[:60] or "outlet"


def ingest(entry: dict) -> dict:
    """Validate, classify and file one outlet descriptor."""
    uri = (entry.get("uri") or entry.get("url") or entry.get("magnet")
           or entry.get("m3u8") or entry.get("mpd") or "").strip()
    if not uri:
        return {"error": "no uri/url/magnet/m3u8/mpd provided"}
    title = (entry.get("title") or uri.split("/")[-1].split("?")[0]
             or "outlet")[:120]
    kind = classify(uri)
    rec = {
        "title": title,
        "uri": uri,
        "url": uri,  # compatibility with candidate/fetch pipeline
        "type": kind,
        "fetchable": kind in ("direct", "web", "hls", "dash", "image"),
        "tags": [str(t).lower() for t in entry.get("tags", [])][:12]
                or title.lower().split()[:8],
        "source": entry.get("source", "ingest"),
        "node": entry.get("node", ""),
        "ts": time.time(),
    }
    out_dir = CATALOGUE_D / kind
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{_slug(title)}.json"
    dest.write_text(json.dumps(rec, indent=1))
    return {"stored": str(dest), "type": kind, "title": title,
            "fetchable": rec["fetchable"]}


def ingest_file(path: Path | str) -> dict:
    """Ingest a dropped .json descriptor (file or list of files)."""
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return {"error": f"bad json: {e}"}
    items = data if isinstance(data, list) else [data]
    return {"results": [ingest(i) for i in items if isinstance(i, dict)]}


def extra_catalogue() -> list[dict]:
    """All ingested entries as catalogue candidates."""
    out = []
    if not CATALOGUE_D.exists():
        return out
    for f in sorted(CATALOGUE_D.rglob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        if isinstance(rec, dict) and rec.get("url"):
            rec.setdefault("origin", "ingested")
            rec.setdefault("type", classify(rec["url"]))
            out.append(rec)
    return out
