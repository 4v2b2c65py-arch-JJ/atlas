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


# ---- vid passkey analysis: audio transcription + keyphrase category --

FFMPEG = None


def _ffmpeg() -> str | None:
    global FFMPEG
    if FFMPEG is None:
        try:
            import imageio_ffmpeg
            FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            FFMPEG = ""
    return FFMPEG or None


def _content_length(url: str) -> int:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={
            "User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def identity_hash(url: str, kind: str = "") -> str:
    """Stable content identity for cache matching: canonical url +
    kind + size fingerprint."""
    import hashlib
    canon = url.split("?")[0].rstrip("/")
    raw = f"{canon}|{kind}|{_content_length(url)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def transcribe(url: str, seconds: int = 45) -> str | None:
    """Extract audio via ffmpeg -> SpeechRecognition (Google Web
    Speech). Returns transcript text or None."""
    ff = _ffmpeg()
    if not ff:
        return None
    import subprocess
    import tempfile
    import speech_recognition as sr
    with tempfile.NamedTemporaryFile(suffix=".wav",
                                     delete=False) as t:
        wav = t.name
    try:
        subprocess.run(
            [ff, "-y", "-t", str(seconds), "-i", url, "-vn",
             "-ar", "16000", "-ac", "1", wav],
            capture_output=True, timeout=seconds + 60)
        if not Path(wav).stat().st_size:
            return None
        rec = sr.Recognizer()
        with sr.AudioFile(wav) as src:
            audio = rec.record(src)
        try:
            return rec.recognize_google(audio)
        except sr.UnknownValueError:
            return ""      # audio present, no speech detected
        except sr.RequestError:
            try:
                return rec.recognize_sphinx(audio)
            except Exception:
                return ""
    except Exception:
        return None
    finally:
        try:
            Path(wav).unlink()
        except Exception:
            pass


def keyphrase_score(text: str, phrase: str) -> int:
    """WordPress-style keyphrase scoring: occurrences, density,
    and whether the phrase leads the transcript."""
    if not text or not phrase:
        return 0
    t, p = text.lower(), phrase.lower().strip()
    if not p:
        return 0
    words = t.split()
    hits = t.count(p)
    score = min(60, hits * 25)
    if words and p in words[0]:
        score += 20
    density = hits / max(1, len(words))
    score += min(20, int(density * 400))
    return min(100, score)


def _category(score: int, has_phrase: bool, has_audio: bool) -> str:
    if not has_audio:
        return "no-audio"
    if not has_phrase:
        return "ambient"
    if score >= 50:
        return "keyphrase-match"
    if score >= 20:
        return "keyphrase-partial"
    return "keyphrase-miss"


def analyze(url: str, keyphrase: str | None = None,
            seconds: int = 45) -> dict:
    """Transcribe a vid passkey's audio, score against keyphrase,
    stamp identity hash + category, persist. Works on any direct
    media URL — passkey or not."""
    rec = {"url": url, "kind": _kind(url), "via": "",
           "site": _site(url), "ts": time.time(), "verified": True}
    ident = identity_hash(url, rec["kind"])
    out = {"identity": ident, "kind": rec["kind"], "url": url}
    if rec["kind"] != "vid":
        out.update({"category": "img", "transcript": None,
                    "keyphrase_score": 0})
        return out
    text = transcribe(url, seconds=seconds)
    score = keyphrase_score(text or "", keyphrase or "")
    cat = _category(score, bool(keyphrase), text is not None)
    out.update({"category": cat, "transcript": (text or "")[:500],
                "keyphrase_score": score})
    # merge into store under the site so recall() sees it
    data = _load()
    site = rec["site"]
    existing = next((r for r in data.get(site, [])
                     if r.get("url") == url), None)
    if existing:
        existing.update({"identity": ident, "category": cat,
                         "transcript": out["transcript"],
                         "keyphrase_score": score})
    else:
        rec.update({"identity": ident, "category": cat,
                    "transcript": out["transcript"],
                    "keyphrase_score": score})
        data.setdefault(site, []).insert(0, rec)
        data[site] = data[site][:20]
    _save()
    _store[site] = data.get(site, [])
    return out


def find_by_hash(ident: str) -> dict | None:
    """Cache identity match — find a stored passkey by content hash."""
    for site, recs in _load().items():
        for rec in recs:
            if rec.get("identity") == ident:
                return {"site": site, **rec}
    return None


def bind(site_or_url: str) -> dict:
    """Pass passkey identity to the bot path as a cookie in the
    persistent browser context: atlas_pk=<json identity payload>."""
    import urllib.parse as up
    data = _load()
    raw = site_or_url if "://" in site_or_url \
        else "https://" + site_or_url
    site = _site(raw)
    recs = data.get(site) or []
    if not recs:
        for s, rs in data.items():
            if site in s or s in site:
                site, recs = s, rs
                break
    if not recs:
        return {"error": "no passkeys for site", "site": site}
    # prefer analysed vid passkeys, then any vid, then img
    rec = next((r for r in recs if r.get("kind") == "vid"
                and r.get("identity")), None) \
        or next((r for r in recs if r.get("kind") == "vid"), None) \
        or recs[0]
    payload = {"i": rec.get("identity") or identity_hash(
                   rec["url"], rec.get("kind", "")),
               "k": rec.get("kind", ""),
               "c": rec.get("category", ""),
               "s": rec.get("keyphrase_score", 0),
               "u": rec["url"]}
    value = up.quote(json.dumps(payload, separators=(",", ":")))
    import browser
    res = browser.set_cookie("atlas_pk", value,
                             "." + site.split(":")[0])
    return {"bound": site, "cookie": "atlas_pk",
            "identity": payload["i"], "category": payload["c"],
            "browser": res}


# ---- Q-time audio flow: quantized energy, pauses, continuity,
# ---- audio correlation binding, alphabetical-comparative scoring ----

import numpy as np

_SR = 16000


def _decode_mono(url: str, seconds: int) -> "np.ndarray | None":
    """ffmpeg -> 16kHz mono float array, capped at `seconds`."""
    ff = _ffmpeg()
    if not ff:
        return None
    import subprocess
    import tempfile
    import wave
    with tempfile.NamedTemporaryFile(suffix=".wav",
                                     delete=False) as t:
        wav = t.name
    try:
        subprocess.run(
            [ff, "-y", "-t", str(seconds), "-i", url, "-vn",
             "-ar", str(_SR), "-ac", "1", wav],
            capture_output=True, timeout=seconds + 90)
        with wave.open(wav, "rb") as w:
            raw = w.readframes(w.getnframes())
        if not raw:
            return None
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) \
            / 32768.0
    except Exception:
        return None
    finally:
        try:
            Path(wav).unlink()
        except Exception:
            pass


