"""Social platform connectors — TikTok, Instagram, Facebook, Threads.

Two directions:
  fetch  — pull public posts into ATLAS (yt-dlp for tiktok/ig/fb;
           og-tag scrape fallback for threads)
  post   — publish a stored outlet back out (Meta Graph API for
           threads/instagram/facebook; TikTok Content Posting API).

Posting requires per-platform credentials in data/social_tokens.json:
  threads:    {access_token, user_id}
  instagram:  {access_token, ig_user_id}   (professional account)
  facebook:   {access_token, page_id}
  tiktok:     {access_token}              (approved Content Posting app)
Without tokens, post() returns a clear "not configured" error — nothing
is faked.
"""

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
TOKENS_PATH = ROOT / "data" / "social_tokens.json"
UA = {"User-Agent": "ATLAS-social/1.0"}

PLATFORMS = {
    "tiktok": {
        "hosts": ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com"),
        "fetch": "ytdlp", "post": "tiktok",
        "needs": ["access_token"],
    },
    "instagram": {
        "hosts": ("instagram.com", "instagr.am"),
        "fetch": "ytdlp", "post": "meta_graph",
        "needs": ["access_token", "ig_user_id"],
    },
    "facebook": {
        "hosts": ("facebook.com", "fb.watch", "m.facebook.com"),
        "fetch": "ytdlp", "post": "meta_graph",
        "needs": ["access_token", "page_id"],
    },
    "threads": {
        "hosts": ("threads.net", "threads.com"),
        "fetch": "scrape", "post": "meta_graph",
        "needs": ["access_token", "user_id"],
    },
}


def platform(url: str) -> str | None:
    u = (url or "").lower()
    for name, cfg in PLATFORMS.items():
        if any(h in u for h in cfg["hosts"]):
            return name
    return None


def direct_media(url: str) -> str | None:
    """Best-effort direct media URL for platforms yt-dlp can't handle
    (Threads). Returns None when nothing playable is found."""
    if platform(url) != "threads":
        return None
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": UA["User-Agent"]})
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:
        return None
    for pat in (r'property="og:video(?::secure_url)?"\s+content="([^"]+)"',
                r'"video_url"\s*:\s*"([^"]+)"',
                r'"video_versions"\s*:\s*\[\s*\{[^}]*"url"\s*:\s*"([^"]+)"'):
        m = re.search(pat, html)
        if m:
            return m.group(1).replace("\\u0026", "&").replace("&amp;", "&")
    return None


def _load() -> dict:
    try:
        return json.loads(TOKENS_PATH.read_text())
    except Exception:
        return {}


def _save(data: dict) -> None:
    TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_PATH.write_text(json.dumps(data, indent=1))


def set_token(platform_name: str, fields: dict) -> dict:
    if platform_name not in PLATFORMS:
        return {"error": f"unknown platform {platform_name}"}
    data = _load()
    data[platform_name] = {**data.get(platform_name, {}), **fields,
                           "ts": time.time()}
    _save(data)
    return status()


def status() -> dict:
    data = _load()
    out = {}
    for name, cfg in PLATFORMS.items():
        tok = data.get(name, {})
        configured = all(tok.get(k) for k in cfg["needs"])
        out[name] = {"configured": configured,
                     "fetch": cfg["fetch"], "post": cfg["post"],
                     "needs": cfg["needs"]}
    return {"platforms": out}


def _post_json(url: str, payload: dict, token: str) -> dict:
    body = urllib.parse.urlencode(
        {**payload, "access_token": token}).encode()
    req = urllib.request.Request(url, data=body, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"http {e.code}",
                "detail": e.read().decode("utf-8", "replace")[:300]}


def _post_multipart(url: str, fields: dict, file_field: str,
                    file_path: Path, token: str) -> dict:
    boundary = "----atlas" + str(int(time.time() * 1000))
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="{file_field}"; filename="{file_path.name}"\r\n'
                 f"Content-Type: video/mp4\r\n\r\n".encode()
                 + file_path.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        url, data=body,
        headers={**UA, "Content-Type":
                 f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"http {e.code}",
                "detail": e.read().decode("utf-8", "replace")[:300]}


