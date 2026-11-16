"""Sonify — render the tablet's wedge formations as audio.

The OCR formations become sound: each cell's dominant
wedge-orientation class maps to a frequency, its depth
score to amplitude, and columns play right-to-left in
cuneiform reading order. The inscription literally
becomes a waveform — 'decrypt wave patterns' taken
literally.

Mapping (just-intonation-ish, honest synthesis):
  wedge class 0 (horizontal) -> 220.0 Hz  (A3)
  wedge class 1 (vertical)   -> 277.2 Hz  (C#4)
  wedge class 2 (diag-up)    -> 329.6 Hz  (E4)
  wedge class 3 (diag-down)  -> 392.0 Hz  (G4)
  depth score                -> amplitude 0.2-1.0
  secondary class present    -> +5th harmonic, -6dB

Output: data/sea/voice/tablet_sonification.wav (mono
22050Hz) + a sea block describing the mapping.
"""

import json
import math
import struct
import wave
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
OCR = ROOT / "data" / "sea" / "ocr_tablet.json"
OUTW = ROOT / "data" / "sea" / "voice" / \
    "tablet_sonification.wav"
SR = 22050
CELL_MS = 90

FREQS = [220.0, 277.18, 329.63, 392.00]
HARM = 1.5  # fifth above


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def _cell_tone(feat: dict, dur: float) -> list[float]:
    """Synthesize one cell: dominant class fundamental +
    secondary class as quieter fifth."""
    hist = feat.get("wedge_hist") or [0, 0, 0, 0]
    if not any(hist):
        return []
    order = sorted(range(4), key=lambda i: -hist[i])
    dom, sub = order[0], order[1]
    f0 = FREQS[dom]
    depth = min(max(feat.get("depth", 300) / 600.0, 0.15),
                1.0)
    amp = 0.15 + 0.55 * depth
    n = int(SR * dur)
    out = []
    # short attack/decay envelope per cell
    atk = int(n * 0.08)
    rel = int(n * 0.25)
    for i in range(n):
        env = 1.0
        if i < atk:
            env = i / max(atk, 1)
        elif i > n - rel:
            env = (n - i) / max(rel, 1)
        t = i / SR
        s = (math.sin(2 * math.pi * f0 * t) +
             0.4 * math.sin(2 * math.pi * f0 * HARM * t) +
             (0.18 * math.sin(2 * math.pi *
                              FREQS[sub] * HARM * t)
              if hist[sub] > hist[dom] * 0.5 else 0.0))
        out.append(s * env * amp * 0.33)
    return out


def sonify() -> dict:
    if not OCR.exists():
        return {"error": "no_ocr", "hint": "run sea_ocr first"}
    d = json.loads(OCR.read_text())
    trans = d.get("transcription", [])
    # cuneiform reads right-to-left: reverse column order
    cols = list(reversed(trans))
    samples = []
    col_notes = []
    for col in cols:
        cells = col.get("cells", [])
        col_samples = []
        col_dur = 0.0
        for c in cells:
            tone = _cell_tone(c, CELL_MS / 1000.0)
            col_samples.extend(tone)
            col_dur += CELL_MS / 1000.0
            # tiny gap between cells
            col_samples.extend([0.0] * int(SR * 0.012))
        # column gap — slightly longer breath between
        # columns, like a scribe's pause
        col_samples.extend([0.0] * int(SR * 0.09))
        samples.extend(col_samples)
        col_notes.append({
            "column": col.get("column"),
            "cells": len(cells),
            "duration_s": round(col_dur, 2)})
    # normalize
    peak = max((abs(s) for s in samples), default=1.0) or 1.0
    gain = 0.89 / peak
    pcm = b"".join(
        struct.pack("<h", int(max(-1.0, min(1.0, s * gain))
                               * 32767))
        for s in samples)
    OUTW.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUTW), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)
    rec = {"kind": "sonification",
           "device_time": _device_time(),
           "source": "ocr_tablet.json",
           "audio": str(OUTW.relative_to(ROOT)),
           "sample_rate": SR, "duration_s":
           round(len(samples) / SR, 2),
           "columns": len(cols), "cells": d.get("cells"),
           "mapping": {"wedge_class_freqs": FREQS,
                       "fifth_harmonic": HARM,
                       "depth_to_amp": "0.15+0.55*norm",
                       "order": "right-to-left"},
           "column_notes": col_notes[:40],
           "derived": True,
           "note": ("formations sonified — wedge class -> "
                    "pitch, depth -> amplitude; not speech, "
                    "the inscription as waveform")}
    (OUTW.parent / "sonify_tablet.json").write_text(
        json.dumps(rec, indent=2, ensure_ascii=False))
    try:
        import eventbus
        eventbus.emit("sonification",
                      {"duration": rec["duration_s"]})
    except Exception:
        pass
    return rec


if __name__ == "__main__":
    r = sonify()
    print(json.dumps({k: v for k, v in r.items()
                      if k != "column_notes"},
                     indent=2)[:1500])