def audio_flow(url: str, seconds: int = 60,
               quantum: float = 0.5) -> dict:
    """Q-time flow analysis: energy quantized into `quantum`-second
    windows -> voiced/unvoiced -> speech segments, pauses, and a
    continuity score."""
    x = _decode_mono(url, seconds)
    if x is None or len(x) < _SR // 2:
        return {"error": "no decodable audio"}
    hop = int(_SR * quantum)
    n = len(x) // hop
    if n < 4:
        return {"error": "audio too short"}
    frames = x[:n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-9)
    # adaptive threshold: median + 0.5*MAD-ish spread
    med = float(np.median(rms))
    thr = max(med * 2.5, float(np.percentile(rms, 60)) * 1.15, 1e-4)
    voiced = rms > thr
    # contiguous voiced runs -> segments; gaps -> pauses
    segs, pauses = [], []
    i = 0
    while i < n:
        if voiced[i]:
            j = i
            while j < n and voiced[j]:
                j += 1
            if (j - i) * quantum >= 0.3:
                segs.append({"start": round(i * quantum, 2),
                             "end": round(j * quantum, 2)})
            i = j
        else:
            j = i
            while j < n and not voiced[j]:
                j += 1
            if (j - i) * quantum >= 0.4:
                pauses.append(round((j - i) * quantum, 2))
            i = j
    total = n * quantum
    voiced_t = sum(s["end"] - s["start"] for s in segs)
    pause_t = sum(pauses)
    continuity = round(voiced_t / max(total, 0.01), 3)
    pause_ratio = round(pause_t / max(total, 0.01), 3)
    # flow score rewards continuous speech, penalizes choppy pauses
    flow_score = round(max(0.0, continuity * (1.0 - 0.6 * pause_ratio)
                           - 0.1 * len(pauses) / max(len(segs), 1)), 3)
    return {"quantum": quantum, "windows": n, "threshold": round(thr, 5),
            "segments": segs, "pauses": pauses,
            "voiced_ratio": continuity, "pause_ratio": pause_ratio,
            "flow_score": flow_score,
            "envelope": [round(float(v), 4) for v in
                         (rms / (rms.max() + 1e-9))[:240]]}


