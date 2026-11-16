"""VoiceLib — character voice/audio reference library.

Scans game asset dirs (Undertale extracted tree, video
inbox) for audio/video files, matches filenames against
the name record (familytree labels + truename true_names —
the vanitas-book absorb mechanism) until every file gets
a character assignment or stays honestly unassigned.

Each match mints a sim://voiceasset/<slug> URI — the
reference lives inside the simulation like everything
else. mp4/wav/ogg/mp3 all accepted.

Honest boundary: extracted Undertale tree ships music+sfx
(.ogg), NOT voice lines — those live inside data.win.
Entries are tagged role:"music"|"sfx"|"video"|"voice" —
voice refs only appear when real voice samples exist.

Store: data/voice_library/library.jsonl
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "voice_library"
LIB = OUT / "library.jsonl"

AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac", ".m4a",
             ".aac", ".opus"}
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi"}

SCAN_DIRS = [
    DATA / "undertale" / "extracted",
    ROOT / "videos",
    ROOT / "gml" / "SML-1" / "videos",
]

# name aliases beyond familytree labels
ALIASES = {
    "sans": ["sans", "dusttale", "sansfight"],
    "papyrus": ["papyrus", "papyrusboss", "papyruss"],
    "toriel": ["toriel"],
    "flowey": ["flowey", "asriel", "asriel dreemurr"],
    "gaster": ["gaster", "gasterblaster"],
    "undyne": ["undyne", "undynescary", "undynetheme",
               "undynefast", "undynepiano",
               "undynetrue", "undyneboss", "x_undyne"],
    "alphys": ["alphys"],
    "chara": ["chara", "charafight"],
    "muffet": ["muffet"],
    "asgore": ["asgore", "vsasgore"],
    "senketsu": ["senketsu"],
    "reaper_sans": ["reaper"],
    "frisk": ["frisk", "fallen human"],
    "vanitas": ["vanitas"],
    "wd_gaster": ["wdgaster", "w.d. gaster"],
    "chara_remnant": ["remnant"],
    "flowey_true": ["floweytrue", "true flowey"],
}


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def _append(rec: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with LIB.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_",
                  s.lower()).strip("_")[:60]


def _truename_map() -> dict:
    """Vanitas-book absorb: true_name -> node from
    truename/names.jsonl."""
    m = {}
    for r in _jsonl(DATA / "truename" / "names.jsonl") or []:
        tn = (r.get("true_name") or "").lower()
        node = r.get("node")
        if tn and node:
            m[tn] = node
            m.setdefault(tn.replace(" ", ""), node)
    return m


def _match_character(stem: str,
                     true_map: dict) -> tuple:
    """Match filename stem to a character — aliases first,
    then truename absorb, then substring scan. Returns
    (character, matched_via, confidence)."""
    s = stem.lower().replace("-", "_")
    # strip common prefixes
    for pre in ("mus_", "snd_", "vgm_", "stream_"):
        if s.startswith(pre):
            s = s[len(pre):]
    # direct alias token match
    for char, aliases in ALIASES.items():
        for a in aliases:
            if a in s:
                return char, "alias:" + a, "high"
    # truename absorb
    for tn, node in true_map.items():
        if len(tn) > 3 and tn.replace("_", "") in \
                s.replace("_", ""):
            return node, "truename:" + tn, "high"
    # generic substring vs familytree labels
    try:
        import familytree
        for node in familytree.LABEL:
            base = node.split("__")[0]
            if base in s:
                return base, "label:" + base, "medium"
    except Exception:
        pass
    return None, None, "none"


def _role(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    stem = path.stem.lower()
    if stem.startswith(("mus_", "vgm_", "stream_")):
        return "music"
    if stem.startswith("snd_"):
        return "sfx"
    return "voice" if ext == ".wav" else "audio"


def scan() -> dict:
    """Walk scan dirs, match names, build the library."""
    true_map = _truename_map()
    seen = set()
    if LIB.exists():
        for r in _jsonl(LIB) or []:
            seen.add(r.get("file"))
    added, matched, unmatched = 0, 0, 0
    chars = {}
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext not in AUDIO_EXT | VIDEO_EXT:
                continue
            sp = str(p)
            if sp in seen:
                continue
            seen.add(sp)
            char, via, conf = _match_character(p.stem, true_map)
            role = _role(p)
            slug = _slug(char or "unassigned") + "__" + \
                _slug(p.stem)
            rec = {"ts": _device_time()["ts"],
                   "device_time": _device_time(),
                   "kind": "voice_asset",
                   "file": sp, "format": ext.lstrip("."),
                   "role": role, "character": char,
                   "matched_via": via, "confidence": conf,
                   "sim_uri": "sim://voiceasset/" + slug,
                   "size": p.stat().st_size,
                   "derived": True}
            _append(rec)
            added += 1
            if char:
                matched += 1
                chars.setdefault(char, []).append(slug)
            else:
                unmatched += 1
    return {"kind": "voicelib_scan", "added": added,
            "matched": matched, "unmatched": unmatched,
            "characters": {k: len(v) for k, v in
                           chars.items()},
            "truename_keys": len(true_map)}


def library(character: str | None = None) -> dict:
    recs = [r for r in _jsonl(LIB) or []
            if not character or r.get("character") ==
            character]
    by_char = {}
    for r in recs:
        c = r.get("character") or "unassigned"
        by_char.setdefault(c, []).append(r)
    return {"kind": "voicelib", "total": len(recs),
            "characters": {k: len(v) for k, v in
                           by_char.items()},
            "entries": recs[-200:]}


def status() -> dict:
    recs = list(_jsonl(LIB) or [])
    latest = {}
    for r in recs:
        latest[r["file"]] = r
    chars = {}
    for r in latest.values():
        c = r.get("character") or "unassigned"
        chars[c] = chars.get(c, 0) + 1
    return {"kind": "voicelib_state",
            "device_time": _device_time(),
            "assets": len(latest),
            "by_character": chars,
            "note": ("game audio/video -> character refs; "
                     "truename absorb active; voice lines "
                     "need data.win extraction (not yet "
                     "present as files)")}
