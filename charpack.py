"""Character pack — winding word pack of original subtitles for
Gaster, ToA (Senketsu/Chara/Undyne) and Vanitas grimware
characters. Phrase unlocks report a social-security-style status;
each character carries an emoji glyph.

Matching: normalized substring + token overlap on the phrase
winding pack. Unlocks append to data/charpack/unlocks.jsonl.
Emoji pack exposes the full character->emoji->phrases map.
"""

import hashlib
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "charpack"
UNLOCKS = OUT / "unlocks.jsonl"
GGUF = OUT / "charpack.gguf"
ICONS = OUT / "icons"
MANIFEST = OUT / "icons.json"

# accepted icon formats — original kept verbatim, PNG emoji
# preview rendered via PIL when available
ICON_FORMATS = {".png", ".jpg", ".jpeg", ".webp", ".gif",
                ".svg", ".ico", ".bmp", ".avif", ".tif",
                ".tiff"}
EMOJI_SIZE = 128

# winding word pack — original subtitle lines per character.
# grimware = the ToA/Vanitas token the line maps to (decoded, not
# invented). tier = unlock weight.
CHAR_PACK = {
    "gaster": {
        "emoji": "🐘", "tier": "legendary", "origin": "undertale",
        "grimware": ["SENKETSU_GATE", "GATE_BREAKER"],
        "phrases": [
            "it's a beautiful day outside",
            "birds are singing flowers are blooming",
            "on days like these kids like you",
            "should be burning in hell",
            "the bible",
            "don't give up gaster believes in you",
            "you are filled with determination",
        ]},
    "sans": {
        "emoji": "😹", "tier": "legendary", "origin": "undertale",
        "grimware": ["CONTRACTED_DISAPPEAR", "SOUL_VERB"],
        "phrases": [
            "do you wanna have a bad time",
            "it's a beautiful day outside",
            "in this world it's kill or be killed",
            "i'm just a skeleton",
            "nothing lasts forever",
            "you are filled with determination",
        ]},
    "flowey": {
        "emoji": "🌸", "tier": "epic", "origin": "undertale",
        "grimware": ["GRIMOIRE_INK", "LORE_REWRITE"],
        "phrases": [
            "in this world it's kill or be killed",
            "do you wanna have a bad time",
            "once upon a time",
            "the water is warm",
            "geeettttt dunked on",
        ]},
    "toriel": {
        "emoji": "🌷", "tier": "rare", "origin": "undertale",
        "grimware": ["FLOWER_VESSEL", "FLOWER_AWAKING"],
        "phrases": [
            "it's dangerous to go alone take this",
            "dearies i worry about you",
            "you are filled with determination",
        ]},
    "papyrus": {
        "emoji": "📄", "tier": "rare", "origin": "undertale",
        "grimware": ["PAGE_TURN", "PAGE_READ"],
        "phrases": [
            "get dunked on",
            "i'm the strongest skeleton",
            "you can't beat me",
            "geeettttt dunked on",
        ]},
    "alphys": {
        "emoji": "🧪", "tier": "rare", "origin": "undertale",
        "grimware": ["CHRONICLE_HAND"],
        "phrases": [
            "geeettttt dunked on",
            "i'm a little weirdo",
            "science is cool",
            "the royals are such jerks",
        ]},
    "undyne": {
        "emoji": "🏹", "tier": "epic", "origin": "undertale",
        "grimware": ["SCORE_RECORD", "SCORE_COUNCIL"],
        "phrases": [
            "you can do it believe in yourself",
            "all in the name of love",
            "i'm undyne the whale",
            "it's dangerous to go alone",
        ]},
    "asgore": {
        "emoji": "🐐", "tier": "epic", "origin": "undertale",
        "grimware": ["SKY_ARCHIVE", "MARGIN_NOTE"],
        "phrases": [
            "the world is filled with pain",
            "do you wanna have a bad time",
            "you are filled with determination",
        ]},
    "senketsu": {
        "emoji": "⚔️", "tier": "legendary", "origin": "toa",
        "grimware": ["SCORE_RECORD", "SENKETSU_GATE",
                     "GATE_BREAKER"],
        "phrases": [
            "i bear the sins of the past",
            "my blade remembers",
            "the score rises",
            "blood for the lady",
            "i am senketsu",
        ]},
    "chara": {
        "emoji": "🕯️", "tier": "epic", "origin": "toa",
        "grimware": ["FLOWER_VESSEL", "FLOWER_AWAKING"],
        "phrases": [
            "you are filled with determination",
            "the flame that never dies",
            "i am chara",
            "hold on to your humanity",
        ]},
    "vanitas": {
        "emoji": "📖", "tier": "legendary", "origin": "vanitas",
        "grimware": ["GRIMOIRE_INK", "LORE_REWRITE",
                     "PAGE_TURN"],
        "phrases": [
            "my name is vanitas",
            "the book of vanitas",
            "a true name is required",
            "speak the name and the book opens",
            "i am the author of all tales",
        ]},
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def _tokens(s: str) -> set:
    return set(_norm(s).split())


def unlock_status(phrase: str) -> dict:
    """Social-security-style unlock: match a winding phrase against
    the char pack -> character, emoji, tier, grimware decode."""
    p = _norm(phrase)
    ptoks = _tokens(phrase)
    best = None
    for name, c in CHAR_PACK.items():
        for ph in c["phrases"]:
            nph = _norm(ph)
            if nph == p or nph in p or p in nph:
                score = 1.0
            else:
                ov = len(ptoks & _tokens(ph))
                score = ov / max(len(_tokens(ph)), 1)
            if score and (best is None
                          or score > best["score"]):
                best = {"score": score, "character": name,
                        "matched_phrase": ph}
    unlocked = bool(best and best["score"] >= 0.5)
    char = CHAR_PACK.get(best["character"], {}) if best else {}
    fp = hashlib.sha256(p.encode()).hexdigest()[:16]
    rec = {"ts": time.time(), "kind": "charpack_unlock",
           "phrase_fp": fp, "phrase_len": len(phrase),
           "unlocked": unlocked,
           "character": best["character"] if unlocked else None,
           "match": best["matched_phrase"] if unlocked else None,
           "match_score": round(best["score"], 2)
           if unlocked else 0,
           "emoji": char.get("emoji"),
           "tier": char.get("tier"),
           "grimware": char.get("grimware"),
           "origin": char.get("origin"),
           "derived": True}
    OUT.mkdir(parents=True, exist_ok=True)
    with UNLOCKS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def emoji_pack() -> dict:
    """The full char pack as an emoji map."""
    return {name: {"emoji": c["emoji"], "tier": c["tier"],
                   "origin": c["origin"],
                   "grimware": c["grimware"],
                   "phrases": len(c["phrases"])}
            for name, c in CHAR_PACK.items()}


def generate_emoji(character: str) -> dict:
    """Emoji glyph for one character + its winding phrases."""
    c = CHAR_PACK.get(character.lower())
    if not c:
        return {"character": character, "found": False}
    return {"character": character, "found": True,
            "emoji": c["emoji"], "tier": c["tier"],
            "origin": c["origin"], "grimware": c["grimware"],
            "phrases": c["phrases"]}


def _load_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def _save_manifest(m: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=1))


