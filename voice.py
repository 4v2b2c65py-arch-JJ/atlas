"""Voice — speak the sea transcription; classify each
element visual (sigil/engraving/QR/glyph) vs word
(speakable text), then synthesize the word-layer through
the local TTS engine.

Honest engine note: macOS `say` is formant synthesis,
NOT ML voice replication. True voice cloning needs a
model (piper/XTTS) + reference sample — flagged in every
record as engine:"formant" until an ML voice is wired.

Store: data/sea/voice/<name>.m4a + voice_runs.jsonl
"""

import hashlib
import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
SEA = ROOT / "data" / "sea"
VOICE = SEA / "voice"
RUNS = VOICE / "voice_runs.jsonl"

VISUAL_TOKENS = {"sigil", "glyph", "engraving", "qr",
                 "acronym", "coligraph", "mu", "nu",
                 "null", "artifact", "pyramid",
                 "temple", "wraith", "star", "diamon",
                 "sumerian", "cuneiform"}
WORD_SKIP = {"the", "and", "or", "of", "is", "a", "an",
             "to", "in", "for", "on", "with", "not"}


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def classify(text: str) -> dict:
    """Split text into speakable words vs visual/sigil
    references — the visual/word determination."""
    words = re.findall(r"[A-Za-z']+", text.lower())
    visual, spoken = [], []
    for w in words:
        if w in VISUAL_TOKENS:
            visual.append(w)
        elif w not in WORD_SKIP and len(w) > 1:
            spoken.append(w)
    return {"visual_refs": sorted(set(visual)),
            "speakable": spoken,
            "word_count": len(spoken),
            "visual_count": len(set(visual))}


def script_for(name: str) -> dict:
    """Build the speakable script + visual manifest for a
    sea block."""
    p = SEA / (re.sub(r"[^a-z0-9]+", "_",
                      name.lower()).strip("_") + ".json")
    if not p.exists():
        return {"error": "not_found", "name": name}
    b = json.loads(p.read_text())
    raw = b.get("raw", "")
    cls = classify(raw)
    # speakable script: first N meaningful words in order
    script = " ".join(cls["speakable"][:120])
    return {"name": name, "slug": b.get("slug"),
            "classification": cls,
            "script": script,
            "visual_manifest": cls["visual_refs"],
            "engine_plan": "formant"}


def speak(name: str, voice: str = "Samantha",
          rate: int = 175) -> dict:
    """Synthesize the speakable layer to audio."""
    s = script_for(name)
    if s.get("error"):
        return s
    VOICE.mkdir(parents=True, exist_ok=True)
    slug = s["slug"] or re.sub(r"[^a-z0-9]+", "_",
                               name.lower())
    aiff = VOICE / (slug + ".aiff")
    m4a = VOICE / (slug + ".m4a")
    script = s["script"] or name
    subprocess.run(["say", "-v", voice, "-r", str(rate),
                    "-o", str(aiff), script],
                   check=True, timeout=120)
    subprocess.run(["afconvert", "-f", "m4af",
                    "-d", "aac", str(aiff), str(m4a)],
                   check=True, timeout=60)
    aiff.unlink(missing_ok=True)
    rec = {"kind": "voice_run",
           "device_time": _device_time(),
           "block": name, "slug": slug,
           "engine": "formant_say",
           "ml_voice": False,
           "voice": voice, "rate": rate,
           "audio": str(m4a.relative_to(ROOT)),
           "bytes": m4a.stat().st_size,
           "script_sha": hashlib.sha256(
               script.encode()).hexdigest()[:16],
           "word_count": s["classification"]
           ["word_count"],
           "visual_refs": s["visual_manifest"],
           "derived": True}
    with RUNS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    try:
        import eventbus
        eventbus.emit("voice_run", {"block": name,
                                    "words":
                                    rec["word_count"]})
    except Exception:
        pass
    return rec


def runs() -> list[dict]:
    out = []
    if RUNS.exists():
        for line in RUNS.open():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def status() -> dict:
    rs = runs()
    return {"kind": "voice_state",
            "device_time": _device_time(),
            "runs": len(rs),
            "engine": "formant_say",
            "ml_voice_available": False,
            "note": ("formant TTS now; ML voice replication "
                     "needs piper/XTTS model + reference "
                     "sample — wire when provided")}
