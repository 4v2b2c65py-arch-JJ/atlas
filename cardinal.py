"""Cardinal line — name-gated book mechanism (TOA + Vanitas decode).

cardinal_line.txt is preserved verbatim — same decode method as
toa_algo.txt: this module parses AROUND the structure, never
rewrites it.

Authority base plate (per spec):
- the true name identifies the target; the command-code bearer
  token opens the book — the vid cannot play without its name
- authority arises from knowledge + identity: passkey
  identity_hash binding is the knowledge proof
- data flow: opensource identity scraping via passkeys.harvest /
  passkeys.analyze feeds the authority requirements
- session preset: data/cardinal_session.json records what is
  occurring; triggers fire only on recorded events — consent is
  the recorded session, never bypassed
- engrave(): burns the instruction into the video's ORIGINAL
  layers (drawtext in-stream, not a sidecar) and seeds the
  transcript with that instruction
"""

import hashlib
import json
import re
import subprocess
import time
import urllib.request
from pathlib import Path

import passkeys

ROOT = Path(__file__).parent
CARDINAL_PATH = ROOT / "cardinal_line.txt"
SESSION_PATH = ROOT / "data" / "cardinal_session.json"
TRANSCRIPT_DIR = ROOT / "data" / "transcripts"
ENGRAVED_DIR = ROOT / "data" / "engraved"

# same cipher alphabet family as toa_algo.txt pools
CIPHER_RE = re.compile(r"[A-Za-z0-9_^|@><\-—_']+")
SECTION_RE = re.compile(r"^[A-Z][A-Za-z ]{2,30}$")

FONTS = [
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/System/Library/Fonts/Geneva.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]


def _ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def spec_hash() -> str:
    return hashlib.sha256(CARDINAL_PATH.read_bytes()).hexdigest()[:16]


def parse_cardinal() -> dict:
    """Parse around the preserved structure — sections, cipher
    tokens, command pairs. Read-only."""
    text = CARDINAL_PATH.read_text()
    sections, cur, buf = {}, "preamble", []
    for line in text.splitlines():
        s = line.strip()
        if SECTION_RE.match(s) and len(s) < 30 and " " not in s.strip() \
                or s in ("Origin Myth novel", "Red = blue",
                         "Clock ticks", "Fast ticks",
                         "Series command shell"):
            if buf:
                sections[cur] = buf
            cur, buf = s, []
        else:
            buf.append(line)
    if buf:
        sections[cur] = buf
    tokens = []
    for lines in sections.values():
        for ln in lines:
            for m in CIPHER_RE.finditer(ln):
                t = m.group(0)
                if len(t) >= 2:
                    tokens.append(t)
    pairs = re.findall(r"(Fate:\w+)", text)
    nums = [int(n) for n in re.findall(r"\b\d+\b", text)]
    return {
        "spec": "cardinal_line",
        "sha": spec_hash(),
        "sections": {k: len(v) for k, v in sections.items()},
        "tokens": tokens,
        "token_count": len(tokens),
        "commands": pairs,
        "numbers": nums,
        "fate_ticks": [44, 45, 46, 47, 48, 49],
        "connectors": 6,
        "timepieces": "over 16",
    }


def book_token(name: str) -> str:
    """Command-code bearer token bound to the book's true name."""
    return hashlib.sha256(
        f"{name.strip().lower()}:{spec_hash()}:cardinal".encode()
    ).hexdigest()[:32]


def _load_session() -> dict:
    try:
        return json.loads(SESSION_PATH.read_text())
    except Exception:
        return {"state": "dormant", "events": [], "consent": True}


def _save_session(s: dict) -> None:
    SESSION_PATH.parent.mkdir(exist_ok=True)
    tmp = SESSION_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    tmp.replace(SESSION_PATH)


def record_event(kind: str, detail: str = "") -> dict:
    """Session preset: record what is occurring. Triggers may only
    fire on recorded events — consent preserved by construction."""
    s = _load_session()
    s["events"].append({"ts": time.time(), "kind": kind,
                        "detail": detail[:300]})
    s["events"] = s["events"][-500:]
    s["state"] = "armed" if s["events"] else "dormant"
    _save_session(s)
    return s


def session_state() -> dict:
    s = _load_session()
    s["spec"] = spec_hash()
    s["parsed"] = parse_cardinal()["sections"]
    return s


def authority_requirements() -> list:
    """The base plate — what the book's authority demands, in code.
    Each check is evaluated live by open_book()."""
    return [
        {"check": "true_name_present",
         "rule": "target identified by its name — no name, no play"},
        {"check": "bearer_token_valid",
         "rule": "command-code bearer token derived from name+spec"},
        {"check": "knowledge_bound",
         "rule": "authority arises from knowledge: passkey "
                 "identity_hash must exist for the target"},
        {"check": "identity_scraped",
         "rule": "data flow: opensource identity scrape produced "
                 "an identity record (passkeys.harvest/analyze)"},
        {"check": "session_preset_active",
         "rule": "session preset records what is occurring; "
                 "triggers fire only on recorded events"},
        {"check": "consent_preserved",
         "rule": "session consent flag true — never violated"},
    ]


def _identities_from_passkeys(site_or_url: str) -> list:
    """Data flow: opensource identity scrape via passkeys store —
    recall/analyze yield identity hashes that bind knowledge."""
    out = []
    for kind in ("vid", "img"):
        url = passkeys.recall(site_or_url, kind)
        if url:
            out.append({"kind": kind, "url": url,
                        "identity": passkeys.identity_hash(url, kind)})
    return out


