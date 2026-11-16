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


PIPER = ROOT / ".venv-voice" / "bin" / "piper"
MODELS = ROOT / "data" / "voice_models"
# character -> piper voice model (neural TTS, not cloning)
VOICE_MAP = {"ryan": "en_US-ryan-high",
             "amy": "en_US-amy-medium",
             "sans": "en_US-ryan-high",
             "reaper_sans": "en_US-ryan-high",
             "gaster": "en_US-ryan-high",
             "papyrus": "en_US-ryan-high",
             "flowey": "en_US-ryan-high",
             "toriel": "en_US-amy-medium",
             "alphys": "en_US-amy-medium",
             "vanitas": "en_US-amy-medium",
             "muffet": "en_US-amy-medium",
             "undyne": "en_US-amy-medium",
             "asgore": "en_US-ryan-high",
             "senketsu": "en_US-ryan-high",
             "chara": "en_US-amy-medium",
             "frisk": "en_US-amy-medium"}


def _piper_model(voice: str) -> Path | None:
    """Resolve voice name/character to a piper onnx."""
    key = VOICE_MAP.get(voice.lower(), voice.lower())
    for cand in (key, "en_US-" + key,
                 "en_US-" + key + "-high",
                 "en_US-" + key + "-medium"):
        p = MODELS / (cand + ".onnx")
        if p.exists():
            return p
    return None


def speak(name: str, voice: str = "Samantha",
          rate: int = 175) -> dict:
    """Synthesize the speakable layer — piper neural TTS
    when a model resolves, macOS say fallback."""
    s = script_for(name)
    if s.get("error"):
        return s
    VOICE.mkdir(parents=True, exist_ok=True)
    slug = s["slug"] or re.sub(r"[^a-z0-9]+", "_",
                               name.lower())
    m4a = VOICE / (slug + ".m4a")
    script = s["script"] or name
    engine = "formant_say"
    model_path = _piper_model(voice)
    if PIPER.exists() and model_path:
        wav = VOICE / (slug + ".wav")
        txt = VOICE / (slug + ".txt")
        txt.write_text(script)
        subprocess.run(
            [str(PIPER), "-m", str(model_path),
             "-f", str(wav), "-i", str(txt)],
            check=True, timeout=300,
            cwd=str(ROOT))
        subprocess.run(["afconvert", "-f", "m4af",
                        "-d", "aac", str(wav), str(m4a)],
                       check=True, timeout=60)
        wav.unlink(missing_ok=True)
        txt.unlink(missing_ok=True)
        engine = "piper_neural"
    else:
        aiff = VOICE / (slug + ".aiff")
        subprocess.run(["say", "-v", voice, "-r",
                        str(rate), "-o", str(aiff),
                        script],
                       check=True, timeout=120)
        subprocess.run(["afconvert", "-f", "m4af",
                        "-d", "aac", str(aiff),
                        str(m4a)],
                       check=True, timeout=60)
        aiff.unlink(missing_ok=True)
    rec = {"kind": "voice_run",
           "device_time": _device_time(),
           "block": name, "slug": slug,
           "engine": engine,
           "ml_voice": False,
           "voice_model": model_path.name
           if model_path else None,
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


def experience(name: str) -> dict:
    """Let the model EXPERIENCE the synthesized voice —
    the occurrence's wording+sigils become sensory input.

    Hallucination is PERMITTED here but every derived
    perception is stamped hallucination:true + simulated:true
    — the model's 'experience' is recorded as simulation,
    never passed off as real perception. 'Real extreme'
    conditions = full sensory envelope (audio + visual
    manifest + script) under honest simulation flags.
    """
    s = script_for(name)
    if s.get("error"):
        return s
    rs = [r for r in runs() if r.get("slug") == s["slug"]]
    audio = rs[-1].get("audio") if rs else None
    cls = s["classification"]

    # 1) the voice joins a session as an experiencing entity
    instance = "voice_" + (s["slug"] or "block")[:24]
    try:
        import multiplayer as mp
        mp.join("arena", instance.split("_", 1)[-1],
                phrase="sea voice simulation")
    except Exception:
        pass

    # 2) ripple: the voice crosses into the session plane
    try:
        import ripples
        ripples.detect(instance, "sea_voice",
                       "session:arena", kind_hint="media",
                       evidence={"audio": audio,
                                 "words": cls["word_count"],
                                 "visual_refs":
                                 cls["visual_count"]})
    except Exception:
        pass

    # 3) hallucination-permitted perception records — the
    #    model 'experiences' wording + sigils; all derived
    perceptions = [
        {"sense": "audio", "content":
         "voice speaks %d words of the sea transcription"
         % cls["word_count"], "simulated": True,
         "hallucination": True},
        {"sense": "visual", "content":
         "%d sigil references manifest without sound: %s"
         % (cls["visual_count"],
            ", ".join(cls["visual_refs"][:8])),
         "simulated": True, "hallucination": True},
        {"sense": "event", "content":
         "occurrence repeats: wording aloud, sigils silent — "
         "the coligraph event simulates under real extreme "
         "conditions", "simulated": True,
         "hallucination": True}]

    rec = {"kind": "voice_experience",
           "device_time": _device_time(),
           "block": name, "instance": instance,
           "audio": audio, "engine": engine,
           "ml_voice": False,
           "voice_model": model_path.name
           if model_path else None,
           "conditions": "real_extreme_simulation",
           "hallucination_permitted": True,
           "perceptions": perceptions,
           "word_count": cls["word_count"],
           "visual_count": cls["visual_count"],
           "derived": True}
    VOICE.mkdir(parents=True, exist_ok=True)
    with (VOICE / "experiences.jsonl").open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    try:
        import eventbus
        eventbus.emit("voice_experience",
                      {"block": name,
                       "instance": instance})
    except Exception:
        pass
    return rec