def _envelope(url: str, seconds: int = 60) -> "np.ndarray | None":
    """1 value/sec normalized RMS envelope for correlation."""
    x = _decode_mono(url, seconds)
    if x is None:
        return None
    hop = _SR
    n = len(x) // hop
    if n < 4:
        return None
    frames = x[:n * hop].reshape(n, hop)
    env = np.sqrt((frames ** 2).mean(axis=1) + 1e-9)
    return env / (env.max() + 1e-9)


def correlate(url_a: str, url_b: str, seconds: int = 60) -> dict:
    """Audio binding: Pearson correlation of the two energy
    envelopes — how strongly two passkeys' audio flows match."""
    a, b = _envelope(url_a, seconds), _envelope(url_b, seconds)
    if a is None or b is None:
        return {"error": "unusable audio"}
    n = min(len(a), len(b))
    if n < 4:
        return {"error": "too short"}
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    denom = float(np.sqrt((a ** 2).sum() * (b ** 2).sum()))
    score = float((a * b).sum() / denom) if denom else 0.0
    return {"binding": round(score, 4), "windows": n,
            "match": "strong" if score > 0.6 else
                     "partial" if score > 0.3 else "weak"}


def flow_signature(text: str) -> str:
    """Alphabetical-comparative signature: word-initial sequence —
    two flows compare by how their initials align."""
    words = re.findall(r"[a-z]+", (text or "").lower())
    return "".join(w[0] for w in words[:400])


def signature_score(sig_a: str, sig_b: str) -> float:
    """Comparative score between two alpha signatures via
    SequenceMatcher on the initial-letter streams."""
    import difflib
    if not sig_a or not sig_b:
        return 0.0
    return round(difflib.SequenceMatcher(None, sig_a, sig_b).ratio(), 4)


def analyze_flow(url: str, keyphrase: str | None = None,
                 seconds: int = 60) -> dict:
    """Full Q-time flow analysis for a vid passkey: envelope flow,
    pauses/continuity, transcript, alpha signature, and audio
    binding vs other stored vid passkeys on the same site."""
    flow = audio_flow(url, seconds=seconds)
    text = transcribe(url, seconds=min(seconds, 45)) \
        if _kind(url) == "vid" else None
    sig = flow_signature(text or "")
    out = {"url": url, "identity": identity_hash(url, _kind(url)),
           "flow": flow, "signature": sig,
           "transcript": (text or "")[:500] or None,
           "keyphrase_score": keyphrase_score(text or "",
                                              keyphrase or "")}
    # persist flow fields onto the stored record
    data = _load()
    site = _site(url) or Path(url).stem  # local files key by stem
    for rec in data.get(site, []):
        if rec.get("url") == url:
            rec.update({"flow_score": flow.get("flow_score"),
                        "continuity": flow.get("voiced_ratio"),
                        "pauses": flow.get("pauses"),
                        "signature": sig,
                        "transcript": out["transcript"],
                        "keyphrase_score": out["keyphrase_score"]})
            break
    else:
        data.setdefault(site, []).insert(0, {
            "kind": _kind(url), "url": url, "via": "", "site": site,
            "ts": time.time(), "verified": True,
            "identity": out["identity"], "category": "flow",
            "flow_score": flow.get("flow_score"),
            "continuity": flow.get("voiced_ratio"),
            "pauses": flow.get("pauses"), "signature": sig,
            "transcript": out["transcript"],
            "keyphrase_score": out["keyphrase_score"]})
        data[site] = data[site][:20]
    _save()
    _store[site] = data.get(site, [])
    # audio-correlation binding against sibling vid passkeys
    bindings = []
    for rec in data.get(site, []):
        u = rec.get("url")
        if not u or u == url or rec.get("kind") != "vid":
            continue
        c = correlate(url, u, seconds=min(seconds, 45))
        if "binding" in c:
            bindings.append({"url": u, "binding": c["binding"],
                             "match": c["match"]})
            # alpha-comparative between the two transcripts
            other_sig = rec.get("signature") or flow_signature(
                rec.get("transcript") or "")
            bindings[-1]["alpha_score"] = signature_score(
                sig, other_sig)
    out["bindings"] = sorted(bindings,
                             key=lambda b: -b["binding"])[:8]
    return out
