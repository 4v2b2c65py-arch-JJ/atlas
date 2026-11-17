"""audiostretch — consonant length ('cosonant lengh').

Pitch-preserving duration extension via WSOLA-lite:
analysis frames overlapped-added at a different
synthesis hop — duration stretches, pitch stays.

Used to grow the short Undertale voice blips
(flowey_talk = 0.06s) into XTTS-eligible ~6s clone
references: tile the blips + stretch to target.

Store: data/voice_refs/*_ext.wav
"""

import json
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
REFS = ROOT / "data" / "voice_refs"
SR = 44100
N = 2048
HOP = 512


def _now() -> dict:
    n = datetime.now().astimezone()
    return {"ts": n.timestamp(),
            "iso": n.isoformat(timespec="milliseconds"),
            "timezone": n.tzname() or "local",
            "date": n.date().isoformat()}


def _read(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
        ch = w.getnchannels()
        fr = w.getframerate()
        sw = w.getsampwidth()
    if sw == 2:
        x = np.frombuffer(raw, dtype=np.int16) \
            .astype(np.float32) / 32768.0
    else:
        x = np.frombuffer(raw, dtype=np.uint8) \
            .astype(np.float32) / 128 - 1
    if ch > 1:
        x = x.reshape(-1, ch).mean(1)
    if fr != SR:
        n = int(len(x) * SR / fr)
        x = np.interp(np.linspace(0, len(x) - 1, n),
                      np.arange(len(x)), x).astype(
            np.float32)
    return x


def _write(path: Path, x: np.ndarray) -> None:
    x = np.clip(x, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(
            (x * 32767).astype(np.int16).tobytes())


def stretch(x: np.ndarray, rate: float) -> np.ndarray:
    """WSOLA-lite: analysis hop HOP, synthesis hop
    HOP*rate — output is `rate` times longer, pitch
    preserved (frame content reused, not resampled)."""
    if rate <= 0 or len(x) < N:
        return x
    win = np.hanning(N).astype(np.float32)
    syn_hop = max(1, int(round(HOP * rate)))
    n_out = int(len(x) / HOP * syn_hop) + N
    out = np.zeros(n_out, dtype=np.float32)
    norm = np.zeros(n_out, dtype=np.float32)
    pos = 0
    while pos + N <= len(x):
        frame = x[pos:pos + N] * win
        o = int(pos / HOP * syn_hop)
        out[o:o + N] += frame
        norm[o:o + N] += win ** 2
        pos += HOP
    nz = norm > 1e-6
    out[nz] /= norm[nz]
    return out


def extend(path: Path, target_s: float = 6.5,
           out_name: str | None = None) -> dict:
    """Stretch one wav to target duration."""
    x = _read(path)
    dur = len(x) / SR
    if dur >= target_s:
        rate = 1.0
    else:
        rate = target_s / dur
    y = stretch(x, min(rate, 8.0))
    out = path.with_name(
        out_name or (path.stem + "_ext.wav"))
    _write(out, y)
    return {"kind": "stretch_run",
            "device_time": _now(),
            "src": path.name, "out": out.name,
            "src_s": round(dur, 2),
            "rate": round(rate, 2),
            "out_s": round(len(y) / SR, 2),
            "method": "wsola_lite",
            "note": ("consonant length — pitch-"
                     "preserving duration extension")}


def make_clone_refs(target_s: float = 6.5) -> dict:
    """Grow every voice_ref into an XTTS-eligible
    extended reference. Blips get tiled+stretched;
    long-enough refs pass through."""
    made = []
    for p in sorted(REFS.glob("*.wav")):
        if p.stem.endswith("_ext"):
            continue
        x = _read(p)
        dur = len(x) / SR
        # tile short blips to ~target then stretch mildly
        if dur < target_s * 0.5:
            reps = int(target_s / max(dur, 0.05)) + 1
            y = np.tile(x, reps)
            y = stretch(y, target_s * SR / len(y))
            out = p.with_name(p.stem + "_ext.wav")
            _write(out, y)
            made.append({"src": p.name, "out": out.name,
                         "src_s": round(dur, 2),
                         "out_s": round(len(y) / SR, 2),
                         "tiled": reps})
        elif dur < target_s:
            r = extend(p, target_s)
            made.append({"src": p.name,
                         "out": r["out"],
                         "src_s": r["src_s"],
                         "out_s": r["out_s"],
                         "tiled": 0})
    rec = {"kind": "clone_refs",
           "device_time": _now(),
           "target_s": target_s,
           "made": made,
           "note": ("consonant lengh applied — blips "
                    "tiled + pitch-preserved stretch to "
                    "XTTS-eligible duration")}
    return rec


def status() -> dict:
    refs = [p.name for p in REFS.glob("*_ext.wav")] \
        if REFS.exists() else []
    return {"kind": "audiostretch_state",
            "device_time": _now(),
            "extended_refs": refs,
            "method": "wsola_lite pitch-preserving",
            "note": ("cosonant lengh — duration via "
                     "consonant length, not resample")}