def _render_emoji_png(src: Path, dst: Path) -> bool:
    """Normalize any icon to a square RGBA PNG (emoji-style)."""
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        im = Image.open(src).convert("RGBA")
        im.thumbnail((EMOJI_SIZE, EMOJI_SIZE),
                     Image.LANCZOS)
        canvas = Image.new("RGBA", (EMOJI_SIZE, EMOJI_SIZE),
                           (0, 0, 0, 0))
        canvas.paste(im, ((EMOJI_SIZE - im.width) // 2,
                          (EMOJI_SIZE - im.height) // 2), im)
        dst.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(dst, "PNG")
        return True
    except Exception:
        return False


def install_icon(character: str, src: Path | str,
                 emoji_png: bool = True) -> dict:
    """Install a custom icon for a character. Accepts png/jpg/
    jpeg/webp/gif/svg/ico/bmp/avif/tif — original stored verbatim
    under data/charpack/icons/, emoji PNG preview rendered."""
    character = character.lower()
    if character not in CHAR_PACK:
        return {"character": character, "installed": False,
                "error": "unknown character"}
    src = Path(src)
    ext = src.suffix.lower()
    if ext not in ICON_FORMATS:
        return {"character": character, "installed": False,
                "error": f"unsupported format {ext or '(none)'}",
                "accepted": sorted(ICON_FORMATS)}
    ICONS.mkdir(parents=True, exist_ok=True)
    dest = ICONS / f"{character}{ext}"
    dest.write_bytes(src.read_bytes())
    preview = ICONS / f"{character}.emoji.png"
    rendered = _render_emoji_png(dest, preview) if emoji_png \
        else False
    man = _load_manifest()
    man[character] = {"file": dest.name, "format": ext,
                      "bytes": dest.stat().st_size,
                      "installed_ts": time.time(),
                      "emoji_png": preview.name if rendered
                      else None,
                      "source": str(src)}
    _save_manifest(man)
    return {"character": character, "installed": True,
            "icon": str(dest), "emoji_png": str(preview)
            if rendered else None,
            "format": ext, "bytes": dest.stat().st_size}


def icon_path(character: str) -> Path | None:
    """Resolve installed icon file for a character."""
    man = _load_manifest()
    rec = man.get(character.lower())
    if not rec:
        # fall back to scanning icons dir by prefix
        hits = sorted(ICONS.glob(f"{character.lower()}.*")) \
            if ICONS.exists() else []
        hits = [h for h in hits
                if h.suffix.lower() in ICON_FORMATS]
        return hits[0] if hits else None
    p = ICONS / rec["file"]
    return p if p.exists() else None


def icon_pack() -> dict:
    """Emoji pack view: unicode emoji + installed icon status."""
    man = _load_manifest()
    out = {}
    for name, c in CHAR_PACK.items():
        rec = man.get(name, {})
        icon = icon_path(name)
        out[name] = {"emoji": c["emoji"], "tier": c["tier"],
                     "origin": c["origin"],
                     "icon": rec.get("file") or
                     (icon.name if icon else None),
                     "format": rec.get("format"),
                     "emoji_png": rec.get("emoji_png"),
                     "installed": bool(icon)}
    return out


def install_icon_bytes(character: str, filename: str,
                       data: bytes) -> dict:
    """Install icon from raw bytes (API upload path)."""
    ext = Path(filename).suffix.lower()
    tmp = OUT / f".upload_{character}{ext}"
    OUT.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(data)
    try:
        return install_icon(character, tmp)
    finally:
        tmp.unlink(missing_ok=True)


def pack_gguf() -> dict:
    import namescrape
    recs = []
    if UNLOCKS.exists():
        for line in UNLOCKS.open():
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    recs.append({"kind": "charpack_map", "pack": emoji_pack()})
    return namescrape.write_gguf(recs, path=GGUF)


def status() -> dict:
    def count(p):
        return sum(1 for _ in p.open()) if p.exists() else 0
    unlocked = {}
    if UNLOCKS.exists():
        for line in UNLOCKS.open():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("unlocked"):
                unlocked[r["character"]] = unlocked.get(
                    r["character"], 0) + 1
    return {"characters": len(CHAR_PACK),
            "phrases": sum(len(c["phrases"])
                           for c in CHAR_PACK.values()),
            "unlocks": count(UNLOCKS), "unlocked_chars": unlocked}
