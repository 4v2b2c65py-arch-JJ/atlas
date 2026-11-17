"""piececlone — compaction by piece, grid-keyed by slug.

Text splits into pieces; each piece's KEY PHRASE is its
slug — the piece is content-addressed by slug in a shared
cache (data/voice_pieces/_cache/<slug>.wav), so identical
phrases reuse one synthesis across every text and every
character attempt that shares the reference.

Each clone writes a GRID manifest (not an array): pieces
arranged ceil(sqrt(n)) cols x rows; cells carry
{r, c, slug, text, cache_file}. replay() reads the grid
row-major by default, or accepts an explicit order of
slugs/indices — pieces get reused and resorted by
whichever demand pattern turns up.

Runner loads XTTS ONCE, loops pieces, writes cache files
— retries resume (existing cache files skip).
"""

import hashlib
import json
import math
import re
import subprocess
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
PIECES = ROOT / "data" / "voice_pieces"
CACHE = PIECES / "_cache"
SR = 24000
GAP_S = 0.06


def _now() -> dict:
    n = datetime.now().astimezone()
    return {"ts": n.timestamp(),
            "iso": n.isoformat(timespec="milliseconds"),
            "timezone": n.tzname() or "local",
            "date": n.date().isoformat()}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _piece_slug(text: str) -> str:
    """The key phrase IS the slug — content-addressed."""
    return _slug(text)[:48] or "piece"


def split_pieces(text: str, max_words: int = 4) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    pieces = []
    for sent in parts:
        words = sent.split()
        for i in range(0, len(words), max_words):
            pieces.append(" ".join(words[i:i + max_words]))
    return [p for p in pieces if p]


def _grid(pieces: list[str]) -> dict:
    """Arrange pieces into a grid — cells keyed by slug."""
    n = len(pieces)
    cols = max(1, math.ceil(math.sqrt(n)))
    cells = []
    for i, text in enumerate(pieces):
        sl = _piece_slug(text)
        cells.append({"r": i // cols, "c": i % cols,
                      "slug": sl, "text": text,
                      "cache": "_cache/%s.wav" % sl})
    return {"rows": math.ceil(n / cols), "cols": cols,
            "cells": cells}


def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
        ch = w.getnchannels()
        fr = w.getframerate()
        sw = w.getsampwidth()
    if sw == 2:
        x = np.frombuffer(raw, dtype=np.int16) \
            .astype(np.float32) / 32768.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype=np.int32) \
            .astype(np.float32) / 2147483648.0
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


def _write_wav(path: Path, x: np.ndarray) -> None:
    x = np.clip(x, -1, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(
            (x * 32767).astype(np.int16).tobytes())


RUNNER = '''import sys, os, re
from TTS.api import TTS
os.environ["HF_TOKEN"] = os.environ.get("ATLAS_HF_TOKEN", "")
pieces_file, ref, lang, cache_dir = sys.argv[1:5]
def slug(t):
    return re.sub(r"[^a-z0-9]+", "_", t.lower()).strip("_")[:48]
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to("cpu")
for i, line in enumerate(open(pieces_file)):
    text = line.strip()
    if not text:
        continue
    sl = slug(text) or "piece_%03d" % i
    out = os.path.join(cache_dir, sl + ".wav")
    if os.path.exists(out):
        print("cached", sl, flush=True)
        continue
    tts.tts_to_file(text=text, speaker_wav=ref,
                    language=lang, file_path=out)
    print("done", sl, flush=True)
'''


def clone_pieces(text: str, character: str,
                 out_name: str | None = None,
                 language: str = "en",
                 max_words: int = 4) -> dict:
    """Grid-keyed piece synthesis — one XTTS load,
    slug-addressed cache, grid manifest per clone."""
    import xtts
    st = xtts.available()
    if not st["ready"]:
        return {"error": "xtts_not_ready",
                "missing": st["missing"]}
    ref = xtts.reference_for(character)
    if not ref:
        return {"error": "no_reference",
                "character": character}
    pieces = split_pieces(text, max_words)
    slug = out_name or _slug(character + "_" +
                             text[:24])
    pdir = PIECES / slug
    pdir.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    pf = pdir / "pieces.txt"
    pf.write_text("\n".join(pieces) + "\n")
    grid = _grid(pieces)
    (pdir / "grid.json").write_text(
        json.dumps(grid, ensure_ascii=False, indent=2))
    runner = ROOT / "data" / "voice_library" / \
        "_piece_run.py"
    runner.write_text(RUNNER)
    import os
    env = dict(os.environ)
    env["ATLAS_HF_TOKEN"] = xtts.hf_token() or ""
    cached_before = len(list(CACHE.glob("*.wav")))
    r = subprocess.run(
        [str(ROOT / ".venv-voice" / "bin" / "python"),
         str(runner), str(pf), str(ref), language,
         str(CACHE)],
        input="y\ny\ny\n", capture_output=True,
        text=True, timeout=1800, cwd=str(ROOT),
        env=env)
    made = [c for c in grid["cells"]
            if (CACHE / (c["slug"] + ".wav")).exists()]
    rec = {"kind": "piece_clone",
           "device_time": _now(),
           "character": character,
           "reference": ref.name,
           "text": text[:200],
           "grid": {"rows": grid["rows"],
                    "cols": grid["cols"],
                    "cells": len(grid["cells"])},
           "n_pieces": len(pieces),
           "resolved": len(made),
           "cache_total": len(list(CACHE.glob("*.wav"))),
           "cache_before": cached_before,
           "dir": str(pdir.relative_to(ROOT)),
           "engine": "xtts_v2_pieces_grid",
           "ml_voice": True,
           "license_pact": "voice_borrow_pact",
           "derived": True,
           "stderr_tail": r.stderr[-200:] if
           r.returncode else ""}
    mdir = PIECES / "manifests"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / (slug + ".json")).write_text(
        json.dumps(rec, ensure_ascii=False, indent=2))
    try:
        import eventbus
        eventbus.emit("piece_clone",
                      {"slug": slug,
                       "cells": len(grid["cells"])})
    except Exception:
        pass
    return rec


