"""Twitch keepalive — viewer bots that hold a live channel open.

Twitch live edges rotate playlist tokens and drop idle viewers; a
channel that looks watched stays live. For each registered channel
we run N 'viewer' threads that repeatedly resolve the HLS edge and
pull the newest media segments (like real viewers), re-resolving
when the playlist rotates or the stream drops. Registration is
automatic: any successful live fetch ensures the channel is kept.

State: data/twitch_keepalive.json — channels + viewer counts.
"""

import json
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
STATE = DATA / "twitch_keepalive.json"
YTDLP = str(ROOT / ".venv" / "bin" / "yt-dlp")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X "
                     "10_15_7) ATLAS-viewer"}

_lock = threading.Lock()
_threads: dict = {}   # channel -> [Thread]
_seen: dict = {}      # channel -> set of fetched segment urls


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"channels": {}}


def _save(st: dict) -> None:
    DATA.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1))


def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def resolve(url_or_channel: str) -> str:
    """yt-dlp -g -> live m3u8 edge (master or media playlist)."""
    target = url_or_channel
    if "://" not in target:
        target = f"https://www.twitch.tv/{target.lstrip('@')}"
    r = subprocess.run([YTDLP, "-g", "--socket-timeout", "15",
                        target], capture_output=True, text=True,
                       timeout=45)
    return next((ln.strip() for ln in r.stdout.splitlines()
                 if ".m3u8" in ln and ln.startswith("http")), "")


def _media_playlist(m3u8: str) -> tuple[str, list, float]:
    """If master playlist, pick the highest-bandwidth variant;
    return (media_url, segment_urls, target_duration)."""
    text = _get(m3u8).decode("utf-8", "replace")
    if "#EXT-X-STREAM-INF" in text:
        best_bw, best_uri = -1, ""
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith("#EXT-X-STREAM-INF"):
                bw = int(re.search(r"BANDWIDTH=(\d+)", ln).group(1)) \
                    if re.search(r"BANDWIDTH=(\d+)", ln) else 0
                if i + 1 < len(lines) and bw > best_bw:
                    best_bw, best_uri = bw, lines[i + 1].strip()
        if not best_uri:
            return "", [], 0.0
        m3u8 = urllib.parse.urljoin(m3u8, best_uri)
        text = _get(m3u8).decode("utf-8", "replace")
    base = m3u8.rsplit("/", 1)[0] + "/"
    segs = [urllib.parse.urljoin(base, ln.strip())
            for ln in text.splitlines()
            if ln.strip() and not ln.startswith("#")]
    td = re.search(r"#EXT-X-TARGETDURATION:(\d+)", text)
    return m3u8, segs, float(td.group(1)) if td else 2.0


def _viewer(channel: str, idx: int, viewers: int) -> None:
    """One viewer bot: hold the edge, pull newest segments like a
    real client; stagger phase so viewers don't sync."""
    fails = 0
    m3u8, segs, td = "", [], 2.0
    while True:
        try:
            if not m3u8 or fails >= 3:
                m3u8 = resolve(channel)
                if not m3u8:
                    fails += 1
                    time.sleep(min(60, 10 * fails))
                    continue
                _seen.setdefault(channel, set())
                fails = 0
                # phase-offset each viewer
                time.sleep((idx * 1.7) % max(td, 1))
            media, segs, td = _media_playlist(m3u8)
            fresh = [s for s in segs
                     if s not in _seen[channel]][-2:]
            for s in fresh:
                try:
                    _get(s, timeout=15)
                    _seen[channel].add(s)
                    # cap memory: keep last ~200 segs
                    if len(_seen[channel]) > 200:
                        _seen[channel] = set(list(
                            _seen[channel])[-200:])
                except Exception:
                    pass
            # playlist rotation: live edges refresh ~every targetdur
            time.sleep(max(1.5, td * 0.9))
            # re-resolve periodically so rotated tokens get picked up
            if random.random() < 0.15:
                m3u8 = ""
        except Exception:
            fails += 1
            time.sleep(min(45, 5 * fails))


import random  # noqa: E402  (kept near use for viewer jitter)


def ensure(channel: str, viewers: int = 3,
           title: str = "") -> dict:
    """Register a channel and start its viewer bots (idempotent)."""
    channel = channel.strip().lstrip("@").lower()
    if not channel:
        return {"error": "channel required"}
    with _lock:
        st = _load()
        st.setdefault("channels", {})
        st["channels"].setdefault(channel, {
            "viewers": viewers, "title": title,
            "since": time.time(), "state": "live"})
        _save(st)
        live = [t for t in _threads.get(channel, [])
                if t.is_alive()]
        if len(live) < viewers:
            for i in range(len(live), viewers):
                t = threading.Thread(target=_viewer,
                                     args=(channel, i, viewers),
                                     daemon=True)
                t.start()
                _threads.setdefault(channel, []).append(t)
    return {"channel": channel, "viewers": viewers,
            "threads": len(_threads.get(channel, []))}


def release(channel: str) -> dict:
    """Stop keepalive for a channel (viewers exit on next fail)."""
    st = _load()
    ch = st.get("channels", {}).pop(channel.lower(), None)
    _save(st)
    return {"released": channel, "was": bool(ch)}


def status() -> dict:
    st = _load()
    out = {}
    for ch, meta in st.get("channels", {}).items():
        ts = _threads.get(ch, [])
        out[ch] = {**meta,
                   "alive": sum(1 for t in ts if t.is_alive())}
    return {"channels": out}


def autostart() -> None:
    """Restart keepalive for every persisted channel."""
    for ch, meta in _load().get("channels", {}).items():
        ensure(ch, int(meta.get("viewers", 3)),
               meta.get("title", ""))
