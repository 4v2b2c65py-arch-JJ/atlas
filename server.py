"""ATLAS stream hub — local web server for the video outlet feed.

Real-time: SSE event stream, session cookies, HTTP-cacheable streams.
Peers: discovered outlet URLs are exchanged and merged into a shared pool.
Search: LLM-expanded queries -> local match -> uptime-checked candidates
-> auto-fetch through the same store pipeline.

Endpoints:
  GET  /                     -> web/index.html (Vue app)
  GET  /api/events           -> SSE stream (engage/comms/fetched/node)
  GET  /api/feed?q=&cat=     -> video outlet cards (store-sorted)
  GET  /api/search?q=&fetch= -> smart search + catalogue/peer auto-fetch
  GET  /api/uptime           -> POST {urls:[...]} probe candidate URLs
  GET/POST /api/peers        -> peer registry + discovered URL pool
  GET  /api/stream?path=     -> video file bytes (ETag cached)
  GET  /api/node             -> receiver/processor status
  GET  /api/ip               -> caller IP + server LAN IPs
  GET/POST /api/engage       -> likes/comments/reposts (shared state)
  GET/POST /api/comms        -> direct messages between nodes
"""

import concurrent.futures
import hashlib
import json
import mimetypes
import queue
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WEB = ROOT / "web"
DATA = ROOT / "data"
STORE = ROOT / "videos" / "store"
DATA.mkdir(exist_ok=True)

_FETCH_LOCK = threading.Lock()


def _serialized(fn):
    """Serialize fetch+store so tuner/search/peer fetches can't race
    on the same inbox files."""
    import functools

    @functools.wraps(fn)
    def w(*a, **k):
        with _FETCH_LOCK:
            return fn(*a, **k)
    return w

ENGAGE_PATH = DATA / "engagement.json"
COMMS_PATH = DATA / "comms.json"
TIMELINE_PATH = DATA / "timeline.jsonl"


def log_event(kind: str, data: dict) -> None:
    """Append one event to the persistent timeline — the backup
    session log that makes any point in time replayable."""
    try:
        with TIMELINE_PATH.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "kind": kind,
                                **data}) + "\n")
    except Exception:
        pass


def timeline_events(since: float = 0.0, until: float = 0.0,
                    kind: str = "", limit: int = 500) -> list:
    out = []
    try:
        with TIMELINE_PATH.open() as f:
            for ln in f:
                try:
                    e = json.loads(ln)
                except Exception:
                    continue
                if since and e.get("ts", 0) < since:
                    continue
                if until and e.get("ts", 0) > until:
                    continue
                if kind and e.get("kind") != kind:
                    continue
                out.append(e)
    except FileNotFoundError:
        pass
    return out[-limit:]


def replay_at(ts: float) -> dict:
    """Reconstruct engage + comms state as it was at timestamp ts —
    review/replay the session at any point on the timeline."""
    eng = load_json(ENGAGE_PATH, {})
    vids = {}
    for src, v in eng.items():
        vids[src] = {
            "likes": sorted(c for c, t in
                            (v.get("likes") or {}).items() if t <= ts),
            "reposts": sorted(c for c, t in
                              (v.get("reposts") or {}).items()
                              if t <= ts),
            "comments": [c for c in (v.get("comments") or [])
                         if c.get("ts", 0) <= ts][-50:]}
    msgs = [m for m in load_json(COMMS_PATH, [])
            if m.get("ts", 0) <= ts][-100:]
    events = timeline_events(until=ts, limit=200)
    return {"at": ts, "videos": vids, "comms": msgs,
            "events": events}
FEED_CACHE = DATA / "feed_cache.json"
PEERS_PATH = DATA / "peer_urls.json"
_lock = threading.Lock()
_started = time.time()
_clients: dict[str, float] = {}
_requests = 0
_subs: list[queue.Queue] = []
_uptime_cache: dict[str, tuple[float, dict]] = {}
UPTIME_TTL = 120

import api_keys  # noqa: E402
import beacon  # noqa: E402
import bittorrent  # noqa: E402
import identity  # noqa: E402
import ingest  # noqa: E402
import llm  # noqa: E402
import manga  # noqa: E402
import aniwatch  # noqa: E402
import social  # noqa: E402
import tunnel  # noqa: E402
import browser  # noqa: E402
import passkeys  # noqa: E402
import flowviz  # noqa: E402
import twitch  # noqa: E402
import sites  # noqa: E402
import economy  # noqa: E402
import prime  # noqa: E402
import tax  # noqa: E402
import catalogue  # noqa: E402
import vision  # noqa: E402
import cardinal  # noqa: E402
import namescrape  # noqa: E402
import nameconsensus  # noqa: E402
import caster  # noqa: E402
import gaster  # noqa: E402

# GML/SML-1 spec engine (AUTO-01 add-1 rule, CF-01 requirements)
GML_DIR = ROOT / "gml" / "SML-1"
if str(GML_DIR) not in sys.path:
    sys.path.insert(0, str(GML_DIR))
try:
    import gml_spec  # noqa: E402
except Exception:
    gml_spec = None

AUTO_PATH = DATA / "gml_auto.json"


# ---------- small utils ----------

def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def save_json(path: Path, obj):
    path.write_text(json.dumps(obj, indent=1))


