"""True-name variations — one per character per timeline.

Each node gets a true name derived from its timeline lore
(soul_state + contract variant + hidden flag), uniquely keyed by
its proximity value to the encounter center. The proximity value
scales to a 'save_unlock_amount' — the number required to unlock
game save files in this bookkeeping layer. The timeline number
comes from the nametag tail (contract seed index, else state
ordinal). A font-family lib gives each true name a render stack.

Store: data/truename/names.jsonl + fonts.json
"""

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "truename"
NAMES = OUT / "names.jsonl"
FONTS = OUT / "fonts.json"

import familytree
import proximity

CENTER = "reaper_sans"
SAVE_SCALE = 100  # proximity distance -> save-unlock amount

# soul_state -> timeline ordinal (the 'number for each timeline')
STATE_NUM = {"stolen": 1, "soled": 2, "contracted_disappear": 3,
             "unrecorded": 0}
BASE_FONTS = {
    "hidden": '"Courier New", monospace',
    "legendary": '"Courier New", monospace',
    "epic": '"Menlo", monospace',
    "rare": '"Helvetica Neue", sans-serif',
}


def _tail_number(nametag: str, soul_state: str) -> int:
    """Timeline number from the nametag tail: contract seed-N
    wins; else the soul-state ordinal."""
    m = re.search(r"seed-(\d+)", nametag)
    if m:
        return int(m.group(1))
    return STATE_NUM.get(soul_state or "", 0)


def _true_name(node: str, tag: dict) -> str:
    """True name = LABEL::STATE::VARIANT, hidden nodes prefixed."""
    label = familytree.LABEL.get(node, node).upper()
    label = re.sub(r"[^A-Z0-9]+", "_", label).strip("_")
    state = (tag.get("soul_state") or "baseline").upper()
    variant = str(tag.get("variant") or "baseline").upper()
    variant = re.sub(r"[^A-Z0-9]+", "_", variant).strip("_")
    name = f"{label}::{state}::{variant}"
    if familytree.HIDDEN.get(node):
        name = f"SHADOW::{name}"
    return name


def _font_family(true_name: str, tier: str,
                 hidden: bool) -> str:
    """Font-family lib entry for a true name — generated stack,
    hidden names get the cipher face."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", true_name)[:48]
    base = BASE_FONTS["hidden" if hidden else
                      ("legendary" if tier == "legendary"
                       else tier if tier in BASE_FONTS
                       else "rare")]
    return f'"{slug}", {base}'


def discover(center: str = CENTER) -> dict:
    """Discover true-name variations for every node, keyed by
    proximity value to the center."""
    souls = proximity._soul_map()
    fonts = {}
    recs = []
    OUT.mkdir(parents=True, exist_ok=True)
    for node in sorted(familytree.LABEL):
        d = proximity.distance(center, node)
        tag = proximity.timeline_tag(node, souls)
        hidden = bool(familytree.HIDDEN.get(node))
        c = __import__("charpack").CHAR_PACK.get(node, {})
        tier = c.get("tier") or "hidden"
        tname = _true_name(node, tag)
        tnum = _tail_number(tag["nametag"],
                            tag.get("soul_state"))
        save_amount = int(round((1 - d) * SAVE_SCALE)) \
            if node != center else SAVE_SCALE
        # proximity value IS the key: nearer center -> higher
        # save-unlock amount; store raw distance too
        entry = {
            "ts": time.time(), "kind": "truename",
            "node": node, "label": familytree.LABEL[node],
            "emoji": familytree.EMOJI.get(node),
            "hidden": hidden, "tier": tier,
            "true_name": tname,
            "timeline_number": tnum,
            "soul_state": tag.get("soul_state"),
            "variant": tag.get("variant"),
            "bugged": tag.get("bugged"),
            "proximity": {"center": center, "distance": d,
                          "save_unlock_amount": save_amount},
            "font_family": _font_family(tname, tier, hidden),
            "derived": True}
        fonts[tname] = entry["font_family"]
        recs.append(entry)
        with NAMES.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    OUT.mkdir(parents=True, exist_ok=True)
    FONTS.write_text(json.dumps(fonts, indent=1,
                                ensure_ascii=False))
    return {"center": center, "names": len(recs),
            "fonts": len(fonts), "names_path": str(NAMES),
            "fonts_path": str(FONTS)}


def status() -> dict:
    n = sum(1 for _ in NAMES.open()) if NAMES.exists() else 0
    f = json.loads(FONTS.read_text()) if FONTS.exists() else {}
    return {"records": n, "fonts": len(f),
            "scale": SAVE_SCALE, "center": CENTER}