def _post_threads(tok: dict, video: Path, caption: str) -> dict:
    token, uid = tok["access_token"], tok["user_id"]
    base = "https://graph.threads.net/v1.0"
    r = _post_json(f"{base}/{uid}/threads", {
        "media_type": "VIDEO", "video_url": _public_url(video),
        "text": caption[:500]}, token)
    cid = r.get("id")
    if not cid:
        return r
    return _post_json(f"{base}/{uid}/threads_publish",
                      {"creation_id": cid}, token)


def _post_instagram(tok: dict, video: Path, caption: str) -> dict:
    token, uid = tok["access_token"], tok["ig_user_id"]
    base = "https://graph.facebook.com/v21.0"
    r = _post_json(f"{base}/{uid}/media", {
        "media_type": "REELS", "video_url": _public_url(video),
        "caption": caption[:2200]}, token)
    cid = r.get("id")
    if not cid:
        return r
    return _post_json(f"{base}/{uid}/media_publish",
                      {"creation_id": cid}, token)


def _post_facebook(tok: dict, video: Path, caption: str) -> dict:
    token, pid = tok["access_token"], tok["page_id"]
    return _post_multipart(
        f"https://graph.facebook.com/v21.0/{pid}/videos",
        {"description": caption[:63206],
         "access_token": token}, "source", video, token)


def _post_tiktok(tok: dict, video: Path, caption: str) -> dict:
    token = tok["access_token"]
    size = video.stat().st_size
    init = _post_json(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        {"post_info": {"title": caption[:2200],
                       "privacy_level": "SELF_ONLY"},
         "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                         "chunk_size": size, "total_chunk_count": 1}},
        token)
    if init.get("error", {}).get("code") not in (None, "ok") \
            and "data" not in init:
        return init
    upload_url = ((init.get("data") or {}).get("upload_url"))
    if not upload_url:
        return init
    req = urllib.request.Request(
        upload_url, data=video.read_bytes(), method="PUT",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "video/mp4",
                 "Content-Length": str(size)})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return {"published": True, "http": r.status,
                    "init": init.get("data")}
    except urllib.error.HTTPError as e:
        return {"error": f"upload http {e.code}",
                "detail": e.read().decode("utf-8", "replace")[:300]}


_POSTERS = {"threads": _post_threads, "instagram": _post_instagram,
            "facebook": _post_facebook, "tiktok": _post_tiktok}

_PUBLIC_BASE = ""


def set_public_base(url: str) -> None:
    """Meta/Threads need a public video_url — point this at the hub's
    reachable base (e.g. http://192.168.100.9:8917)."""
    global _PUBLIC_BASE
    _PUBLIC_BASE = url.rstrip("/")


def _public_url(video: Path) -> str:
    if _PUBLIC_BASE:
        return (f"{_PUBLIC_BASE}/api/stream?path="
                f"{urllib.parse.quote(str(video.relative_to(ROOT)))}")
    return f"http://localhost:8917/api/stream?path=" \
           f"{urllib.parse.quote(str(video.relative_to(ROOT)))}"


def post(platform_name: str, video: Path, caption: str = "") -> dict:
    cfg = PLATFORMS.get(platform_name)
    if not cfg:
        return {"error": f"unknown platform {platform_name}"}
    tok = _load().get(platform_name)
    if not tok or not all(tok.get(k) for k in cfg["needs"]):
        return {"error": "not configured",
                "platform": platform_name,
                "needs": cfg["needs"],
                "hint": "POST /api/social/token with credentials"}
    if not video.exists():
        return {"error": f"no such file {video}"}
    kind = cfg["post"]
    if kind == "meta_graph":
        return _POSTERS[platform_name](tok, video, caption)
    return _POSTERS[platform_name](tok, video, caption)