def lan_ips() -> list[str]:
    ips = {"127.0.0.1"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(ips)


def broadcast(kind: str, data):
    msg = f"event: {kind}\ndata: {json.dumps(data)}\n\n"
    for q in list(_subs):
        try:
            q.put_nowait(msg)
        except queue.Full:
            pass


# ---------- media index ----------

def video_files() -> list[Path]:
    files = []
    if STORE.exists():
        for p in STORE.rglob("*"):
            if p.suffix.lower() in (".mp4", ".webm", ".mov", ".mkv"):
                files.append(p)
    return files


def probe_cached(path: Path) -> dict:
    cache = load_json(FEED_CACHE, {})
    key = str(path)
    st = path.stat()
    if key in cache and cache[key].get("mtime") == st.st_mtime \
            and cache[key].get("size") == st.st_size \
            and "tags" in cache[key]:
        return cache[key]
    import cv2
    cap = cv2.VideoCapture(str(path))
    meta = {
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": round(cap.get(cv2.CAP_PROP_FPS), 2),
        "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
    }
    cap.release()
    meta["duration"] = round(meta["frames"] / meta["fps"], 2) if meta["fps"] else 0
    meta["category"] = path.parent.name if path.parent != STORE else "misc"
    meta["name"] = path.stem
    meta["tags"] = sorted(set(
        meta["name"].replace("_", " ").replace("-", " ").lower().split()
    ) | {meta["category"]})
    meta["size"] = st.st_size
    meta["mtime"] = st.st_mtime
    cache[key] = meta
    save_json(FEED_CACHE, cache)
    return meta


def feed(q: str = "", cat: str = "", keywords: list | None = None) -> list[dict]:
    items = []
    terms = [t.lower() for t in (keywords or [q])] if (keywords or q) else []
    for p in video_files():
        try:
            meta = probe_cached(p)
        except Exception:
            continue
        meta["src"] = str(p.relative_to(ROOT))
        if cat and cat != "all" and meta["category"] != cat:
            continue
        if terms:
            hay = " ".join([meta["name"], meta["category"],
                            " ".join(meta["tags"])]).lower()
            hits = 0
            for t in terms:
                toks = [w for w in t.split() if len(w) > 2]
                if t in hay or sum(1 for w in toks if w in hay) >= max(1, (len(toks) + 1) // 2):
                    hits += 1
            if hits == 0:
                continue
            meta["_score"] = hits
        items.append(meta)
    items.sort(key=lambda m: (-m.get("_score", 0), -m["mtime"]))
    for m in items:
        m.pop("_score", None)
    return items


# ---------- candidates: catalogue + peer pool ----------

def peer_pool() -> list[dict]:
    """URLs discovered by peers/self, as pseudo-catalogue entries."""
    pool = load_json(PEERS_PATH, {})
    out = []
    for url, meta in pool.items():
        slug = url.split("/")[-1].rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
        out.append({
            "title": meta.get("title") or slug,
            "url": url,
            "tags": meta.get("tags", []) + slug.lower().split(),
            "size_mb": meta.get("size_mb", 10),
            "peer": meta.get("source", "peer"),
        })
    return out


def all_candidates(keywords: list[str]) -> list[dict]:
    """Catalogue entries + peer pool, tag-scored against query keywords."""
    cands = [{**e, "origin": "catalogue"} for e in catalogue.CATALOGUE]
    cands += [{**e, "origin": "ingested"} for e in ingest.extra_catalogue()]
    cands += [{**e, "origin": "peer"} for e in peer_pool()]
    kws = [k.lower() for k in keywords]
    for c in cands:
        hay = " ".join([c["title"], " ".join(c["tags"])]).lower()
        c["_score"] = sum(
            1 for k in kws
            if k in hay or any(w in hay for w in k.split() if len(w) > 2)
        )
    return sorted(cands, key=lambda c: -c["_score"])


def check_uptimes(urls: list[str]) -> dict:
    """Concurrent HEAD probes with TTL cache."""
    now = time.time()
    todo = [u for u in urls if u not in _uptime_cache
            or now - _uptime_cache[u][0] > UPTIME_TTL]

    UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AtlasHub/1.0"}

    def probe(u):
        t0 = time.time()
        try:
            req = urllib.request.Request(u, method="HEAD", headers=UA)
            with urllib.request.urlopen(req, timeout=8) as r:
                return u, {"ok": 200 <= r.status < 400, "status": r.status,
                           "ms": int((time.time() - t0) * 1000)}
        except Exception:
            # HEAD rejected -> ranged GET
            try:
                req = urllib.request.Request(
                    u, headers={**UA, "Range": "bytes=0-0"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    return u, {"ok": True, "status": r.status,
                               "ms": int((time.time() - t0) * 1000)}
            except Exception as e2:
                return u, {"ok": False, "error": str(e2)[:120],
                           "ms": int((time.time() - t0) * 1000)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for u, res in ex.map(probe, todo):
            _uptime_cache[u] = (now, res)
    return {u: _uptime_cache.get(u, (0, {}))[1] for u in urls}


def register_peer_url(url: str, title: str, tags: list, source: str):
    with _lock:
        pool = load_json(PEERS_PATH, {})
        pool[url] = {"title": title, "tags": tags, "source": source,
                     "ts": time.time()}
        # cap pool size, evict oldest
        if len(pool) > 500:
            for k in sorted(pool, key=lambda k: pool[k].get("ts", 0))[:len(pool) - 500]:
                pool.pop(k, None)
        save_json(PEERS_PATH, pool)


def restash_if_poor(stored: dict) -> dict:
    """Never drop usable content: gate-rejected items get upscaled when
    resolution is the problem, otherwise re-filed with the score gate
    bypassed. Only hard failures (probe/dimensions/bytes) stay rejected."""
    if not stored or "rejected" not in stored.get("categories", []):
        return stored
    path = stored.get("stored")
    if not path or not Path(path).exists():
        return stored
    import upscale
    try:
        poor = upscale.needs_upscale(path)
    except Exception:
        poor = False
    if poor:
        up = upscale.upscale_video(path)
        if up:
            rest = identity.store_file(str(up), skip_score_gate=True)
            Path(path).unlink(missing_ok=True)
            up.unlink(missing_ok=True)
            return {**rest, "upscaled": True}
    try:
        rest = identity.store_file(path, skip_score_gate=True)
    except Exception:
        return stored
    if rest.get("stored") and rest["stored"] != path:
        Path(path).unlink(missing_ok=True)  # remove the rejected copy
    return {**rest, "restashed": True}


@_serialized
def fetch_url(entry: dict) -> dict | None:
    """Download one candidate through curl -> inbox -> identity store."""
    import subprocess
    import shutil
    need = int(entry.get("size_mb", 10) * 1e6 * 1.2)
    if need > shutil.disk_usage(ROOT).free * 0.5:
        return {"error": "storage gate", "title": entry["title"]}
    inbox = ROOT / "videos" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_"
                   for c in entry["title"])[:80]
    dest = inbox / f"{safe}.mp4"
    r = subprocess.run(
        ["curl", "-fL", "--retry", "1", "--max-time", "300",
         "-o", str(dest), entry["url"]],
        capture_output=True)
    if r.returncode != 0 or not dest.exists() or dest.stat().st_size < 10000:
        dest.unlink(missing_ok=True)
        return {"error": "download failed", "title": entry["title"]}
    stored = restash_if_poor(identity.store_file(str(dest),
                                               skip_score_gate=True))
    dest.unlink(missing_ok=True)  # inbox is transient; store copy is canonical
    register_peer_url(entry["url"], entry["title"], entry.get("tags", []),
                      entry.get("origin", "self"))
    return {"title": entry["title"], "url": entry["url"],
            "stored": stored.get("stored"),
            "categories": stored.get("categories"),
            "size_mb": entry.get("size_mb")}


YTDLP = str(ROOT / ".venv" / "bin" / "yt-dlp")
PEER_HUBS_PATH = DATA / "peer_hubs.json"
CLIP_SECONDS = 60  # every fetch becomes a stream-length clip


def ffmpeg_ok() -> bool:
    import subprocess
    try:
        return subprocess.run(["ffmpeg", "-version"], capture_output=True,
                              timeout=10).returncode == 0
    except Exception:
        return False


def ytsearch(query: str, n: int = 5) -> list[dict]:
    """Real web reach: YouTube search via yt-dlp flat playlist."""
    import subprocess
    try:
        r = subprocess.run(
            [YTDLP, "--flat-playlist", "--dump-single-json",
             f"--playlist-end", str(n), f"ytsearch{n}:{query}"],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
        out = []
        for e in data.get("entries", []):
            # skip livestreams/upcoming — they can't be clipped offline
            live = str(e.get("live_status") or "").lower()
            if e.get("is_live") or live in ("is_live", "is_upcoming"):
                continue
            dur = e.get("duration") or 0
            title = e.get("title", "")
            url = e.get("webpage_url") or e.get("url") or ""
            if url and not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={url}"
            out.append({
                "title": title,
                "url": url,
                "duration": dur,
                "clip_seconds": min(dur, CLIP_SECONDS) if dur else CLIP_SECONDS,
                "uploader": e.get("uploader") or e.get("channel"),
                "tags": title.lower().split()[:12],
                "origin": "web",
                "size_mb": 8,
            })
        return [c for c in out if c["url"]]
    except Exception:
        return []


@_serialized
def fetch_youtube(entry: dict) -> dict | None:
    """Download a web result through yt-dlp -> inbox -> identity store."""
    import subprocess
    import shutil
    # without ffmpeg we can't section-cut; refuse very long sources
    if not ffmpeg_ok() and entry.get("duration", 0) > 900:
        return {"error": "source too long (ffmpeg needed for clip cut)",
                "title": entry["title"]}
    inbox = ROOT / "videos" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_"
                   for c in entry["title"])[:60] or "webclip"
    dest = inbox / f"{safe}.mp4"
    has_ff = ffmpeg_ok()
    # without ffmpeg: single-file progressive format only (no merge/remux)
    fmt = ("bv*[height<=720]+ba/b[height<=720]/b" if has_ff
           else "b[height<=720]/b")
    base = [YTDLP, "--no-playlist", "--socket-timeout", "30"]
    attempts = []
    if has_ff:
        attempts.append(
            ["-f", "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]"
                  "/b[ext=mp4]/b[height<=720]/b",
             "--merge-output-format", "mp4", "--remux-video", "mp4",
             "--download-sections",
             f"*00:00:00-00:{CLIP_SECONDS // 60:02d}:{CLIP_SECONDS % 60:02d}"])
    else:
        attempts.append(["-f", "b[height<=720][ext=mp4]/b[ext=mp4]/b"])
    attempts.append([])  # last resort: yt-dlp default format
    r = None
    for extra in attempts:
        for p in inbox.glob(f"{safe}.*"):
            p.unlink(missing_ok=True)
        r = subprocess.run(base + extra +
                           ["-o", str(dest.with_suffix("")) + ".%(ext)s",
                            entry["url"]],
                           capture_output=True, text=True, timeout=600)
        vids = [p for p in inbox.glob(f"{safe}.*")
                if p.suffix.lower() in (".mp4", ".mkv", ".webm")
                and p.stat().st_size > 50000]
        if vids:
            break
    # yt-dlp may append ext; find the produced file
    produced = list(inbox.glob(f"{safe}.*"))
    pref = {".mp4": 0, ".mov": 1, ".mkv": 2, ".webm": 3}
    vids = sorted((p for p in produced
                   if p.suffix.lower() in pref),
                  key=lambda p: pref[p.suffix.lower()])
    video = vids[0] if vids else None
    if not video or video.stat().st_size < 50000:
        for p in produced:
            p.unlink(missing_ok=True)
        return {"error": "yt-dlp failed", "title": entry["title"],
                "stderr": (r.stderr or "")[-300:]}
    if shutil.disk_usage(ROOT).free < video.stat().st_size * 2:
        video.unlink(missing_ok=True)
        return {"error": "storage gate", "title": entry["title"]}
    stored = restash_if_poor(identity.store_file(str(video),
                                               skip_score_gate=True))
    for p in produced:
        if p != video:
            p.unlink(missing_ok=True)
    video.unlink(missing_ok=True)  # inbox transient; store is canonical
    register_peer_url(entry["url"], entry["title"], entry.get("tags", []),
                      "web")
    return {"title": entry["title"], "url": entry["url"],
            "origin": "web", "uploader": entry.get("uploader"),
            "stored": stored.get("stored"),
            "categories": stored.get("categories")}


def is_twitch(url: str) -> bool:
    u = (url or "").lower()
    return "twitch.tv" in u or "twitch." in u.split("/")[2] \
        if "://" in u else "twitch.tv" in u


def fetch_twitch(url: str, title: str | None = None) -> dict:
    """Twitch links: clips/VODs go through the yt-dlp clipper;
    live channels resolve to their HLS edge and we cut a bounded
    clip with ffmpeg (section-cutting can't work on live)."""
    u = url.lower()
    live = ("twitch.tv" in u and "/videos/" not in u
            and "clips.twitch.tv" not in u and "/clip/" not in u
            and "/directory/" not in u and "/downloads/" not in u)
    if not live:
        return fetch_youtube({
            "title": title or u.rstrip("/").split("/")[-1][:60]
                     or "twitch",
            "url": url, "tags": ["twitch"], "origin": "web",
            "size_mb": 8})
    # live: yt-dlp -g resolves the m3u8 edge(s)
    import subprocess
    r = subprocess.run([YTDLP, "-g", "--socket-timeout", "20", url],
                       capture_output=True, text=True, timeout=60)
    m3u8 = next((ln.strip() for ln in r.stdout.splitlines()
                 if ".m3u8" in ln and ln.startswith("http")), "")
    if not m3u8:
        return {"error": "live stream not resolvable (offline?)",
                "url": url}
    ff = None
    try:
        import passkeys
        ff = passkeys._ffmpeg()
    except Exception:
        pass
    ff = ff or "ffmpeg"
    inbox = ROOT / "videos" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_"
                   for c in (title or u.split("/")[-1]))[:60] \
        or "twitchlive"
    dest = inbox / f"{safe}.mp4"
    cr = subprocess.run(
        [ff, "-y", "-t", str(CLIP_SECONDS), "-i", m3u8,
         "-c", "copy", "-bsf:a", "aac_adtstoasc", str(dest)],
        capture_output=True, timeout=CLIP_SECONDS + 120)
    if cr.returncode != 0 or not dest.exists() \
            or dest.stat().st_size < 100000:
        dest.unlink(missing_ok=True)
        return {"error": "live clip cut failed", "url": url,
                "stderr": (cr.stderr or b"").decode()[-300:]}
    stored = restash_if_poor(identity.store_file(
        str(dest), skip_score_gate=True))
    dest.unlink(missing_ok=True)
    register_peer_url(url, title or safe, ["twitch", "live"], "web")
    # keep the channel warm: viewer bots hold the live edge open
    chan = u.split("twitch.tv/")[-1].strip("/").split("/")[0]
    twitch.ensure(chan, title=title or safe)
    return {"title": title or safe, "url": url, "origin": "twitch",
            "live": True, "stored": stored.get("stored"),
            "categories": stored.get("categories")}


def fetch_any_url(url: str, title: str | None = None) -> dict:
    """Reach ANY outlet: yt-dlp handles 700+ sites + direct files;
    curl fallback for plain media URLs."""
    if is_twitch(url):
        res = fetch_twitch(url, title)
        if res and "error" not in res:
            return res
    slug = title or url.split("/")[-1].rsplit(".", 1)[0] or "outlet"
    entry = {"title": slug, "url": url, "tags": slug.lower().split(),
             "origin": "direct", "size_mb": 50,
             "uploader": url.split("/")[2] if "://" in url else ""}
    res = fetch_youtube(entry)
    if res and "error" not in res:
        return res
    res2 = fetch_url(entry)
    if res2 and "error" not in res2:
        return res2
    # walled host? try a stored img/vid passkey for this site
    try:
        host = urllib.parse.urlsplit(url).netloc
        pk = passkeys.recall(host)
        if pk and pk != url:
            res3 = fetch_any_url(pk, title=title)
            if res3 and "error" not in res3:
                res3["passkey"] = True
                return res3
    except Exception:
        pass
    return res or res2 or {"error": "unreachable", "url": url}


def peer_hubs() -> list[str]:
    hubs = load_json(PEER_HUBS_PATH, [])
    return [h for h in hubs if isinstance(h, str) and h.startswith("http")]


def relay_query(query: str) -> list[dict]:
    """Transmit the query to peer hubs; merge their outlet results.
    This is the 'frequency over the network' — peers answer with what
    they already stock."""
    import concurrent.futures
    hubs = peer_hubs()
    if not hubs:
        return []

    def ask(base):
        try:
            req = urllib.request.Request(
                base.rstrip("/") + "/api/feed?q=" + urllib.parse.quote(query),
                headers={"User-Agent": "ATLAS-peer/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read())
        except Exception:
            return []

    merged = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for items in ex.map(ask, hubs):
            for it in items if isinstance(items, list) else []:
                it["peer_hub"] = True
                merged.append(it)
    return merged


def fetch_for_query(query: str, keywords: list[str]) -> dict:
    """Smart reach: real web search (ytsearch) first, then catalogue+peer
    candidates with uptime checks. Auto-fetches through the store pipeline."""
    # --- 1) real network reach: YouTube/web video search ---
    web = ytsearch(query, 5)
    existing = {p.stem.lower() for p in video_files()}
    web = [w for w in web
           if "".join(c if c.isalnum() or c in "-_" else "_"
                      for c in w["title"])[:60].lower() not in existing]
    if not ffmpeg_ok():
        # no ffmpeg → can't clip long sources; only short ones are fetchable
        web_fetchable = [w for w in web if w.get("duration", 0) <= 900]
    else:
        web_fetchable = web
    if web_fetchable:
        pick = llm.match_catalogue(query, {"keywords": keywords},
                                   web_fetchable) or web_fetchable[0]
        fetched = fetch_youtube(pick)
        broadcast("fetched", {"q": query, "fetched": fetched})
        web_cands = [{"title": w["title"], "uploader": w.get("uploader"),
                      "duration": w.get("duration"), "url": w["url"],
                      "origin": "web"} for w in web]
        if fetched and "error" not in fetched:
            return {"query": query, "expanded_keywords": keywords,
                    "fetched": fetched, "origin": "web",
                    "web_candidates": web_cands}
        web_note = fetched
    else:
        web_cands = []
        web_note = {"note": "web search empty"}

    # --- 2) catalogue + peer pool fallback: only genuinely relevant hits ---
    cands = all_candidates(keywords or [query])
    top = [c for c in cands if c["_score"] > 0][:8]
    if not top:
        return {"query": query, "fetched": None, "web": web_note,
                "web_candidates": web_cands,
                "note": "no relevant local candidates — web options above"}

    statuses = check_uptimes([c["url"] for c in top])
    live = [c for c in top if statuses.get(c["url"], {}).get("ok")]
    for c in top:
        c["uptime"] = statuses.get(c["url"], {})

    pick = None
    if live:
        pick = llm.match_catalogue(query, {"keywords": keywords},
                                   [{k: v for k, v in c.items() if k != "_score"}
                                    for c in live])
        if pick:
            for c in live:
                if c["url"] == pick.get("url") or c["title"] == pick.get("title"):
                    pick = c
                    break
    if not pick:
        pick = live[0] if live else None
    if not pick:
        return {"query": query, "fetched": None, "web": web_note,
                "web_candidates": web_cands,
                "candidates": [{"title": c["title"], "uptime": c["uptime"]}
                               for c in top],
                "note": "all candidates unreachable"}

    fetched = fetch_url(pick)
    broadcast("fetched", {"q": query, "fetched": fetched})
    return {"query": query, "expanded_keywords": keywords,
            "fetched": fetched, "web": web_note,
            "web_candidates": web_cands,
            "candidates": [{"title": c["title"], "origin": c["origin"],
                            "uptime": c["uptime"]} for c in top]}


# ---------- node / status ----------

_tuner_state = {"last_query": None, "last_fetch_ts": 0.0, "fetches": 0}


def stream_tuner():
    """Keeps the stream alive: rotates fresh queries on an interval and
    pulls new media through the same fetch pipeline. Skips when fresh
    content already landed (manual searches count)."""
    profile = identity.load_profile()
    cfg = profile.get("stream_tuner", {})
    if not cfg.get("enabled"):
        return
    interval = int(cfg.get("interval_s", 300))
    queries = cfg.get("queries") or ["nature", "city", "ocean", "space"]
    i = 0
    while True:
        time.sleep(interval)
        try:
            newest = max((p.stat().st_mtime for p in video_files()),
                         default=0)
            if time.time() - newest < interval * 0.8:
                continue
            q = queries[i % len(queries)]
            i += 1
            exp = llm.expand_query(q, profile)
            kws = exp.get("keywords", [q]) if exp else [q]
            res = fetch_for_query(q, kws)
            _tuner_state["last_query"] = q
            _tuner_state["last_fetch_ts"] = time.time()
            if res.get("fetched") and "error" not in (res.get("fetched") or {}):
                _tuner_state["fetches"] += 1
        except Exception:
            pass


def community_sync():
    """Gossip loop: pull peer wishlists + resolved maps so community
    descriptors (magnet/peer/hls) get resolved across the mesh.
    Any hub with a resolver publishes via POST /api/resolve; everyone
    else adopts the fetchable URL into catalogue.d + peer pool."""
    while True:
        time.sleep(120)
        for base in peer_hubs():
            try:
                req = urllib.request.Request(
                    base.rstrip("/") + "/api/resolved",
                    headers={"User-Agent": "ATLAS-peer/1.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    remote = json.loads(r.read()).get("resolved", {})
                local = load_json(DATA / "resolved.json", {})
                changed = False
                for k, rec in remote.items():
                    if k not in local and str(rec.get("url", "")).startswith("http"):
                        local[k] = rec
                        ingest.ingest({"title": rec.get("title", k),
                                       "url": rec["url"],
                                       "tags": rec.get("tags", []),
                                       "source": "community",
                                       "node": rec.get("resolved_by", base)})
                        register_peer_url(rec["url"], rec.get("title", k),
                                          rec.get("tags", []),
                                          "community-resolved")
                        changed = True
                if changed:
                    save_json(DATA / "resolved.json", local)
                    broadcast("peers", {"resolved_count": len(local)})
            except Exception:
                pass


TORRENT_JOBS = DATA / "torrent_jobs.json"


def torrent_worker():
    """BitTorrent plugin: resolves wishlist magnets locally via the
    webtorrent runtime, stores the video, and publishes a LAN stream
    URL back into resolved.json + the peer pool for the whole mesh."""
    while True:
        time.sleep(90)
        try:
            if not bittorrent.available():
                continue
            wants = [e for e in ingest.extra_catalogue()
                     if e.get("type") == "magnet" and not e.get("fetchable")]
            if not wants:
                continue
            jobs = load_json(TORRENT_JOBS, {})
            resolved = load_json(DATA / "resolved.json", {})
            now = time.time()
            pending = [w for w in wants
                       if w["uri"] not in resolved
                       and jobs.get(w["uri"], {}).get("status") != "done"
                       and now - jobs.get(w["uri"], {}).get("ts", 0) > 1800]
            if not pending:
                continue
            w = pending[0]
            uri = w["uri"]
            jobs[uri] = {"status": "running", "ts": now}
            save_json(TORRENT_JOBS, jobs)
            import shutil
            if shutil.disk_usage(ROOT).free < 2e9:
                jobs[uri] = {"status": "skipped-storage", "ts": time.time()}
                save_json(TORRENT_JOBS, jobs)
                continue
            slug = "".join(c if c.isalnum() or c in "-_" else "_"
                           for c in w.get("title", "torrent"))[:50]
            work = ROOT / "videos" / "inbox" / f"bt_{slug}"
            video = bittorrent.resolve(uri, work, timeout=600)
            if not video:
                jobs[uri] = {"status": "failed", "ts": time.time()}
                save_json(TORRENT_JOBS, jobs)
                continue
            if video.stat().st_size > shutil.disk_usage(ROOT).free * 0.4:
                video.unlink(missing_ok=True)
                jobs[uri] = {"status": "skipped-storage", "ts": time.time()}
                save_json(TORRENT_JOBS, jobs)
                continue
            stored = restash_if_poor(identity.store_file(
                str(video), skip_score_gate=True))
            stored_path = stored.get("stored")
            pub_url = None
            if stored_path:
                try:
                    rel = str(Path(stored_path).resolve()
                              .relative_to(ROOT))
                    pub_ip = next((i for i in lan_ips()
                                   if not i.startswith("127.")),
                                  lan_ips()[0])
                    pub_url = ("http://" + pub_ip + ":8917/api/stream"
                               "?path=" + urllib.parse.quote(rel))
                except Exception:
                    pass
            res = load_json(DATA / "resolved.json", {})
            res[uri] = {"key": uri, "url": pub_url or str(video),
                        "title": w.get("title", uri),
                        "tags": w.get("tags", []),
                        "resolved_by": "bittorrent-plugin",
                        "ts": time.time()}
            save_json(DATA / "resolved.json", res)
            if pub_url:
                ingest.ingest({"title": w.get("title", uri), "url": pub_url,
                               "tags": w.get("tags", []),
                               "source": "bittorrent",
                               "node": "self"})
                register_peer_url(pub_url, w.get("title", uri),
                                  w.get("tags", []), "bittorrent")
            shutil.rmtree(work, ignore_errors=True)
            jobs[uri] = {"status": "done", "ts": time.time(),
                         "stored": stored_path}
            save_json(TORRENT_JOBS, jobs)
            broadcast("fetched", {"q": uri, "fetched": {
                "title": w.get("title"), "url": pub_url,
                "origin": "bittorrent", "stored": stored_path}})
        except Exception:
            pass


def node_status() -> dict:
    import shutil
    profile = identity.load_profile()
    u = shutil.disk_usage(ROOT)
    return {
        "identity": profile["identity"],
        "receiver": {
            "bind": "0.0.0.0",
            "lan_ips": lan_ips(),
            "uptime_s": round(time.time() - _started, 1),
            "unique_clients": len(_clients),
            "requests": _requests,
            "sse_subscribers": len(_subs),
        },
        "processor": {
            "radio": beacon.wifi_info(),
            "beacon_port": beacon.BEACON_PORT,
            "ollama_online": bool(llm.list_models()),
            "ollama_models": llm.list_models(),
            "observer_model": profile.get("observer_model"),
            "videos_indexed": len(video_files()),
            "peer_urls": len(peer_pool()),
            "ingested_outlets": len(ingest.extra_catalogue()),
            "community": {
                "wishlist": sum(1 for e in ingest.extra_catalogue()
                                if not e.get("fetchable")),
                "resolved": len(load_json(DATA / "resolved.json", {})),
            },
            "api_keys": api_keys.status()["users"],
            "tuner": {**_tuner_state,
                      "enabled": bool(identity.load_profile()
                                      .get("stream_tuner", {}).get("enabled"))},
            "storage": {"free_gb": round(u.free / 1e9, 1),
                        "used_gb": round(u.used / 1e9, 1),
                        "total_gb": round(u.total / 1e9, 1)},
        },
    }


def storage_report() -> dict:
    import shutil
    u = shutil.disk_usage(ROOT)
    return {"free_gb": round(u.free / 1e9, 1),
            "used_gb": round(u.used / 1e9, 1),
            "total_gb": round(u.total / 1e9, 1)}


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # -- session cookies: stable per-browser id for round trips --
    def _session(self):
        if hasattr(self, "_sid"):
            return self._sid, self._new_sid
        cookie = self.headers.get("Cookie", "")
        sid = None
        for part in cookie.split(";"):
            if part.strip().startswith("atlas_sid="):
                sid = part.strip().split("=", 1)[1]
        self._new_sid = not sid
        self._sid = sid or uuid.uuid4().hex[:16]
        return self._sid, self._new_sid

    def _cookie_header(self):
        if getattr(self, "_new_sid", False):
            return (f"atlas_sid={self._sid}; Path=/; Max-Age=31536000; "
                    "SameSite=Lax; HttpOnly")
        return None

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        ck = self._cookie_header()
        if ck:
            self.send_header("Set-Cookie", ck)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _client_ip(self):
        return self.client_address[0]

    def _json_body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def _track_key(self):
        """Record X-ATLAS-Key usage if a bearer key is presented."""
        if hasattr(self, "_key_meta"):
            return self._key_meta
        key = self.headers.get("X-ATLAS-Key", "")
        self._key_meta = api_keys.check_key(key) if key else None
        return self._key_meta

    def _auth_required(self) -> bool:
        try:
            return bool(identity.load_profile()
                        .get("api", {}).get("auth_required"))
        except Exception:
            return False

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # -- SSE: real-time round trips --
    def do_GET_events(self):
        self._session()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        ck = self._cookie_header()
        if ck:
            self.send_header("Set-Cookie", ck)
        self.end_headers()
        q = queue.Queue(maxsize=200)
        _subs.append(q)
        try:
            # hello event so clients know they're live
            q.put_nowait("event: hello\ndata: {}\n\n")
            last = time.time()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
                except queue.Empty:
                    if time.time() - last > 15:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        last = time.time()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if q in _subs:
                _subs.remove(q)

    def do_GET(self):
        global _requests
        _requests += 1
        self._session()
        ip = self._client_ip()
        _clients[ip] = time.time()
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        path = u.path
        self._track_key()

        try:
            if path == "/api/events":
                return self.do_GET_events()
            if path in ("/", "/index.html"):
                html = (WEB / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.send_header("Cache-Control", "no-cache")
                ck = self._cookie_header()
                if ck:
                    self.send_header("Set-Cookie", ck)
                self.end_headers()
                self.wfile.write(html)
                return
            if path == "/api/feed":
                query = qs.get("q", [""])[0]
                cat = qs.get("cat", [""])[0]
                # fast path: literal token match, no LLM wait
                local = feed(query, cat)
                if not local and query:
                    exp = llm.expand_query(query, identity.load_profile())
                    if exp:
                        local = feed(query, cat, exp.get("keywords"))
                self._send(200, local)
                return
            if path == "/api/search":
                query = qs.get("q", [""])[0]
                want_fetch = qs.get("fetch", ["0"])[0] in ("1", "true")
                # a raw URL query goes straight to the outlet
                if query.startswith("http"):
                    fetched = fetch_any_url(query) if want_fetch else None
                    self._send(200, {"q": query, "results": [],
                                     "fetched": fetched})
                    return
                keywords = None
                local = feed(query, "")
                if not local and query:
                    exp = llm.expand_query(query, identity.load_profile())
                    if exp:
                        keywords = exp.get("keywords", [query])
                        local = feed(query, "", keywords)
                # transmit query to peer hubs; merge what they stock
                peer_items = relay_query(query) if query else []
                seen = {i["src"] for i in local}
                local += [i for i in peer_items if i.get("src") not in seen]
                fetched = fetch_for_query(query, keywords or [query]) \
                    if (want_fetch and query) else None
                # transmit the query frequency to peer hubs so they can
                # fetch/stock on their side too (fire-and-forget)
                if want_fetch and query:
                    def _relay_fetch():
                        for base in peer_hubs():
                            try:
                                req = urllib.request.Request(
                                    base.rstrip("/") + "/api/search?q="
                                    + urllib.parse.quote(query) + "&fetch=1",
                                    method="POST", data=b"{}",
                                    headers={"Content-Type": "application/json",
                                             "User-Agent": "ATLAS-peer/1.0"})
                                urllib.request.urlopen(req, timeout=10).read()
                            except Exception:
                                pass
                    threading.Thread(target=_relay_fetch, daemon=True).start()
                web_cands = (fetched or {}).get("web_candidates") or []
                disc = [
                    {"title": c["title"], "origin": c["origin"],
                     "tags": c["tags"][:5]}
                    for c in all_candidates(keywords or [query])[:6]
                ]
                disc += [{"title": w["title"], "origin": "web",
                          "url": w["url"], "tags": [w.get("uploader") or "web"][:1]}
                         for w in web_cands[:6]]
                self._send(200, {
                    "q": query, "results": local, "fetched": fetched,
                    "peer_results": len(peer_items),
                    "discovery": disc,
                    "processor": {"ollama_online": bool(llm.list_models()),
                                  "vision_model": vision.vision_model()},
                })
                return
            if path == "/api/auth/status":
                self._send(200, api_keys.status())
                return
            if path == "/api/manga":
                q = qs.get("q", [""])[0]
                if not q:
                    self._send(400, {"error": "q required"})
                    return
                src = qs.get("source", [""])[0]
                if src:
                    if src in manga.MADARA_SITES:
                        fn = lambda q: manga.madara_search(q, src)  # noqa: E731
                    elif src == "aniwatch":
                        fn = aniwatch.search
                    else:
                        fn = {"mangadex": manga.search,
                              "asura": manga.asura_search}.get(src)
                    self._send(200, {"results": fn(q) if fn else [],
                                     "source": src})
                else:
                    self._send(200, {"results": manga.search_all(q)})
                return
            if path == "/api/manga/chapters":
                mid = qs.get("id", [""])[0]
                src = qs.get("source", ["mangadex"])[0]
                if not mid:
                    self._send(400, {"error": "id required"})
                    return
                lim = int(qs.get("limit", ["30"])[0] or 30)
                self._send(200, {"chapters": manga.chapters_for(
                    src, mid, limit=lim)})
                return
            if path == "/api/aniwatch":
                q = qs.get("q", [""])[0]
                if not q:
                    self._send(400, {"error": "q required"})
                    return
                self._send(200, {"results": aniwatch.search(q)})
                return
            if path == "/api/aniwatch/episodes":
                aid = qs.get("id", [""])[0]
                if not aid:
                    self._send(400, {"error": "id required"})
                    return
                lim = int(qs.get("limit", ["30"])[0] or 30)
                self._send(200, {"episodes": aniwatch.episodes(aid, lim)})
                return
            if path == "/api/social":
                self._send(200, social.status())
                return
            if path == "/api/lore":
                import unlock
                key = qs.get("key", [""])[0]
                reg = unlock.registry()
                if key:
                    self._send(200, {"key": key,
                                     "entry": reg.get("keys", {})
                                     .get(key)})
                else:
                    self._send(200, reg)
                return
            if path == "/api/nexus":
                import nexus
                title = qs.get("title", [""])[0]
                if title:
                    self._send(200, nexus.nexus_refs(
                        title, mod_count=int(
                            qs.get("mods", ["10"])[0])))
                else:
                    self._send(200, nexus.status())
                return
            if path == "/api/gamerefs":
                import gamerefs
                title = qs.get("title", [""])[0]
                if title:
                    self._send(200, gamerefs.match_title(title))
                elif qs.get("batch", [""])[0]:
                    lim = int(qs.get("limit", ["25"])[0])
                    self._send(200, gamerefs.match_dlps(limit=lim))
                elif qs.get("nexus", [""])[0]:
                    lim = int(qs.get("limit", ["15"])[0])
                    self._send(200, gamerefs.apply_nexus_titles(
                        count=lim))
                else:
                    self._send(200, gamerefs.status())
                return
            if path == "/api/mods":
                import modlib
                if qs.get("enrich", [""])[0]:
                    self._send(200, modlib.enrich_file())
                elif qs.get("discover", [""])[0]:
                    self._send(200, modlib.model_discover())
                else:
                    self._send(200, modlib.list_mods())
                return
            if path == "/api/partition":
                import partition
                profile = qs.get("profile", ["default"])[0]
                if qs.get("run", [""])[0]:
                    self._send(200, partition.run(profile))
                else:
                    self._send(200, partition.partition_status(profile))
                return
            if path == "/api/unlock":
                import unlock
                title = qs.get("title", qs.get("q", [""]))[0]
                profile = qs.get("profile", ["default"])[0]
                if not title:
                    self._send(400, {"error": "title required"})
                    return
                if qs.get("status", [""])[0]:
                    self._send(200, unlock.profile_status(profile))
                    return
                dl = qs.get("download", ["1"])[0] not in ("0", "false")
                res = unlock.unlock_title(title, profile,
                                          download=dl)
                c = unlock.unlock_comment(res)
                res["comment"] = c
                self._send(200, res)
                return
            if path == "/api/tunnel":
                st = tunnel.status()
                st["cert"] = tunnel.cert_status()
                self._send(200, st)
                return
            if path == "/api/auto":
                state = load_json(AUTO_PATH, {"counter": 0})
                self._send(200, {"spec": "AUTO-01", "rule": "add 1",
                                 **state})
                return
            if path == "/api/browser":
                url = qs.get("url", [""])[0]
                if not url:
                    self._send(200, browser.status())
                    return
                r = browser.render(url, wait_ms=int(
                    qs.get("wait", ["4000"])[0] or 4000))
                if qs.get("shot", ["0"])[0] != "1":
                    r.pop("screenshot", None)
                self._send(200, r)
                return
            if path == "/api/passkeys":
                site = qs.get("site", [""])[0]
                kind = qs.get("kind", [""])[0] or None
                if site:
                    hit = passkeys.recall(site, kind)
                    self._send(200, {"site": site, "url": hit})
                else:
                    self._send(200, passkeys.list_all())
                return
            if path == "/api/sites":
                self._send(200, sites.list_sites())
                return
            if path == "/api/earn":
                node = qs.get("node", [""])[0] or None
                self._send(200, economy.status(node))
                return
            if path == "/api/book":
                self._send(200, cardinal.status())
                return
            if path == "/api/names":
                q = qs.get("q", [""])[0]
                if q:
                    self._send(200, namescrape.lookup(q))
                else:
                    self._send(200, namescrape.status())
                return
            if path == "/api/consensus":
                self._send(200, nameconsensus.status())
                return
            if path == "/api/cast":
                self._send(200, caster.status())
                return
            if path == "/api/spectrum":
                import spectrum
                action = qs.get("action", ["status"])[0]
                if action == "analyze":
                    self._send(200, spectrum.composite_analysis(
                        qs.get("text", [""])[0]))
                elif action == "run":
                    ds = qs.get("dataset", [""])[0]
                    self._send(200, spectrum.run_dataset(
                        Path(ds) if ds else
                        ROOT / "data" / "spatial" / "predictions.jsonl",
                        label=qs.get("label", [""])[0]))
                elif action == "repeat":
                    self._send(200, spectrum.repeater(
                        interval_s=float(qs.get("interval", ["60"])[0]),
                        cycles=int(qs.get("cycles", ["1"])[0])))
                elif action == "gguf":
                    self._send(200, spectrum.pack_gguf())
                else:
                    self._send(200, spectrum.status())
                return
            if path == "/api/resets":
                import resets
                action = qs.get("action", ["status"])[0]
                if action == "calendar":
                    self._send(200, resets.calendar())
                elif action == "record":
                    self._send(200, resets.record_reset(
                        qs.get("node", [""])[0],
                        qs.get("to", [""])[0],
                        qs.get("from", [""])[0] or None,
                        qs.get("profile", ["default"])[0],
                        qs.get("cause", ["player_reset"])[0]))
                else:
                    self._send(200, resets.status())
                return
            if path == "/api/sessions":
                import sessions
                action = qs.get("action", ["status"])[0]
                if action == "event":
                    import json as _json
                    try:
                        _in = _json.loads(qs.get("inputs",
                                     ["{}"])[0])
                    except Exception:
                        _in = {}
                    try:
                        _out = _json.loads(qs.get("outputs",
                                      ["{}"])[0])
                    except Exception:
                        _out = {}
                    self._send(200, sessions.record_event(
                        qs.get("session", ["default"])[0],
                        inputs=_in, outputs=_out,
                        node=qs.get("node", [""])[0] or None,
                        cause=qs.get("cause",
                                     ["player"])[0]))
                elif action == "session":
                    self._send(200, sessions.session_events(
                        qs.get("id", ["default"])[0]))
                else:
                    self._send(200, sessions.status())
                return
            if path == "/api/ripples":
                import ripples
                action = qs.get("action", ["status"])[0]
                if action == "detect":
                    self._send(200, ripples.detect(
                        qs.get("entity", [""])[0],
                        qs.get("from", [""])[0],
                        qs.get("to", [""])[0],
                        qs.get("via", ["statement"])[0],
                        kind_hint=qs.get("kind", [""])[0]
                        or None))
                elif action == "scan":
                    self._send(200, ripples.scan())
                else:
                    self._send(200, ripples.status())
                return
            if path == "/api/multi":
                import multiplayer as mp
                action = qs.get("action", ["status"])[0]
                if action == "join":
                    self._send(200, mp.join(
                        qs.get("session", ["lobby"])[0],
                        qs.get("char", [""])[0],
                        qs.get("profile", ["default"])[0],
                        qs.get("phrase", [""])[0] or None,
                        qs.get("label", [""])[0] or None))
                elif action == "say":
                    self._send(200, mp.say(
                        qs.get("session", ["lobby"])[0],
                        qs.get("instance", [""])[0],
                        qs.get("text", [""])[0]))
                elif action == "leave":
                    self._send(200, mp.leave(
                        qs.get("session", ["lobby"])[0],
                        qs.get("instance", [""])[0]))
                elif action == "roster":
                    self._send(200, mp.roster(
                        qs.get("session", [""])[0] or None))
                elif action == "chat":
                    self._send(200, mp.chatlog(
                        qs.get("session", ["lobby"])[0],
                        int(qs.get("limit", ["100"])[0])))
                elif action == "instance":
                    self._send(200, mp.instance(
                        qs.get("id", [""])[0]))
                else:
                    self._send(200, mp.status())
                return
            if path == "/api/forum":
                import forum
                action = qs.get("action", ["status"])[0]
                if action == "load":
                    self._send(200, forum.load(
                        qs.get("url", [""])[0],
                        qs.get("profile", ["default"])[0]))
                else:
                    self._send(200, forum.status())
                return
            if path == "/api/scaffold":
                import scaffold
                action = qs.get("action", ["status"])[0]
                if action == "fill":
                    self._send(200, scaffold.fill(
                        qs.get("name", ["scaffold_block"])[0]))
                else:
                    self._send(200, scaffold.status())
                return
            if path == "/api/genesis":
                import genesis
                action = qs.get("action", ["status"])[0]
                if action == "observe":
                    self._send(200, genesis.observe(
                        qs.get("node", [""])[0],
                        qs.get("profile", ["default"])[0],
                        ticks=int(qs.get("ticks", ["100"])[0])))
                elif action == "set":
                    self._send(200, genesis.reality_set())
                else:
                    self._send(200, genesis.status())
                return
            if path == "/api/tierlayers":
                import tierlayers
                action = qs.get("action", ["status"])[0]
                if action == "range":
                    self._send(200, tierlayers.output_range(
                        qs.get("node", [""])[0],
                        qs.get("profile", ["default"])[0]))
                elif action == "game":
                    self._send(200, tierlayers.the_game_layer())
                elif action == "target":
                    self._send(200, tierlayers.target_all(
                        qs.get("node", [""])[0],
                        qs.get("skill", [""])[0],
                        qs.get("profile", ["default"])[0]))
                else:
                    self._send(200, tierlayers.status())
                return
            if path == "/api/stack":
                import stack
                action = qs.get("action", ["status"])[0]
                if action == "build":
                    self._send(200, stack.build_stack(
                        qs.get("node", [""])[0],
                        qs.get("profile", ["default"])[0]))
                elif action == "warp":
                    env = {}
                    try:
                        env = json.loads(qs.get("env", ["{}"])[0])
                    except Exception:
                        pass
                    self._send(200, stack.warp(
                        qs.get("node", [""])[0],
                        qs.get("plane", ["plane_3d"])[0], env,
                        qs.get("profile", ["default"])[0]))
                elif action == "engage":
                    env = {}
                    try:
                        env = json.loads(qs.get("env", ["{}"])[0])
                    except Exception:
                        pass
                    self._send(200, stack.engage(
                        qs.get("a", [""])[0],
                        qs.get("b", [""])[0],
                        qs.get("plane", ["plane_3d"])[0], env,
                        qs.get("profile", ["default"])[0]))
                else:
                    self._send(200, stack.status(
                        qs.get("profile", ["default"])[0]))
                return
            if path == "/api/mechanics":
                import mechanics
                action = qs.get("action", ["status"])[0]
                if action == "combos":
                    self._send(200, {"combos":
                                     mechanics.combos2()})
                elif action == "expand":
                    self._send(200, mechanics.expand(
                        qs.get("profile", ["default"])[0],
                        limit_combos=int(
                            qs.get("limit", ["0"])[0])))
                elif action == "assign":
                    self._send(200, mechanics.assign(
                        qs.get("node", [""])[0],
                        qs.get("combo", [""])[0],
                        qs.get("trigger", [""])[0],
                        qs.get("profile", ["default"])[0]))
                elif action == "fire":
                    env = {}
                    try:
                        env = json.loads(qs.get("env", ["{}"])[0])
                    except Exception:
                        pass
                    self._send(200, mechanics.fire(
                        qs.get("trigger", [""])[0], env,
                        qs.get("profile", ["default"])[0]))
                else:
                    self._send(200, mechanics.status(
                        qs.get("profile", ["default"])[0]))
                return
            if path == "/api/timelinecombos":
                import timelinecombos
                action = qs.get("action", ["status"])[0]
                if action == "expand":
                    self._send(200, timelinecombos.expand(
                        qs.get("profile", ["default"])[0]))
                elif action == "combos":
                    self._send(200, {"combos":
                                     timelinecombos.combos()})
                else:
                    self._send(200, timelinecombos.status(
                        qs.get("profile", ["default"])[0]))
                return
            if path == "/api/termlib":
                import termlib
                action = qs.get("action", ["status"])[0]
                if action == "ingest":
                    self._send(200, termlib.ingest(
                        per_seed=int(qs.get("n", ["25"])[0])))
                elif action == "terms":
                    self._send(200, {"terms": termlib.list_terms(
                        qs.get("cat", [""])[0] or None,
                        limit=int(qs.get("limit", ["50"])[0]))})
                elif action == "apply":
                    terms = [t for t in qs.get("terms", [""])[0]
                             .split(",") if t]
                    self._send(200, termlib.apply_selection(
                        qs.get("node", [""])[0], terms))
                elif action == "apply_branch":
                    terms = [t for t in qs.get("terms", [""])[0]
                             .split(",") if t]
                    self._send(200, termlib.apply_branch(
                        qs.get("branch", [""])[0], terms,
                        limit=int(qs.get("limit", ["5"])[0])))
                else:
                    self._send(200, termlib.status())
                return
            if path == "/api/variants":
                import variants
                action = qs.get("action", ["status"])[0]
                if action == "register":
                    n = variants.register()
                    variants.store()
                    self._send(200, {"registered": n,
                                     **variants.status()})
                elif action == "expand":
                    variants.register()
                    variants.store()
                    import truename
                    self._send(200, truename.discover())
                elif action == "config":
                    import variants as _v
                    self._send(200, _v.apply_config(
                        qs.get("cfg", [""])[0]))
                else:
                    self._send(200, variants.status())
                return
            if path == "/api/branches":
                import branches
                action = qs.get("action", ["status"])[0]
                if action == "map":
                    self._send(200, branches.branch_map(
                        qs.get("center", [""])[0] or None))
                else:
                    self._send(200, branches.status())
                return
            if path == "/api/resets":
                import resets
                action = qs.get("action", ["status"])[0]
                if action == "calendar":
                    self._send(200, resets.calendar())
                elif action == "record":
                    self._send(200, resets.record_reset(
                        qs.get("node", [""])[0],
                        qs.get("to", [""])[0],
                        qs.get("from", [""])[0] or None,
                        qs.get("profile", ["default"])[0],
                        qs.get("cause", ["player_reset"])[0]))
                else:
                    self._send(200, resets.status())
                return
            if path == "/api/sessions":
                import sessions
                action = qs.get("action", ["status"])[0]
                if action == "event":
                    import json as _json
                    try:
                        _in = _json.loads(qs.get("inputs",
                                     ["{}"])[0])
                    except Exception:
                        _in = {}
                    try:
                        _out = _json.loads(qs.get("outputs",
                                      ["{}"])[0])
                    except Exception:
                        _out = {}
                    self._send(200, sessions.record_event(
                        qs.get("session", ["default"])[0],
                        inputs=_in, outputs=_out,
                        node=qs.get("node", [""])[0] or None,
                        cause=qs.get("cause",
                                     ["player"])[0]))
                elif action == "session":
                    self._send(200, sessions.session_events(
                        qs.get("id", ["default"])[0]))
                else:
                    self._send(200, sessions.status())
                return
            if path == "/api/ripples":
                import ripples
                action = qs.get("action", ["status"])[0]
                if action == "detect":
                    self._send(200, ripples.detect(
                        qs.get("entity", [""])[0],
                        qs.get("from", [""])[0],
                        qs.get("to", [""])[0],
                        qs.get("via", ["statement"])[0],
                        kind_hint=qs.get("kind", [""])[0]
                        or None))
                elif action == "scan":
                    self._send(200, ripples.scan())
                else:
                    self._send(200, ripples.status())
                return
            if path == "/api/multi":
                import multiplayer as mp
                action = qs.get("action", ["status"])[0]
                if action == "join":
                    self._send(200, mp.join(
                        qs.get("session", ["lobby"])[0],
                        qs.get("char", [""])[0],
                        qs.get("profile", ["default"])[0],
                        qs.get("phrase", [""])[0] or None,
                        qs.get("label", [""])[0] or None))
                elif action == "say":
                    self._send(200, mp.say(
                        qs.get("session", ["lobby"])[0],
                        qs.get("instance", [""])[0],
                        qs.get("text", [""])[0]))
                elif action == "leave":
                    self._send(200, mp.leave(
                        qs.get("session", ["lobby"])[0],
                        qs.get("instance", [""])[0]))
                elif action == "roster":
                    self._send(200, mp.roster(
                        qs.get("session", [""])[0] or None))
                elif action == "chat":
                    self._send(200, mp.chatlog(
                        qs.get("session", ["lobby"])[0],
                        int(qs.get("limit", ["100"])[0])))
                elif action == "instance":
                    self._send(200, mp.instance(
                        qs.get("id", [""])[0]))
                else:
                    self._send(200, mp.status())
                return
            if path == "/api/forum":
                import forum
                action = qs.get("action", ["status"])[0]
                if action == "load":
                    self._send(200, forum.load(
                        qs.get("url", [""])[0],
                        qs.get("profile", ["default"])[0]))
                else:
                    self._send(200, forum.status())
                return
            if path == "/api/scaffold":
                import scaffold
                action = qs.get("action", ["status"])[0]
                if action == "fill":
                    self._send(200, scaffold.fill(
                        qs.get("name", ["scaffold_block"])[0]))
                else:
                    self._send(200, scaffold.status())
                return
            if path == "/api/genesis":
                import genesis
                action = qs.get("action", ["status"])[0]
                if action == "observe":
                    self._send(200, genesis.observe(
                        qs.get("node", [""])[0],
                        qs.get("profile", ["default"])[0],
                        ticks=int(qs.get("ticks", ["100"])[0])))
                elif action == "set":
                    self._send(200, genesis.reality_set())
                else:
                    self._send(200, genesis.status())
                return
            if path == "/api/tierlayers":
                import tierlayers
                action = qs.get("action", ["status"])[0]
                if action == "range":
                    self._send(200, tierlayers.output_range(
                        qs.get("node", [""])[0],
                        qs.get("profile", ["default"])[0]))
                elif action == "game":
                    self._send(200, tierlayers.the_game_layer())
                elif action == "target":
                    self._send(200, tierlayers.target_all(
                        qs.get("node", [""])[0],
                        qs.get("skill", [""])[0],
                        qs.get("profile", ["default"])[0]))
                else:
                    self._send(200, tierlayers.status())
                return
            if path == "/api/stack":
                import stack
                action = qs.get("action", ["status"])[0]
                if action == "build":
                    self._send(200, stack.build_stack(
                        qs.get("node", [""])[0],
                        qs.get("profile", ["default"])[0]))
                elif action == "warp":
                    env = {}
                    try:
                        env = json.loads(qs.get("env", ["{}"])[0])
                    except Exception:
                        pass
                    self._send(200, stack.warp(
                        qs.get("node", [""])[0],
                        qs.get("plane", ["plane_3d"])[0], env,
                        qs.get("profile", ["default"])[0]))
                elif action == "engage":
                    env = {}
                    try:
                        env = json.loads(qs.get("env", ["{}"])[0])
                    except Exception:
                        pass
                    self._send(200, stack.engage(
                        qs.get("a", [""])[0],
                        qs.get("b", [""])[0],
                        qs.get("plane", ["plane_3d"])[0], env,
                        qs.get("profile", ["default"])[0]))
                else:
                    self._send(200, stack.status(
                        qs.get("profile", ["default"])[0]))
                return
            if path == "/api/mechanics":
                import mechanics
                action = qs.get("action", ["status"])[0]
                if action == "combos":
                    self._send(200, {"combos":
                                     mechanics.combos2()})
                elif action == "expand":
                    self._send(200, mechanics.expand(
                        qs.get("profile", ["default"])[0],
                        limit_combos=int(
                            qs.get("limit", ["0"])[0])))
                elif action == "assign":
                    self._send(200, mechanics.assign(
                        qs.get("node", [""])[0],
                        qs.get("combo", [""])[0],
                        qs.get("trigger", [""])[0],
                        qs.get("profile", ["default"])[0]))
                elif action == "fire":
                    env = {}
                    try:
                        env = json.loads(qs.get("env", ["{}"])[0])
                    except Exception:
                        pass
                    self._send(200, mechanics.fire(
                        qs.get("trigger", [""])[0], env,
                        qs.get("profile", ["default"])[0]))
                else:
                    self._send(200, mechanics.status(
                        qs.get("profile", ["default"])[0]))
                return
            if path == "/api/timelinecombos":
                import timelinecombos
                action = qs.get("action", ["status"])[0]
                if action == "expand":
                    self._send(200, timelinecombos.expand(
                        qs.get("profile", ["default"])[0]))
                elif action == "combos":
                    self._send(200, {"combos":
                                     timelinecombos.combos()})
                else:
                    self._send(200, timelinecombos.status(
                        qs.get("profile", ["default"])[0]))
                return
            if path == "/api/termlib":
                import termlib
                action = qs.get("action", ["status"])[0]
                if action == "ingest":
                    self._send(200, termlib.ingest(
                        per_seed=int(qs.get("n", ["25"])[0])))
                elif action == "terms":
                    self._send(200, {"terms": termlib.list_terms(
                        qs.get("cat", [""])[0] or None,
                        limit=int(qs.get("limit", ["50"])[0]))})
                elif action == "apply":
                    terms = [t for t in qs.get("terms", [""])[0]
                             .split(",") if t]
                    self._send(200, termlib.apply_selection(
                        qs.get("node", [""])[0], terms))
                elif action == "apply_branch":
                    terms = [t for t in qs.get("terms", [""])[0]
                             .split(",") if t]
                    self._send(200, termlib.apply_branch(
                        qs.get("branch", [""])[0], terms,
                        limit=int(qs.get("limit", ["5"])[0])))
                else:
                    self._send(200, termlib.status())
                return
            if path == "/api/variants":
                import variants
                action = qs.get("action", ["status"])[0]
                if action == "register":
                    n = variants.register()
                    variants.store()
                    self._send(200, {"registered": n,
                                     **variants.status()})
                elif action == "expand":
                    variants.register()
                    variants.store()
                    import truename
                    self._send(200, truename.discover())
                elif action == "config":
                    import variants as _v
                    self._send(200, _v.apply_config(
                        qs.get("cfg", [""])[0]))
                else:
                    self._send(200, variants.status())
                return
            if path == "/api/branches":
                import branches
                action = qs.get("action", ["status"])[0]
                if action == "map":
                    self._send(200, branches.branch_map(
                        qs.get("center", [""])[0]))
                else:
                    self._send(200, branches.status())
                return
            if path == "/api/truename":
                import truename
                action = qs.get("action", ["status"])[0]
                if action == "discover":
                    self._send(200, truename.discover(
                        qs.get("center", ["reaper_sans"])[0]))
                else:
                    self._send(200, truename.status())
                return
            if path == "/api/proximity":
                import proximity
                action = qs.get("action", ["status"])[0]
                if action == "encounter":
                    self._send(200, proximity.encounter(
                        qs.get("center", ["reaper_sans"])[0]))
                else:
                    self._send(200, proximity.status())
                return
            if path == "/api/familytree":
                import familytree
                action = qs.get("action", ["status"])[0]
                if action == "summon":
                    self._send(200, familytree.summon(
                        qs.get("node", [""])[0]))
                elif action == "render":
                    self._send(200, {"tree": familytree.render()})
                else:
                    self._send(200, familytree.status())
                return
            if path == "/api/dna":
                import dna
                action = qs.get("action", ["status"])[0]
                if action == "fetch":
                    acc = qs.get("id", [""])[0]
                    if not acc:
                        self._send(400, {"error": "id required"})
                        return
                    self._send(200, dna.analyze(acc))
                elif action == "expand":
                    ids = [x for x in qs.get("ids", [""])[0]
                           .split(",") if x]
                    self._send(200, dna.expand_datasets(ids))
                elif action == "gguf":
                    self._send(200, dna.pack_gguf())
                else:
                    self._send(200, dna.status())
                return
            if path == "/api/charpack":
                import charpack
                action = qs.get("action", ["status"])[0]
                if action == "unlock":
                    ph = qs.get("phrase", [""])[0]
                    if not ph:
                        self._send(400, {"error": "phrase required"})
                        return
                    self._send(200, charpack.unlock_status(ph))
                elif action == "emoji":
                    self._send(200, charpack.generate_emoji(
                        qs.get("char", [""])[0]))
                elif action == "pack":
                    self._send(200, charpack.emoji_pack())
                elif action == "icons":
                    self._send(200, charpack.icon_pack())
                elif action == "probe":
                    self._send(200, charpack.probe())
                elif action == "icon":
                    ch = qs.get("char", [""])[0]
                    p = charpack.icon_path(ch)
                    if qs.get("emoji", ["0"])[0] in ("1", "true"):
                        ep = charpack.ICONS / f"{ch}.emoji.png"
                        if ep.exists():
                            p = ep
                    if not p or not p.exists():
                        self._send(404, {"error": "no icon"})
                        return
                    data = p.read_bytes()
                    ctype = mimetypes.guess_type(
                        str(p))[0] or "application/octet-stream"
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length",
                                     str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif action == "gguf":
                    self._send(200, charpack.pack_gguf())
                else:
                    self._send(200, charpack.status())
                return
            if path == "/api/gaster":
                self._send(200, gaster.status())
                return
            if path == "/api/prime":
                action = qs.get("action", ["status"])[0]
                if action == "wallets":
                    self._send(200, prime.wallets(force=True))
                else:
                    self._send(200, prime.status())
                return
            if path == "/api/tax":
                if qs.get("action", [""])[0] == "compliance":
                    self._send(200, tax.compliance(
                        node=qs.get("node", [""])[0] or None,
                        since=float(qs.get("since", ["0"])[0] or 0),
                        until=float(qs.get("until", ["0"])[0] or 0)))
                    return
                self._send(200, tax.statement(
                    node=qs.get("node", [""])[0] or None,
                    since=float(qs.get("since", ["0"])[0] or 0),
                    until=float(qs.get("until", ["0"])[0] or 0)))
                return
            if path == "/api/twitch":
                self._send(200, twitch.status())
                return
            if path == "/api/wishlist":
                # unresolved community descriptors — peers poll this and
                # resolve what they can (torrent client, local rips)
                want = [e for e in ingest.extra_catalogue()
                        if not e.get("fetchable")]
                self._send(200, {"wishlist": want})
                return
            if path == "/api/resolved":
                # what this hub has resolved for others
                done = load_json(DATA / "resolved.json", {})
                self._send(200, {"resolved": done})
                return
            if path == "/api/catalogue":
                extra = ingest.extra_catalogue()
                by_type = {}
                for e in extra:
                    by_type.setdefault(e.get("type", "unknown"), []).append(
                        {"title": e["title"], "uri": e["uri"],
                         "fetchable": e.get("fetchable", False)})
                self._send(200, {
                    "builtin": len(catalogue.CATALOGUE),
                    "ingested": len(extra), "by_type": by_type,
                    "entries": extra[:100]})
                return
            if path == "/api/peers":
                self._send(200, {"pool": peer_pool()})
                return
            if path == "/api/node":
                self._send(200, node_status())
                return
            if path == "/api/ip":
                self._send(200, {"your_ip": ip, "sid": self._sid,
                                 "server_ips": lan_ips()})
                return
            if path == "/api/engage":
                self._send(200, load_json(ENGAGE_PATH, {}))
                return
            if path == "/api/comms":
                node = qs.get("node", [self._sid])[0]
                msgs = load_json(COMMS_PATH, [])
                mine = [m for m in msgs if m["to"] in (node, "all", "*")
                        or m["from"] == node or m.get("sid") == self._sid]
                self._send(200, mine[-100:])
                return
            if path == "/api/timeline":
                self._send(200, {"events": timeline_events(
                    since=float(qs.get("since", ["0"])[0] or 0),
                    until=float(qs.get("until", ["0"])[0] or 0),
                    kind=qs.get("kind", [""])[0],
                    limit=int(qs.get("limit", ["500"])[0] or 500))})
                return
            if path == "/api/replay":
                at = float(qs.get("at", ["0"])[0] or 0)
                if not at:
                    self._send(400, {"error": "at=<unix ts> required"})
                    return
                self._send(200, replay_at(at))
                return
            if path == "/api/stream":
                rel = qs.get("path", [""])[0]
                target = (ROOT / rel).resolve()
                if not str(target).startswith(str(ROOT)) or not target.exists():
                    self._send(404, {"error": "not found"})
                    return
                st = target.stat()
                etag = f'"{int(st.st_mtime)}-{st.st_size}"'
                if self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.end_headers()
                    return
                ctype = mimetypes.guess_type(str(target))[0] or "video/mp4"
                data = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("ETag", etag)
                self.send_header("Cache-Control", "private, max-age=3600")
                self.end_headers()
                # economy: each stream hit is an impression earning
                try:
                    economy.record_play(rel)
                except Exception:
                    pass
                self.wfile.write(data)
                return
            if path.startswith("/web/") or path.startswith("/static/"):
                f = WEB / path.lstrip("/").split("/", 1)[-1]
                if f.exists() and f.is_file():
                    data = f.read_bytes()
                    ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self._send(404, {"error": "not found"})
                return
            self._send(404, {"error": "not found"})
        except Exception as e:
            try:
                self._send(500, {"error": str(e)})
            except Exception:
                pass

    def do_POST(self):
        global _requests
        _requests += 1
        self._session()
        ip = self._client_ip()
        _clients[ip] = time.time()
        u = urllib.parse.urlparse(self.path)
        self._track_key()
        try:
            body = self._json_body()
            if self._auth_required() and u.path not in (
                    "/api/auth/login", "/api/auth/signup",
                    "/api/auth/status"):
                if not self._key_meta():
                    self._send(401, {"error": "valid X-ATLAS-Key required",
                                     "hint": "POST /api/auth/signup"})
                    return
            if u.path == "/api/engage":
                with _lock:
                    eng = load_json(ENGAGE_PATH, {})
                    vid = eng.setdefault(body.get("video", ""), {
                        "likes": {}, "reposts": {}, "comments": []})
                    client = body.get("client") or self._sid
                    act = body.get("action")
                    if act == "like":
                        vid["likes"][client] = time.time()
                    elif act == "unlike":
                        vid["likes"].pop(client, None)
                    elif act == "repost":
                        vid["reposts"][client] = time.time()
                    elif act == "comment":
                        vid["comments"].append({
                            "client": client, "sid": self._sid,
                            "text": body.get("text", "")[:500],
                            "ts": time.time()})
                    save_json(ENGAGE_PATH, eng)
                    log_event("engage", {
                        "video": body.get("video", ""),
                        "action": act, "client": client,
                        "text": (body.get("text") or "")[:200]})
                    entry = eng.get(body.get("video", ""), {})
                    broadcast("engage", {"video": body.get("video", ""),
                                         "entry": entry})
                    self._send(200, entry)
            elif u.path == "/api/comms":
                with _lock:
                    msgs = load_json(COMMS_PATH, [])
                    msg = {
                        "id": int(time.time() * 1000),
                        "from": body.get("from") or self._sid,
                        "to": body.get("to", "all"),
                        "sid": self._sid,
                        "text": body.get("text", "")[:1000],
                        "ts": time.time()}
                    msgs.append(msg)
                    save_json(COMMS_PATH, msgs[-2000:])
                    log_event("comms", {
                        "id": msg["id"], "from": msg["from"],
                        "to": msg["to"],
                        "text": msg["text"][:200]})
                    broadcast("comms", msg)
                    self._send(200, {"ok": True, "id": msg["id"]})
            elif u.path == "/api/charpack":
                import charpack, base64
                if body.get("action") == "install_icon":
                    data = base64.b64decode(
                        body.get("data_b64", ""))
                    res = charpack.install_icon_bytes(
                        body.get("char", ""),
                        body.get("filename", "icon.png"), data)
                    log_event("charpack", {"char": res.get(
                        "character"), "installed": res.get(
                        "installed")})
                    self._send(200, res)
                else:
                    self._send(400, {"error": "action required"})
            elif u.path == "/api/peers":
                node = body.get("node", "anon")
                if body.get("hub", "").startswith("http"):
                    hubs = peer_hubs()
                    if body["hub"] not in hubs:
                        hubs.append(body["hub"])
                        save_json(PEER_HUBS_PATH, hubs[-20:])
                    self._send(200, {"ok": True, "hubs": hubs})
                    return
                urls = body.get("urls", [])
                added = []
                for e in urls[:50]:
                    u_url = e.get("url") if isinstance(e, dict) else e
                    if not u_url or not str(u_url).startswith("http"):
                        continue
                    register_peer_url(
                        u_url,
                        e.get("title", "") if isinstance(e, dict) else "",
                        e.get("tags", []) if isinstance(e, dict) else [],
                        f"peer:{node}")
                    added.append(u_url)
                broadcast("peers", {"node": node, "added": len(added)})
                self._send(200, {"ok": True, "added": added})
            elif u.path == "/api/vision":
                # reverse-image search: identify -> match -> optional fetch
                import base64
                img_b64 = body.get("image", "")
                img_bytes = base64.b64decode(img_b64) if img_b64 else b""
                if not img_bytes and body.get("path"):
                    fp = (ROOT / body["path"]).resolve()
                    if str(fp).startswith(str(ROOT)) and fp.exists():
                        img_bytes = fp.read_bytes()
                if not img_bytes:
                    self._send(400, {"error": "image required (b64 or path)"})
                    return
                items = feed()
                vs = vision.visual_search(img_bytes, items)
                # expand identified tags through the local model
                query = " ".join(vs["tags"][:6]) or "image"
                keywords = None
                exp = llm.expand_query(query, identity.load_profile())
                if exp:
                    keywords = exp.get("keywords", [query])
                text_hits = feed("", "", keywords) if keywords else []
                fetched = None
                if body.get("fetch") and keywords:
                    fetched = fetch_for_query(query, keywords)
                    broadcast("fetched", {"q": query, "fetched": fetched.get("fetched")})
                self._send(200, {
                    "vision": vs["vision"],
                    "tags": vs["tags"],
                    "visual_matches": vs["matches"],
                    "text_matches": text_hits[:12],
                    "expanded": keywords,
                    "fetched": fetched,
                    "processor": {"ollama_online": bool(llm.list_models()),
                                  "vision_model": vision.vision_model()},
                })
            elif u.path == "/api/fetch":
                url = body.get("url", "")
                if not url.startswith("http"):
                    self._send(400, {"error": "url required"})
                    return
                res = fetch_any_url(url, body.get("title"))
                broadcast("fetched", {"q": url, "fetched": res})
                self._send(200, {"fetched": res})
            elif u.path == "/api/uptime":
                urls = body.get("urls", [])[:50]
                self._send(200, {"uptimes": check_uptimes(urls)})
            elif u.path == "/api/auth/signup":
                self._send(200, api_keys.signup(body.get("username", ""),
                                                body.get("password", "")))
            elif u.path == "/api/auth/login":
                self._send(200, api_keys.login(body.get("username", ""),
                                               body.get("password", "")))
            elif u.path == "/api/auth/logout":
                self._send(200, api_keys.revoke(body.get("username", ""),
                                                body.get("password", "")))
            elif u.path == "/api/ingest":
                # accept one entry, a list, or a path to a dropped .json
                if body.get("path"):
                    fp = (ROOT / body["path"]).resolve()
                    if not str(fp).startswith(str(ROOT)) or not fp.exists():
                        self._send(400, {"error": "bad path"})
                        return
                    self._send(200, ingest.ingest_file(fp))
                elif body.get("entries"):
                    self._send(200, {"results": [
                        ingest.ingest(e) for e in body["entries"]
                        if isinstance(e, dict)]})
                else:
                    self._send(200, ingest.ingest(body))
            elif u.path == "/api/resolve":
                # a peer publishes a resolution for a community descriptor
                # (magnet/peer/hls) -> fetchable URL joins the shared pool
                url = (body.get("url") or "").strip()
                if not url.startswith("http"):
                    self._send(400, {"error": "http url required"})
                    return
                key = (body.get("key") or body.get("magnet")
                       or body.get("title") or url)[:200]
                rec = {"key": key, "url": url,
                       "title": body.get("title") or key,
                       "tags": body.get("tags", []),
                       "resolved_by": body.get("node") or self._sid,
                       "ts": time.time()}
                with _lock:
                    res = load_json(DATA / "resolved.json", {})
                    res[key] = rec
                    save_json(DATA / "resolved.json", res)
                ingest.ingest({"title": rec["title"], "url": url,
                               "tags": rec["tags"], "source": "community",
                               "node": rec["resolved_by"]})
                register_peer_url(url, rec["title"], rec["tags"],
                                  "community-resolved")
                broadcast("peers", {"resolved": rec})
                self._send(200, {"resolved": rec})
            elif u.path == "/api/manga/watch":
                # replicate a manga/manwha chapter into a video outlet
                mid = body.get("manga_id")
                if not mid and body.get("query"):
                    hits = manga.search_all(body["query"], limit=1)
                    mid = hits[0]["id"] if hits else None
                    if hits:
                        body.setdefault("source", hits[0].get("source"))
                if not mid:
                    self._send(400, {"error": "manga_id or query required"})
                    return
                inbox = ROOT / "videos" / "inbox"
                vid = manga.assemble_video(
                    mid, inbox, max_pages=int(body.get("pages", 40)),
                    source=body.get("source", "mangadex"),
                    chapter_index=int(body.get("chapter", -1)))
                if not vid:
                    self._send(200, {"error": "no pages assembled",
                                     "manga_id": mid})
                    return
                stored = restash_if_poor(identity.store_file(
                    str(vid), skip_score_gate=True))
                vid.unlink(missing_ok=True)
                broadcast("fetched", {"q": mid, "fetched": stored})
                self._send(200, {"manga_id": mid, "stored": stored})
            elif u.path == "/api/aniwatch/watch":
                # resolve an anime episode to a clip -> identity store
                aid = str(body.get("anilist_id") or "")
                if not aid and body.get("query"):
                    hits = aniwatch.search(body["query"], limit=1)
                    aid = hits[0]["id"] if hits else ""
                ep = int(body.get("episode", 1) or 1)
                secs = float(body.get("seconds", 60) or 60)
                if not aid:
                    self._send(400, {"error": "anilist_id or query required"})
                    return
                stream = aniwatch.resolve_stream(aid, ep,
                                                  body.get("mode", "sub"))
                if not stream:
                    self._send(200, {"error": "no playable source",
                                     "anilist_id": aid, "episode": ep})
                    return
                inbox = ROOT / "videos" / "inbox"
                inbox.mkdir(parents=True, exist_ok=True)
                out = inbox / f"ani_{aid}_e{ep}.mp4"
                vid = aniwatch.download_clip(
                    stream["m3u8"], out, seconds=secs,
                    referer=stream.get("referer"))
                if not vid:
                    self._send(200, {"error": "clip download failed",
                                     "m3u8": stream["m3u8"]})
                    return
                stored = restash_if_poor(identity.store_file(
                    str(vid), skip_score_gate=True))
                vid.unlink(missing_ok=True)
                broadcast("fetched", {"q": f"anime {aid} ep{ep}",
                                      "fetched": stored})
                self._send(200, {"anilist_id": aid, "episode": ep,
                                 "provider": stream.get("provider"),
                                 "stored": stored})
            elif u.path == "/api/social/token":
                plat = body.get("platform", "")
                fields = {k: v for k, v in body.items()
                          if k != "platform"}
                if body.get("public_base"):
                    social.set_public_base(body["public_base"])
                    fields.pop("public_base", None)
                self._send(200, social.set_token(plat, fields))
            elif u.path == "/api/social/fetch":
                # pull a public social post into the store pipeline
                url = body.get("url", "")
                plat = social.platform(url)
                if not plat:
                    self._send(400, {"error": "not a social URL",
                                     "url": url})
                    return
                target = url
                if plat == "threads":
                    direct = social.direct_media(url)
                    if direct:
                        target = direct
                res = fetch_any_url(target, title=body.get("title"))
                if res and "error" not in res:
                    res["platform"] = plat
                    broadcast("fetched", {"q": url, "fetched": res})
                self._send(200, res or {"error": "unreachable",
                                        "url": url, "platform": plat})
            elif u.path == "/api/social/post":
                plat = body.get("platform", "")
                # resolve src/path to a store file
                vid = None
                cand = body.get("path") or body.get("src") or ""
                for p in video_files():
                    if p.stem == cand or str(p) == cand \
                            or cand in str(p):
                        vid = p
                        break
                if not vid:
                    self._send(400, {"error": "no matching stored outlet",
                                     "src": cand})
                    return
                res = social.post(plat, vid,
                                  body.get("caption", ""))
                broadcast("social", {"platform": plat, "post": res})
                self._send(200, res)
            elif u.path == "/api/tunnel":
                action = body.get("action", "status")
                if action == "configure":
                    self._send(200, tunnel.cf_configure(body))
                elif action == "discover":
                    self._send(200, tunnel.cf_discover(
                        body.get("api_token")))
                elif action == "named":
                    # create named tunnel + DNS via Cloudflare API,
                    # then restart the connector on the stable host
                    res = tunnel.cf_create()
                    if res.get("ok") or res.get("tunnel_id"):
                        tunnel.stop()
                        self._send(200, {**res, **tunnel.start()})
                    else:
                        self._send(200, res)
                elif action == "login":
                    # one-click browser auth -> cert.pem; the cert
                    # watcher then auto-provisions the named tunnel
                    self._send(200, tunnel.login())
                elif action == "cert":
                    # provision named tunnel from cert.pem alone —
                    # no api_token needed
                    res = tunnel.cert_provision(body.get("hostname", ""))
                    if res.get("ok"):
                        tunnel.stop()
                        self._send(200, {**res, **tunnel.start()})
                    else:
                        self._send(200, res)
                elif action == "domainsearch":
                    self._send(200, tunnel.registrar_search(
                        body.get("q") or body.get("hostname", "")))
                elif action == "buy":
                    self._send(200, tunnel.registrar_register(
                        body.get("domain") or body.get("hostname", "")))
                elif action == "start":
                    self._send(200, tunnel.start())
                elif action == "stop":
                    self._send(200, tunnel.stop())
                elif action == "restart":
                    tunnel.stop()
                    self._send(200, tunnel.start())
                else:
                    self._send(200, tunnel.status())
            elif u.path == "/api/earn":
                action = body.get("action", "status")
                node = body.get("node", "") or None
                if action == "revenue":
                    # real revenue in: donation/sponsor/premium
                    self._send(200, economy.revenue(
                        float(body.get("amount", 0)),
                        body.get("kind", "donation"), node,
                        body.get("ref", ""),
                        body.get("currency", "USD")))
                elif action == "payout":
                    self._send(200, economy.payout(
                        node or "atlas",
                        float(body.get("amount", 0)),
                        body.get("method", "btc"),
                        body.get("address", "")))
                elif action == "settle":
                    self._send(200, economy.settle(
                        int(body.get("id", 0)), node or "atlas",
                        body.get("tx", "")))
                elif action == "refund":
                    # earnings return: pending payout back to balance
                    self._send(200, economy.refund(
                        int(body.get("id", 0)), node or "atlas"))
                elif action == "reconcile":
                    self._send(200, economy.reconcile())
                else:
                    self._send(200, economy.status(node))
            elif u.path == "/api/tax":
                action = body.get("action", "statement")
                node = body.get("node", "") or None
                since = float(body.get("since", 0) or 0)
                until = float(body.get("until", 0) or 0)
                if action == "export":
                    self._send(200, tax.export(
                        node, since, until,
                        body.get("fmt", "both")))
                elif action == "compliance":
                    self._send(200, tax.compliance(node, since, until))
                elif action == "file":
                    self._send(200, tax.file(node, since, until))
                elif action == "config":
                    cfg = {k: v for k, v in body.items()
                           if k != "action"}
                    DATA.joinpath("tax_config.json").write_text(
                        json.dumps(cfg, indent=1))
                    self._send(200, {"configured": sorted(cfg)})
                else:
                    self._send(200, tax.statement(node, since, until))
            elif u.path == "/api/prime":
                action = body.get("action", "status")
                if action == "generate":
                    self._send(200, prime.generate(
                        body.get("prompt", ""), **{
                            k: v for k, v in body.items()
                            if k not in ("action", "prompt")}))
                elif action == "query":
                    self._send(200, prime.query(
                        body.get("query") or body.get("text", "")))
                elif action == "wallets":
                    self._send(200, prime.wallets(force=True))
                else:
                    self._send(200, prime.status())
            elif u.path == "/api/browser":
                action = body.get("action", "render")
                if action == "status":
                    self._send(200, browser.status())
                elif action == "close":
                    browser.close()
                    self._send(200, {"closed": True})
                elif action == "auto":
                    task = body.get("task", "")
                    if not task:
                        self._send(400, {"error": "task required"})
                        return
                    prof = identity.load_profile()
                    model = llm.pick_model(prof) or "llama3.2"
                    fn = lambda p: llm._chat(model, p)  # noqa: E731
                    r = browser.model_automate(task, fn)
                    self._send(200, r)
                elif action == "guest_login":
                    url = body.get("url", "")
                    user = body.get("username", "")
                    pw_ = body.get("password", "")
                    if not (url and user):
                        self._send(400, {"error":
                                         "url+username required"})
                        return
                    self._send(200, browser.guest_login(url, user, pw_))
                elif action == "auto_login":
                    url, user = body.get("url", ""), body.get("username", "")
                    pw_ = body.get("password", "")
                    if not (url and user):
                        self._send(400, {"error":
                                         "url+username required"})
                        return
                    prof = identity.load_profile()
                    model = llm.pick_model(prof) or "llama3.2"
                    fn = lambda p: llm._chat(model, p)  # noqa: E731
                    self._send(200, browser.auto_login(
                        url, user, pw_, fn, body.get("site", "")))
                elif action == "forms":
                    url = body.get("url", "")
                    if not url:
                        self._send(400, {"error": "url required"})
                        return
                    self._send(200, browser.extract_forms(url))
                elif action == "sessions":
                    self._send(200, browser.sessions())
                else:
                    url = body.get("url", "")
                    if not url:
                        self._send(400, {"error": "url required"})
                        return
                    r = browser.render(url, wait_ms=int(
                        body.get("wait_ms", 4000)))
                    if not body.get("shot"):
                        r.pop("screenshot", None)
                    self._send(200, r)
            elif u.path == "/api/passkeys":
                action = body.get("action", "harvest")
                url = body.get("url", "")
                site = body.get("site", "")
                if action == "register":
                    self._send(200, passkeys.register(
                        url, body.get("via", ""), body.get("kind")))
                elif action == "list":
                    self._send(200, passkeys.list_all(site or None))
                elif action == "analyze":
                    # transcribe vid passkey audio, score keyphrase,
                    # stamp identity hash + wp-style category
                    target = url or site
                    if not target:
                        self._send(400, {"error":
                                         "url or site required"})
                        return
                    if site and not url:
                        pk = passkeys.recall(site, "vid") or \
                            passkeys.recall(site)
                        if not pk:
                            self._send(404, {"error":
                                             "no passkey for site"})
                            return
                        target = pk
                    self._send(200, passkeys.analyze(
                        target, body.get("keyphrase"),
                        int(body.get("seconds", 45))))
                elif action == "bind":
                    # pass passkey identity to bot path via cookie
                    self._send(200, passkeys.bind(url or site))
                elif action == "hash":
                    self._send(200, {"identity": passkeys.identity_hash(
                        url, body.get("kind", ""))})
                elif action == "flow":
                    # Q-time audio flow: pauses/continuity/flow_score,
                    # alpha signature, audio-correlation bindings
                    target = url or site
                    if not target:
                        self._send(400, {"error":
                                         "url or site required"})
                        return
                    if site and not url:
                        target = passkeys.recall(site, "vid") or \
                            passkeys.recall(site)
                        if not target:
                            self._send(404, {"error":
                                             "no passkey for site"})
                            return
                    self._send(200, passkeys.analyze_flow(
                        target, body.get("keyphrase"),
                        int(body.get("seconds", 60))))
                elif action == "correlate":
                    a, b = body.get("a", ""), body.get("b", "")
                    if not (a and b):
                        self._send(400, {"error": "a and b required"})
                        return
                    self._send(200, passkeys.correlate(
                        a, b, int(body.get("seconds", 60))))
                elif action == "viz":
                    # repeat visualiser: audio targets vs visual data
                    target = url or site
                    if not target:
                        self._send(400, {"error":
                                         "url or site required"})
                        return
                    if site and not url:
                        target = passkeys.recall(site, "vid") or \
                            passkeys.recall(site)
                        if not target:
                            self._send(404, {"error":
                                             "no passkey for site"})
                            return
                    self._send(200, flowviz.render(
                        target, int(body.get("seconds", 30)),
                        store=bool(body.get("store", True))))
                else:
                    if not url:
                        self._send(400, {"error": "url required"})
                        return
                    self._send(200, passkeys.harvest(
                        url, int(body.get("max_try", 15))))
            elif u.path == "/api/consensus":
                action = body.get("action", "status")
                if action == "vote":
                    self._send(200, nameconsensus.vote(
                        body.get("name", "")))
                elif action == "plate":
                    self._send(200, nameconsensus.add_plate(
                        body.get("plate", ""), body.get("alias", ""),
                        bool(body.get("combination", True)),
                        body.get("note", "")))
                elif action == "weights":
                    self._send(200, nameconsensus.update_weights())
                else:
                    self._send(200, nameconsensus.status())
            elif u.path == "/api/cast":
                action = body.get("action", "status")
                if action == "cast":
                    self._send(200, caster.cast(
                        body.get("name", ""), body.get("src", ""),
                        float(body.get("at", 0)),
                        body.get("lens", "character"),
                        int(body.get("repeats", 1)),
                        float(body.get("seconds", 8)),
                        body.get("instruction", "")))
                elif action == "mirror":
                    self._send(200, caster.mirror(body))
                elif action == "queue":
                    self._send(200, caster.run_queue(
                        body.get("triggers", []),
                        body.get("caps")))
                elif action == "gguf":
                    self._send(200, caster.store_gguf())
                elif action == "tiers":
                    self._send(200, caster.tier_list())
                else:
                    self._send(200, caster.status())
            elif u.path == "/api/gaster":
                action = body.get("action", "status")
                if action == "enter":
                    self._send(200, gaster.enter_door(
                        body.get("id", ""), sandbox=bool(
                            body.get("sandbox", True))))
                elif action == "repeater":
                    self._send(200, gaster.repeater(
                        body.get("trigger", ""),
                        int(body.get("cycles", 3))))
                elif action == "probe":
                    p = Path(body.get("win", ""))
                    if not p.is_absolute():
                        p = (ROOT / p).resolve()
                    self._send(200, gaster.game_probe(p)
                               if p.exists() else
                               {"error": "not found"})
                elif action == "strings":
                    win = Path(body.get("win", ""))
                    if not win.is_absolute():
                        win = (ROOT / win).resolve()
                    out = ROOT / "data" / "undertale" / "strings.txt"
                    self._send(200, gaster.extract_strings(win, out)
                               if win.exists() else
                               {"error": "not found"})
                elif action == "gguf":
                    self._send(200, gaster.store_gguf())
                else:
                    self._send(200, gaster.status())
            elif u.path == "/api/book":
                action = body.get("action", "status")
                if action == "open":
                    self._send(200, cardinal.open_book(
                        body.get("name", ""), body.get("token", ""),
                        body.get("identity", "")))
                elif action == "session":
                    self._send(200, cardinal.record_event(
                        body.get("kind", "note"),
                        body.get("detail", "")))
                elif action == "scrape":
                    url = body.get("url", "")
                    if not url:
                        self._send(400, {"error": "url required"})
                        return
                    self._send(200, cardinal.scrape_identities(
                        url, int(body.get("max_try", 10))))
                elif action == "engrave":
                    src = body.get("src") or body.get("url") or ""
                    instruction = body.get("instruction", "")
                    if not (src and instruction):
                        self._send(400, {"error":
                                         "src and instruction required"})
                        return
                    self._send(200, cardinal.engrave(
                        src, float(body.get("at", 0)), instruction,
                        float(body.get("seconds", 8)),
                        body.get("out", "")))
                else:
                    self._send(200, cardinal.status())
            elif u.path == "/api/twitch":
                action = body.get("action", "status")
                chan = body.get("channel") or body.get("url", "")
                if action == "ensure":
                    self._send(200, twitch.ensure(
                        chan, int(body.get("viewers", 3)),
                        body.get("title", "")))
                elif action == "release":
                    self._send(200, twitch.release(chan))
                else:
                    self._send(200, twitch.status())
            elif u.path == "/api/sites":
                action = body.get("action", "list")
                name = body.get("name", "")
                if action == "add":
                    self._send(200, sites.add(
                        name, body.get("url", ""),
                        body.get("username", ""),
                        body.get("password", ""),
                        body.get("kind", "")))
                elif action == "remove":
                    self._send(200, sites.remove(name))
                elif action == "login":
                    # drive the stored creds through the browser
                    # session; secrets never leave the server
                    rec = sites.get(name)
                    if not rec:
                        self._send(404, {"error": "unknown site"})
                        return
                    prof = identity.load_profile()
                    model = llm.pick_model(prof) or "llama3.2"
                    fn = lambda p: llm._chat(model, p)  # noqa: E731
                    res = browser.auto_login(
                        rec["url"], rec.get("username", ""),
                        rec.get("password", ""), fn, site=name)
                    sites.mark_login(name, not res.get("error"), res)
                    self._send(200, res)
                else:
                    self._send(200, sites.list_sites())
            elif u.path == "/api/discover":
                # LLM expands a topic into diverse queries, ytsearch each,
                # merge candidates — grows reach without dead-ending
                topic = (body.get("topic") or body.get("q") or "").strip()
                if not topic:
                    self._send(400, {"error": "topic required"})
                    return
                queries = llm.suggest_queries(
                    topic, n=int(body.get("n", 4)),
                    profile=identity.load_profile())
                seen, cands = set(), []
                for qu in queries:
                    for c in ytsearch(qu, 4):
                        if c["url"] not in seen:
                            seen.add(c["url"])
                            cands.append({**c, "via": qu})
                    if len(cands) >= 16:
                        break
                self._send(200, {"topic": topic, "queries": queries,
                                 "candidates": cands[:16]})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            try:
                self._send(500, {"error": str(e)})
            except Exception:
                pass


def auto_worker(interval: int = 30) -> None:
    """AUTO-01 'add 1' rule on auto: every interval ticks the GML
    counter, persists it, and broadcasts the packet over SSE so every
    connected client sees the automation live."""
    if gml_spec is None:
        return
    while True:
        try:
            state = load_json(AUTO_PATH, {"counter": 0})
            pkt = gml_spec.auto_packet(int(state.get("counter", 0)))
            AUTO_PATH.write_text(json.dumps(
                {"counter": pkt["counter"], "ts": time.time(),
                 "bindings": pkt["bindings"]}, indent=1))
            broadcast("auto", pkt)
        except Exception:
            pass
        time.sleep(interval)


PASSKEY_DONE = DATA / "passkey_done.json"


def passkey_worker(interval: int = 300) -> None:
    """Closes the passkey loop on auto: every interval picks the
    oldest unprocessed vid outlet in the store, runs Q-time flow
    analysis (transcript/keyphrase/pauses/bindings) and renders a
    repeat visualiser into the feed. One file per cycle — CPU-friendly."""
    while True:
        try:
            done = set(load_json(PASSKEY_DONE, []))
            vids = sorted(
                (ROOT / "videos" / "store").glob("*/*.mp4"),
                key=lambda p: p.stat().st_mtime)
            for v in vids:
                if v.stem.startswith("flowviz"):
                    continue
                h = hashlib.sha256(str(v).encode()).hexdigest()[:16]
                if h in done:
                    continue
                flowviz.render(str(v), seconds=20, store=True)
                passkeys.analyze_flow(str(v), seconds=30)
                done.add(h)
                PASSKEY_DONE.write_text(json.dumps(
                    sorted(done)[-500:]))
                broadcast("passkey", {"file": v.name,
                                      "identity": h})
                break  # one outlet per cycle
        except Exception:
            pass
        time.sleep(interval)


def economy_worker(interval: int = 600) -> None:
    """Periodic earnings reconciliation — pending payouts vs prime
    funding, persisted + broadcast so the network never loses track."""
    while True:
        try:
            snap = economy.reconcile()
            broadcast("economy", snap)
        except Exception:
            pass
        time.sleep(interval)


def main():
    port = 8917
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"ATLAS hub -> http://localhost:{port}")
    print(f"LAN reach  -> http://{lan_ips()[0]}:{port}")
    beacon_info = beacon.start(
        identity.load_profile().get("identity", "ATLAS"),
        on_peer=lambda msg: broadcast("peers", {"hub": msg.get("hub")}))
    print(f"Beacon     -> udp/{beacon_info['port']} "
          f"radio={beacon_info['wifi'].get('radio')} "
          f"freq={beacon_info['wifi'].get('frequency_mhz')}MHz")
    threading.Thread(target=stream_tuner, daemon=True).start()
    threading.Thread(target=community_sync, daemon=True).start()
    threading.Thread(target=torrent_worker, daemon=True).start()
    # public tunnel: exposes /api/stream for Meta/social posting +
    # off-LAN peers; feeds social.set_public_base automatically
    threading.Thread(target=tunnel.start, daemon=True).start()
    # AUTO-01 spec automation: 'add 1' rule ticking on interval
    threading.Thread(target=auto_worker, daemon=True).start()
    # passkey loop: auto flow-analysis + repeat visualiser per outlet
    threading.Thread(target=passkey_worker, daemon=True).start()
    # economy reconcile: earnings vs prime funding, every 10min
    threading.Thread(target=economy_worker, daemon=True).start()
    # twitch keepalive: viewer bots hold registered live channels
    twitch.autostart()
    # bot session: self-commenting node over the comms link
    try:
        bot_cfg = identity.load_profile().get("bot", {})
        if bot_cfg.get("enabled"):
            import bot
            threading.Thread(
                target=bot.bot_loop,
                args=(bot_cfg.get("name", "atlas-bot"),
                      int(bot_cfg.get("interval_s", 45)),
                      bool(bot_cfg.get("self_chat", True)),
                      bot_cfg.get("guest_logins")),
                daemon=True).start()
            print(f"Bot        -> {bot_cfg.get('name','atlas-bot')} "
                  f"every {bot_cfg.get('interval_s',45)}s")
    except Exception as e:
        print(f"Bot        -> disabled ({e})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
