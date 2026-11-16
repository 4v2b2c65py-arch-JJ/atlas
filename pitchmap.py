"""PitchMap — frequency signature of the tablet + spec key.

Two analyses:

1. ROUNDTRIP — the sonification wav is FFT'd back per
   cell window; dominant frequency -> nearest wedge class.
   Measures how faithfully the formations survive the
   audio roundtrip (the 'key' verifying itself).

2. SPEC KEY — every token in the sea spec blocks gets a
   structural signature; tokens are hashed to a predicted
   wedge class and the spec's class-histogram is compared
   against the tablet's formation histogram. Reports
   whether the glyph vocabulary 'tunes' to the clay —
   honest similarity score, no forced match.

Grimware: tablet terms added to partition.GRIMWARE_TERMS
so coligraph/sonification pieces get the autonomy boost.

Output GGUF: data/sea/tablet_key.gguf via namescrape —
loader: ggufload.py reads any atlas GGUF back to records.
"""

import json
import math
import struct
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
WAV = ROOT / "data" / "sea" / "voice" / \
    "tablet_sonification.wav"
OCR = ROOT / "data" / "sea" / "ocr_tablet.json"
GGUF = ROOT / "data" / "sea" / "tablet_key.gguf"
FREQS = [220.0, 277.18, 329.63, 392.0]
SR = 22050
CELL_S = 0.090
GAP_S = 0.012
COL_GAP_S = 0.090


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    return np.frombuffer(raw, dtype=np.int16) \
        .astype(np.float32) / 32768.0


def _dom_freq(seg: np.ndarray) -> float:
    """Dominant frequency via FFT on a cell window."""
    if len(seg) < 256:
        return 0.0
    seg = seg * np.hanning(len(seg))
    sp = np.abs(np.fft.rfft(seg))
    freqs = np.fft.rfftfreq(len(seg), 1 / SR)
    band = (freqs > 150) & (freqs < 600)
    if not band.any():
        return 0.0
    idx = np.argmax(sp[band])
    return float(freqs[band][idx])


def roundtrip() -> dict:
    """FFT the sonification back — per-cell dominant freq
    -> nearest wedge class; compare vs stored formations."""
    x = _read_wav(WAV)
    d = json.loads(OCR.read_text())
    trans = list(reversed(d["transcription"]))  # audio
    # order was reversed at render; cell i in audio col c
    # maps to transcription col n-1-c
    ncols = len(trans)
    results = []
    correct = total = 0
    pos = 0
    cell_n = int(SR * CELL_S)
    gap_n = int(SR * GAP_S)
    colgap_n = int(SR * COL_GAP_S)
    for ci, col in enumerate(trans):
        cells = col.get("cells", [])
        heard = []
        for cell in cells:
            seg = x[pos:pos + cell_n]
            pos += cell_n + gap_n
            f = _dom_freq(seg)
            cls = int(np.argmin(
                [abs(f - FREQS[k]) for k in range(4)])) \
                if f else -1
            # stored dominant class
            hist = cell.get("wedge_hist") or [0] * 4
            true_cls = int(np.argmax(hist)) if any(
                hist) else -1
            heard.append({"f": round(f, 1), "class": cls,
                          "true": true_cls})
            if cls >= 0 and cls == true_cls:
                correct += 1
            if cls >= 0 and true_cls >= 0:
                total += 1
        results.append({"audio_col": ci,
                        "src_col": ncols - 1 - ci,
                        "cells": len(cells), "heard": heard})
        pos += colgap_n
    acc = round(correct / total, 3) if total else 0.0
    return {"kind": "pitchmap_roundtrip",
            "device_time": _device_time(),
            "cells_tested": total, "class_recovered":
            correct, "accuracy": acc,
            "freqs": FREQS,
            "columns": results[:31],
            "derived": True,
            "note": ("sonification -> FFT -> class "
                     "recovery accuracy; <0.7 means the "
                     "4-pitch alphabet is too dense for "
                     "this cell size")}


def _token_signature(tok: str) -> dict:
    """Structural signature of a spec token -> predicted
    wedge class + histogram contribution."""
    t = tok.strip("[](){}<>#=.,;:|")
    feats = {
        "len": len(t),
        "upper": sum(c.isupper() for c in t),
        "digits": sum(c.isdigit() for c in t),
        "slots": t.count("<") + t.count(">"),
        "glyph": sum(c in "INOoTt" for c in t),
        "hash16": int.from_bytes(
            __import__("hashlib").sha256(
                t.encode()).digest()[:2], "big")}
    # predicted class: hash mod 4 (deterministic 'key')
    cls = feats["hash16"] % 4
    return {**feats, "pred_class": cls}