def _cell_files(slug: str) -> list[Path]:
    """Resolve a clone's grid cells to cache wavs in
    row-major order (or custom order via replay)."""
    pdir = PIECES / slug
    if not pdir.exists():
        cands = [p for p in PIECES.iterdir()
                 if p.is_dir() and slug in p.name]
        if cands:
            pdir = cands[0]
        else:
            return []
    gp = pdir / "grid.json"
    if gp.exists():
        grid = json.loads(gp.read_text())
        cells = sorted(grid["cells"],
                       key=lambda c: (c["r"], c["c"]))
        return [CACHE / (c["slug"] + ".wav")
                for c in cells
                if (CACHE / (c["slug"] + ".wav")).exists()]
    # legacy piece_%03d dirs
    return sorted(pdir.glob("piece_*.wav"))


def replay(slug: str, order: list | None = None,
           out_name: str | None = None) -> dict:
    """Piece-per-bit replay — default grid row-major;
    `order` = list of piece slugs or cell indices to
    resort by demand pattern."""
    pdir = PIECES / slug
    if not pdir.exists():
        cands = [p for p in PIECES.iterdir()
                 if p.is_dir() and slug in p.name]
        pdir = cands[0] if cands else None
    if not pdir:
        return {"error": "no_piece_dir", "slug": slug}
    gp = pdir / "grid.json"
    files = _cell_files(slug)
    if order is not None and gp.exists():
        grid = json.loads(gp.read_text())
        cells = sorted(grid["cells"],
                       key=lambda c: (c["r"], c["c"]))
        picked = []
        for o in order:
            if isinstance(o, int) and 0 <= o < len(cells):
                picked.append(cells[o])
            else:
                for c in cells:
                    if c["slug"] == o or c["text"] == o:
                        picked.append(c)
                        break
        files = [CACHE / (c["slug"] + ".wav")
                 for c in picked
                 if (CACHE / (c["slug"] + ".wav")).exists()]
    if not files:
        return {"error": "no_pieces", "dir": str(pdir)}
    gap = np.zeros(int(GAP_S * SR), dtype=np.float32)
    parts = []
    for i, fp in enumerate(files):
        parts.append(_read_wav(fp))
        if i < len(files) - 1:
            parts.append(gap)
    y = np.concatenate(parts)
    peak = np.abs(y).max() or 1
    if peak > 0.95:
        y = y * (0.95 / peak)
    out = ROOT / "data" / "sea" / "voice" / \
        (out_name or (slug + "_replay.wav"))
    _write_wav(out, y)
    rec = {"kind": "piece_replay",
           "device_time": _now(),
           "slug": slug,
           "pieces": len(files),
           "custom_order": order is not None,
           "audio": str(out.relative_to(ROOT)),
           "duration_s": round(len(y) / SR, 2),
           "sr": SR,
           "derived": True}
    try:
        import eventbus
        eventbus.emit("piece_replay", {"slug": slug})
    except Exception:
        pass
    return rec


def grid(slug: str) -> dict:
    """The grid view — cells with slug/text/cache."""
    pdir = PIECES / slug
    if not pdir.exists():
        cands = [p for p in PIECES.iterdir()
                 if p.is_dir() and slug in p.name]
        pdir = cands[0] if cands else None
    if not pdir or not (pdir / "grid.json").exists():
        return {"error": "no_grid", "slug": slug}
    g = json.loads((pdir / "grid.json").read_text())
    for c in g["cells"]:
        c["resolved"] = (CACHE /
                         (c["slug"] + ".wav")).exists()
    return {"kind": "piece_grid", "slug": pdir.name,
            "rows": g["rows"], "cols": g["cols"],
            "cells": g["cells"]}


def status() -> dict:
    dirs = [p.name for p in PIECES.iterdir()
            if p.is_dir() and p.name != "_cache"] \
        if PIECES.exists() else []
    ncache = len(list(CACHE.glob("*.wav"))) \
        if CACHE.exists() else 0
    return {"kind": "piececlone_state",
            "device_time": _now(),
            "folders": dirs,
            "cache_pieces": ncache,
            "note": ("grid-keyed by slug — pieces reuse "
                     "across texts, replay resorts by "
                     "demand pattern")}
