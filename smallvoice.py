"""SmallVoice — a small voice model built in-house.

No license gate, no 2GB weights: a channel-vocoder-style
timbre transfer. Piper (bootstrap engine) produces
correct phonemes/prosody; the REFERENCE wav contributes
the spectral envelope — the speaker's timbre. Output =
piper's words in the reference voice's color.

Model = long-term average spectrum (LTAS) of the
reference, applied as per-band gain to the bootstrap
signal's STFT magnitudes. ~100 lines of numpy — the
smallest honest voice model that does real conversion.

XTTS can later replace piper as the bootstrap excitation
source once licensed — the envelope layer stays.

Store: data/sea/voice/<slug>_svm.wav + svm_runs.jsonl
"""

import json
import struct
import subprocess
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
VOICE = ROOT / "data" / "sea" / "voice"
RUNS = ROOT / "data" / "voice_library" / "svm_runs.jsonl"
REFS = ROOT / "data" / "voice_refs"
PIPER = ROOT / ".venv-voice" / "bin" / "piper"
MODELS = ROOT / "data" / "voice_models"
SR = 22050
NFFT = 1024
HOP = 256
BANDS = 32  # log-spaced spectral bands


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
        sw = w.getsampwidth()
        ch = w.getnchannels()
        fr = w.getframerate()
    if sw == 2:
        x = np.frombuffer(raw, dtype=np.int16) \
            .astype(np.float32) / 32768.0
    else:
        x = np.frombuffer(raw, dtype=np.uint8) \
            .astype(np.float32) / 128.0 - 1.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    if fr != SR:
        # naive resample via linear interp
        n = int(len(x) * SR / fr)
        x = np.interp(np.linspace(0, len(x) - 1, n),
                      np.arange(len(x)), x).astype(
            np.float32)
    return x


def _write_wav(path: Path, x: np.ndarray) -> None:
    x = np.clip(x, -1, 1)
    pcm = (x * 32767).astype(np.int16).tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)


def _stft(x: np.ndarray):
    win = np.hanning(NFFT).astype(np.float32)
    frames = []
    for i in range(0, len(x) - NFFT + 1, HOP):
        frames.append(np.fft.rfft(
            x[i:i + NFFT] * win))
    return np.array(frames)


def _istft(S: np.ndarray, n: int) -> np.ndarray:
    win = np.hanning(NFFT).astype(np.float32)
    out = np.zeros(n + NFFT)
    wsum = np.zeros(n + NFFT)
    for i, spec in enumerate(S):
        fr = np.fft.irfft(spec, NFFT) * win
        pos = i * HOP
        out[pos:pos + NFFT] += fr
        wsum[pos:pos + NFFT] += win ** 2
    nz = wsum > 1e-6
    out[nz] /= wsum[nz]
    return out[:n]


def _bands() -> np.ndarray:
    """32 log-spaced band edges 80Hz-8kHz."""
    return np.geomspace(80, SR / 2, BANDS + 1)


def _envelope(x: np.ndarray) -> np.ndarray:
    """Long-term average magnitude per band."""
    S = np.abs(_stft(x))
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    edges = _bands()
    env = np.zeros(BANDS)
    for b in range(BANDS):
        m = (freqs >= edges[b]) & (freqs < edges[b + 1])
        if m.any():
            env[b] = S[:, m].mean()
    return env + 1e-9


def _apply_envelope(x: np.ndarray,
                    env: np.ndarray) -> np.ndarray:
    """Reshape x's spectrum toward env per band — keep
    frame-level detail (phonemes), swap band balance
    (timbre)."""
    S = _stft(x)
    mag = np.abs(S)
    ph = np.angle(S)
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    edges = _bands()
    src_env = _envelope(x)
    ratio = env / src_env
    for b in range(BANDS):
        m = (freqs >= edges[b]) & (freqs < edges[b + 1])
        if m.any():
            mag[:, m] *= ratio[b]
    S2 = mag * np.exp(1j * ph)
    return _istft(S2, len(x))


def _piper_wav(text: str, slug: str,
               voice: str = "ryan") -> Path:
    """Bootstrap: piper neural TTS -> raw wav."""
    model = MODELS / ("en_US-%s-high.onnx" % voice
                      if voice == "ryan" else
                      "en_US-%s-medium.onnx" % voice)
    if not model.exists():
        model = next(MODELS.glob("*.onnx"), None)
    wav = VOICE / (slug + "_boot.wav")
    txt = VOICE / (slug + "_boot.txt")
    txt.write_text(text)
    subprocess.run([str(PIPER), "-m", str(model),
                    "-f", str(wav), "-i", str(txt)],
                   check=True, timeout=300, cwd=str(ROOT))
    txt.unlink(missing_ok=True)
    return wav


def convert(text: str, reference: str,
            out_name: str | None = None,
            voice: str = "ryan") -> dict:
    """Bootstrap speech + reference envelope -> small-
    model voice output."""
    ref = None
    for p in REFS.iterdir() if REFS.exists() else []:
        if reference.lower() in p.stem.lower():
            ref = p
            break
    if ref is None:
        return {"error": "no_reference",
                "reference": reference,
                "available": [p.name for p in
                              REFS.iterdir()] if
                REFS.exists() else []}
    slug = out_name or ("svm_" + reference.lower()
                        .replace(" ", "_")[:20])
    boot = _piper_wav(text, slug, voice)
    x_boot = _read_wav(boot)
    x_ref = _read_wav(ref)
    env = _envelope(x_ref)
    y = _apply_envelope(x_boot, env)
    # light normalize
    peak = np.abs(y).max() or 1.0
    if peak > 0.95:
        y = y * (0.95 / peak)
    out = VOICE / (slug + ".wav")
    _write_wav(out, y)
    boot.unlink(missing_ok=True)
    rec = {"kind": "svm_run",
           "device_time": _device_time(),
           "model": "smallvoice_channel_vocoder",
           "bootstrap": "piper_" + voice,
           "reference": ref.name,
           "text": text[:200],
           "audio": str(out.relative_to(ROOT)),
           "bands": BANDS, "sr": SR,
           "ml_voice": "partial",
           "note": ("piper excitation + reference LTAS "
                    "envelope — small in-house voice model; "
                    "xtts can replace bootstrap later"),
           "derived": True}
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    with RUNS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False)
                + "\n")
    try:
        import eventbus
        eventbus.emit("svm_run",
                      {"reference": ref.name})
    except Exception:
        pass
    return rec


def status() -> dict:
    rs = []
    if RUNS.exists():
        for line in RUNS.open():
            try:
                rs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {"kind": "svm_state",
            "device_time": _device_time(),
            "runs": len(rs),
            "model": "channel_vocoder_ltas",
            "references": [p.name for p in
                           REFS.iterdir()] if
            REFS.exists() else [],
            "note": ("small voice model live — piper "
                     "bootstrap + reference envelope; "
                     "xtts bootstrap path reserved")}
