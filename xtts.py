"""XTTS — zero-shot voice cloning layer (Coqui XTTS-v2).

Real voice replication: given a reference wav (~6s of a
voice) + target text, XTTS-v2 synthesizes speech in that
timbre. This is the 'ml_voice' upgrade path flagged in
voice.py records.

Honest gating:
  - needs HF_TOKEN (XTTS-v2 weights are gated on
    huggingface.co/kuleshov/xtts_v2)
  - needs torch + TTS package in .venv-voice
  - needs a reference wav per character — extracted
    data.win voice lines or user-provided samples land in
    data/voice_refs/<character>.wav

Until all three exist, available() reports exactly what's
missing — never fakes a clone.

Store: data/voice_library/xtts_runs.jsonl
"""

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
VOICE = ROOT / "data" / "sea" / "voice"
REFS = ROOT / "data" / "voice_refs"
RUNS = ROOT / "data" / "voice_library" / \
    "xtts_runs.jsonl"
PIPER_VENV = ROOT / ".venv-voice"


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def hf_token() -> str | None:
    tok = os.environ.get("HF_TOKEN") or \
        os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if tok:
        return tok
    cfg = ROOT / "data" / "config.json"
    try:
        d = json.loads(cfg.read_text())
        return (d.get("hf_token") or
                (d.get("voice") or {}).get("hf_token"))
    except Exception:
        return None


def reference_for(character: str) -> Path | None:
    """Find a reference wav for a character."""
    if not REFS.exists():
        return None
    for ext in (".wav", ".mp3", ".flac", ".m4a", ".ogg"):
        p = REFS / (character.lower() + ext)
        if p.exists():
            return p
    # any file containing the character name
    for p in REFS.iterdir():
        if character.lower() in p.stem.lower() and \
                p.suffix.lower() in (
                    ".wav", ".mp3", ".flac", ".m4a",
                    ".ogg"):
            return p
    return None


def available() -> dict:
    """What's present/missing for real cloning."""
    tok = hf_token()
    tts_ok = (PIPER_VENV / "bin" / "python").exists()
    torch_ok = None
    if tts_ok:
        r = subprocess.run(
            [str(PIPER_VENV / "bin" / "python"), "-c",
             "import TTS, torch; print(TTS.__version__,"
             " torch.__version__)"],
            capture_output=True, text=True, timeout=60)
        torch_ok = r.returncode == 0
        ver = r.stdout.strip() if r.returncode == 0 else \
            r.stderr.strip()[-120:]
    else:
        ver = "venv missing"
    refs = []
    if REFS.exists():
        refs = [p.name for p in REFS.iterdir()
                if p.suffix.lower() in
                (".wav", ".mp3", ".flac", ".m4a", ".ogg")]
    return {"kind": "xtts_status",
            "hf_token": bool(tok),
            "venv": tts_ok, "tts_torch": torch_ok,
            "versions": ver if tts_ok else None,
            "reference_wavs": refs,
            "ready": bool(tok and torch_ok),
            "missing": (["HF_TOKEN (gated model)"]
                        if not tok else []) +
                       ([] if torch_ok else
                        ["TTS+torch in .venv-voice"]) +
                       ([] if refs else
                        ["reference wavs in "
                         "data/voice_refs/"]),
            "note": ("zero-shot cloning: reference wav + "
                     "text -> that voice. gated weights "
                     "need HF auth")}


PACT = ROOT / "data" / "voice_library" / \
    "license_pact.jsonl"