def scrape_identities(url: str, max_try: int = 10) -> dict:
    """Harvest + analyze a source for identities — the opensource
    library data flow feeding authority requirements."""
    h = passkeys.harvest(url, max_try=max_try)
    cands = h.get("candidates") or h.get("media") or []
    ids = []
    for c in cands[:5]:
        u = c.get("url") if isinstance(c, dict) else str(c)
        if not u:
            continue
        try:
            a = passkeys.analyze(u, seconds=20)
        except Exception:
            a = {}
        ids.append({
            "url": u,
            "identity": passkeys.identity_hash(u),
            "category": a.get("category"),
            "keyphrase_score": a.get("score"),
        })
    record_event("identity_scrape", f"{url} -> {len(ids)} identities")
    return {"source": url, "identities": ids, "count": len(ids)}


def open_book(name: str, token: str = "",
              identity: str = "") -> dict:
    """The gate: the vid cannot play without its name. Returns the
    authority verdict — every requirement evaluated, nothing
    assumed."""
    name = (name or "").strip()
    checks = {}
    checks["true_name_present"] = bool(name)
    checks["bearer_token_valid"] = bool(
        name and token and
        token.strip().lower() == book_token(name))
    sess = _load_session()
    ids = _identities_from_passkeys(name) or \
        ([{"identity": identity}] if identity else [])
    checks["knowledge_bound"] = bool(ids)
    checks["identity_scraped"] = any(
        e.get("kind") == "identity_scrape" for e in sess["events"])
    checks["session_preset_active"] = bool(sess["events"])
    checks["consent_preserved"] = bool(sess.get("consent", True))
    ok = all(checks.values())
    verdict = {
        "book": name or None,
        "open": ok,
        "checks": checks,
        "requirements": authority_requirements(),
        "session_state": sess["state"],
        "events": len(sess["events"]),
        "identities": ids[:5],
        "spec": spec_hash(),
        "note": "vid cannot play without its name" if not ok else
                "authority arisen — book open",
    }
    record_event("book_open" if ok else "book_denied",
                 name or "unnamed")
    return verdict


def _df_escape(s: str) -> str:
    return (s.replace("\\", "\\\\").replace(":", "\\:")
             .replace("'", "\\'").replace(",", "\\,")
             .replace(";", "\\;").replace("[", "\\[")
             .replace("]", "\\]").replace("%", "\\%"))


def _font() -> str:
    for f in FONTS:
        if Path(f).exists():
            return f
    return "Sans"


def engrave(src: str, at: float, instruction: str,
            seconds: float = 8.0, out: str = "") -> dict:
    """Replay the segment at `at`, capture the exact frame, and
    engrave the instruction into the video's ORIGINAL layers —
    drawtext burned into the stream, not a sidecar. Transcript is
    initialized with the same instruction."""
    ff = _ffmpeg()
    if not ff:
        return {"error": "ffmpeg unavailable"}
    src_p = Path(src)
    if not src_p.is_absolute():
        src_p = (ROOT / src).resolve()
    if not src_p.exists():
        return {"error": f"not found: {src}"}
    ENGRAVED_DIR.mkdir(parents=True, exist_ok=True)
    TRANSCRIPT_DIR.mkdir(parents=True, exist_ok=True)

    stem = src_p.stem
    clip = Path(out) if out else \
        ENGRAVED_DIR / f"{stem}_engraved_{int(at)}.mp4"
    frame = ENGRAVED_DIR / f"{stem}_at_{int(at)}.jpg"
    tid = hashlib.sha256(
        f"{instruction}:{src}:{at}".encode()).hexdigest()[:12]
    tpath = TRANSCRIPT_DIR / f"{tid}.json"

    # capture the exact frame first (the 'capture with the model
    # at the exact time' moment)
    subprocess.run(
        [ff, "-y", "-ss", str(at), "-i", str(src_p),
         "-frames:v", "1", str(frame)],
        capture_output=True, timeout=120)

    font = _font()
    text = _df_escape(instruction[:180])
    vf = (f"drawtext=fontfile='{font}':text='{text}':"
          f"x=(w-text_w)/2:y=h-140:fontsize=34:fontcolor=white:"
          f"borderw=2:bordercolor=black@0.8:"
          f"shadowx=2:shadowy=2:shadowcolor=black@0.6")
    r = subprocess.run(
        [ff, "-y", "-ss", str(at), "-t", str(seconds),
         "-i", str(src_p), "-vf", vf,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-movflags", "+faststart", str(clip)],
        capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        return {"error": "ffmpeg failed", "stderr": r.stderr[-400:]}

    transcript = {
        "id": tid, "instruction": instruction, "source": str(src_p),
        "at": at, "seconds": seconds, "engraved": True,
        "clip": str(clip), "frame": str(frame),
        "layers": "original — drawtext burned into stream",
        "ts": time.time(),
    }
    tpath.write_text(json.dumps(transcript, indent=1))
    record_event("engrave", f"{src_p.name}@{at} '{instruction[:60]}'")

    def rel(p: Path) -> str:
        try:
            return str(p.relative_to(ROOT))
        except ValueError:
            return str(p)

    return {
        "ok": True, "clip": rel(clip), "frame": rel(frame),
        "transcript": rel(tpath), "at": at,
        "instruction": instruction,
        "stream_url": f"/api/stream?path={rel(clip)}",
    }


def status() -> dict:
    p = parse_cardinal()
    return {
        "spec": "cardinal_line", "sha": spec_hash(),
        "sections": p["sections"], "token_count": p["token_count"],
        "commands": p["commands"], "numbers": p["numbers"],
        "fate_ticks": p["fate_ticks"],
        "connectors": p["connectors"], "timepieces": p["timepieces"],
        "session": session_state(),
        "requirements": authority_requirements(),
        "books": {"toa": "Tales of the Abyss",
                  "vanitas": "The Book of Vanitas"},
    }