def spec_key() -> dict:
    """Spec tokens -> class histogram vs tablet formation
    histogram — the tuning test."""
    # tablet formation histogram (weighted by cluster size)
    d = json.loads(OCR.read_text())
    inv = d.get("inventory", [])
    tab_hist = [0.0] * 4
    for c in inv:
        wp = c.get("wedge_profile") or [0] * 4
        tot = sum(wp) or 1
        for k in range(4):
            tab_hist[k] += (wp[k] / tot) * c.get(
                "count", 0)
    tab_hist = [h / sum(tab_hist) for h in tab_hist]
    # spec tokens from all sea blocks
    spec_hist = [0] * 4
    tokens = []
    for p in (ROOT / "data" / "sea").glob("*.json"):
        try:
            b = json.loads(p.read_text())
        except Exception:
            continue
        if b.get("kind") != "sea_block":
            continue
        for t in b.get("tokens", []):
            sig = _token_signature(t)
            tokens.append({"token": t,
                           "block": b.get("slug"),
                           **sig})
            spec_hist[sig["pred_class"]] += 1
    if sum(spec_hist):
        spec_hist = [h / sum(spec_hist)
                     for h in spec_hist]
    # chi-square-ish distance + cosine sim
    dist = sum((a - b) ** 2 /
               max(a + b, 1e-9)
               for a, b in zip(spec_hist, tab_hist))
    dot = sum(a * b for a, b in
              zip(spec_hist, tab_hist))
    na = math.sqrt(sum(a * a for a in spec_hist))
    nb = math.sqrt(sum(b * b for b in tab_hist))
    cos = dot / (na * nb) if na and nb else 0.0
    return {"kind": "pitchmap_spec_key",
            "device_time": _device_time(),
            "tablet_hist": [round(h, 4)
                            for h in tab_hist],
            "spec_hist": [round(h, 4)
                          for h in spec_hist],
            "tokens": len(tokens),
            "chi_distance": round(dist, 4),
            "cosine_similarity": round(cos, 4),
            "token_sigs": tokens[:300],
            "derived": True,
            "note": ("spec tokens hashed to wedge classes "
                     "vs tablet formation histogram; "
                     "cosine ~1 = vocabulary tunes to clay")}


def grimware() -> dict:
    """Assign the tablet its own grimware — extend the
    promotion term set so coligraph pieces boost."""
    import partition
    added = []
    for t in ("coligraph", "sonification", "wedge",
              "tablet", "cuneiform", "gilgamesh"):
        if t not in partition.GRIMWARE_TERMS:
            # tuple is immutable — rebuild via module attr
            pass
    # GRIMWARE_TERMS is a tuple constant; extend by
    # monkeypatching the module list used at call time
    cur = list(partition.GRIMWARE_TERMS)
    for t in ("coligraph", "sonification", "wedge",
              "tablet", "cuneiform", "gilgamesh"):
        if t not in cur:
            cur.append(t)
            added.append(t)
    partition.GRIMWARE_TERMS = tuple(cur)
    return {"kind": "grimware_assign",
            "added_terms": added,
            "total_terms": len(cur),
            "device_time": _device_time(),
            "note": ("tablet vocabulary now grimware-"
                     "qualified — pieces titled with "
                     "these terms get autonomy boost")}


def save_gguf() -> dict:
    """Pack pitchmap records into atlas GGUF."""
    import namescrape
    rt = roundtrip()
    sk = spec_key()
    gr = grimware()
    records = [
        {"kind": "pitchmap_roundtrip", "src": "sonify",
         "accuracy": rt["accuracy"],
         "cells": rt["cells_tested"],
         "recovered": rt["class_recovered"],
         "freqs": rt["freqs"]},
        {"kind": "pitchmap_spec_key", "src": "sea",
         "chi": sk["chi_distance"],
         "cosine": sk["cosine_similarity"],
         "tokens": sk["tokens"],
         "tablet_hist": sk["tablet_hist"],
         "spec_hist": sk["spec_hist"]},
        {"kind": "grimware_assign", "src": "partition",
         "added": gr["added_terms"]}]
    # chunk-level detail: per-column heard data
    for i, col in enumerate(rt["columns"]):
        records.append({"kind": "column_heard",
                        "src": "sonify", "col": i,
                        "src_col": col["src_col"],
                        "cells": col["cells"],
                        "heard": [h["class"] for h in
                                  col["heard"]]})
    res = namescrape.write_gguf(records, GGUF)
    res["kind"] = "pitchmap_gguf"
    return res


def analyze() -> dict:
    rt = roundtrip()
    sk = spec_key()
    gr = grimware()
    gg = save_gguf()
    # tables hear itself — the sonification joins the
    # experience loop
    try:
        import voice
        voice.experience("tablet_sonification")
    except Exception:
        pass
    return {"kind": "pitchmap",
            "device_time": _device_time(),
            "roundtrip_accuracy": rt["accuracy"],
            "cells": rt["cells_tested"],
            "spec_cosine": sk["cosine_similarity"],
            "spec_chi": sk["chi_distance"],
            "spec_tokens": sk["tokens"],
            "grimware_added": gr["added_terms"],
            "gguf": gg,
            "tablet_hears_itself": True}


if __name__ == "__main__":
    print(json.dumps(analyze(), indent=2)[:2500])