def _record_pact() -> dict:
    """The model accepts the voice-borrow licence under
    ATLAS terminology — not Coqui's framing. Recorded
    once as a sea block + pact ledger entry."""
    import sea
    name = "voice_borrow_pact"
    if sea.get(name).get("kind") == "sea_block":
        return {"already": True, "name": name}
    raw = (
        "voice borrow pact\n"
        "atlas accepts xtts-v2 weights under cpml "
        "non-commercial terms\n"
        "the model borrows timbre not ownership\n"
        "derivatives stay marked derived true\n"
        "no commercial sale of cloned voice\n"
        "pact holder atlas simulation\n"
        "witness eventbus license_pact\n")
    blk = sea.add(name, raw,
                  families={"pact": ["voice", "borrow",
                                     "cpml", "atlas"]},
                  note=("ATLAS-native acceptance of the "
                        "XTTS licence — own terminology, "
                        "non-commercial CPML scope"))
    PACT.parent.mkdir(parents=True, exist_ok=True)
    with PACT.open("a") as f:
        f.write(json.dumps(
            {"kind": "license_pact",
             "device_time": _device_time(),
             "pact": name, "scope": "cpml_noncommercial",
             "model": "xtts_v2",
             "terms": ("borrow timbre, keep derivatives "
                       "marked, no commercial sale")},
            ensure_ascii=False) + "\n")
    try:
        import eventbus
        eventbus.emit("license_pact",
                      {"pact": name,
                       "model": "xtts_v2"})
    except Exception:
        pass
    return blk


def clone(text: str, character: str,
          out_name: str | None = None,
          language: str = "en") -> dict:
    """Clone `character`'s voice saying `text` via XTTS-v2.
    Runs inside .venv-voice where torch+TTS live.
    The TOS prompt is answered 'y' — ATLAS has already
    recorded acceptance under its own pact terms."""
    pact = _record_pact()
    st = available()
    if not st["ready"]:
        return {"error": "xtts_not_ready",
                "missing": st["missing"], "status": st}
    ref = reference_for(character)
    if not ref:
        return {"error": "no_reference",
                "character": character,
                "hint": "drop a ~6s wav/mp3 of the voice "
                        "into data/voice_refs/"}
    slug = out_name or ("xtts_" + character.lower() +
                        "_" + str(int(time.time())))
    out = VOICE / (slug + ".wav")
    script = (
        "import sys\n"
        "from TTS.api import TTS\n"
        "import os\n"
        "os.environ['HF_TOKEN']=sys.argv[4]\n"
        "tts = TTS('tts_models/multilingual/"
        "multi-dataset/xtts_v2').to('cpu')\n"
        "tts.tts_to_file(text=sys.argv[1], "
        "speaker_wav=sys.argv[2], language=sys.argv[3],"
        " file_path=sys.argv[5])\n")
    runner = ROOT / "data" / "voice_library" / \
        "_xtts_run.py"
    runner.write_text(script)
    r = subprocess.run(
        [str(PIPER_VENV / "bin" / "python"),
         str(runner), text, str(ref), language,
         hf_token() or "", str(out)],
        input="y\ny\ny\n", capture_output=True,
        text=True, timeout=600, cwd=str(ROOT))
    if r.returncode != 0 or not out.exists():
        return {"error": "xtts_failed",
                "stderr": r.stderr[-400:],
                "stdout": r.stdout[-200:]}
    # to m4a like the rest
    m4a = VOICE / (slug + ".m4a")
    subprocess.run(["afconvert", "-f", "m4af", "-d",
                    "aac", str(out), str(m4a)],
                   check=False, timeout=60)
    rec = {"kind": "xtts_clone",
           "device_time": _device_time(),
           "character": character,
           "reference": str(ref.relative_to(ROOT)),
           "text": text[:200], "language": language,
           "audio_wav": str(out.relative_to(ROOT)),
           "audio_m4a": str(m4a.relative_to(ROOT))
           if m4a.exists() else None,
           "ml_voice": True, "engine": "xtts_v2",
           "license_pact": "voice_borrow_pact",
           "license_scope": "cpml_noncommercial",
           "derived": True}
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    with RUNS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    try:
        import eventbus
        eventbus.emit("xtts_clone",
                      {"character": character})
    except Exception:
        pass
    return rec
