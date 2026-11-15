"""ASCII family tree + summon gate — organizes the char pack
(and hidden characters) into a tree; summoning a node requires
its ancestors to be summoned first. Hidden nodes are 'characters
hidden by the game' — they only emerge through the tree path.

State: data/familytree/summoned.jsonl — append-only summon records.
Render: familytree.render() -> ASCII tree string.
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "familytree"
SUMMONED = OUT / "summoned.jsonl"

import charpack

# tree: parent -> [children]. Roots are franchises/origins.
# hidden=True nodes are 'hidden by the game' — they have no
# public phrases; they only emerge via summon path.
TREE = {
    "undertale": ["toriel", "asgore", "sans", "papyrus",
                  "flowey", "alphys", "undyne", "gaster"],
    "toa": ["senketsu", "chara"],
    "vanitas": ["vanitas"],
    # hidden branch — characters concealed inside the work
    "hidden": ["wd_gaster", "reaper_sans", "muffet",
               "flowey_true", "chara_remnant"],
}
PARENT = {}
for p, kids in TREE.items():
    for k in kids:
        if k != p:
            PARENT[k] = p

HIDDEN = {
    "wd_gaster": {"emoji": "👻", "label": "W.D. Gaster",
                  "note": "the hidden host behind the genre"},
    "reaper_sans": {"emoji": "💀", "label": "Reaper Sans",
                    "note": "sans after the genocide route"},
    "muffet": {"emoji": "🕷️", "label": "Muffet",
               "note": "spider beneath the ruins"},
    "flowey_true": {"emoji": "🌹", "label": "Flowey (true)",
                    "note": "the flower beneath the flower"},
    "chara_remnant": {"emoji": "🕯️", "label": "Chara (remnant)",
                      "note": "what remains after the flames"},
}
EMOJI = {**{k: v["emoji"] for k, v in charpack.CHAR_PACK.items()},
         **{k: v["emoji"] for k, v in HIDDEN.items()}}
LABEL = {**{k: k.replace("_", " ").title()
            for k in charpack.CHAR_PACK},
         **{k: v["label"] for k, v in HIDDEN.items()}}


def _summoned_set() -> set:
    out = set()
    if SUMMONED.exists():
        for line in SUMMONED.open():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("summoned"):
                out.add(r["node"])
    # charpack unlocks count as summon evidence for public chars
    up = ROOT / "data" / "charpack" / "unlocks.jsonl"
    if up.exists():
        for line in up.open():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("unlocked") and r.get("character"):
                out.add(r["character"])
    return out


def _ancestors(node: str) -> list[str]:
    chain = []
    cur = PARENT.get(node)
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        chain.append(cur)
        cur = PARENT.get(cur)
    return chain


def summon(node: str, via: str = "tree") -> dict:
    """Summon a node: requires all ancestors summoned. Emits a
    summon record; hidden nodes get hidden=True + note."""
    node = node.lower().replace(" ", "_")
    known = node in LABEL
    if not known:
        return {"node": node, "summoned": False,
                "error": "unknown node"}
    have = _summoned_set()
    missing = [a for a in _ancestors(node) if a not in have]
    # franchise roots count as satisfied if any member summoned
    roots_needed = [a for a in missing
                    if not any(k in have for k in TREE.get(a, []))]
    if roots_needed:
        # the hidden branch opens once the tree is awake —
        # any 3+ summons count as 'the game has been entered'
        if "hidden" in _ancestors(node) or node in HIDDEN:
            if len(have) >= 3:
                roots_needed = []
        if roots_needed:
            return {"node": node, "summoned": False,
                    "error": "ancestors required",
                    "missing": roots_needed}
    hidden = node in HIDDEN
    rec = {"ts": time.time(), "kind": "familytree_summon",
           "node": node, "label": LABEL[node],
           "emoji": EMOJI.get(node), "hidden": hidden,
           "via": via, "summoned": True,
           "note": HIDDEN.get(node, {}).get("note"),
           "ancestors": _ancestors(node)}
    OUT.mkdir(parents=True, exist_ok=True)
    with SUMMONED.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def render() -> str:
    """ASCII family tree of all nodes with summon state."""
    have = _summoned_set()
    lines = ["ATLAS FAMILY TREE — arise & shadow",
             "=" * 46]
    for root, kids in TREE.items():
        root_mark = "◆" if root in have or any(
            k in have for k in kids) else "◇"
        lines.append(f"{root_mark} {root.upper()}")
        for k in kids:
            mark = "★" if k in have else ("▒" if k in HIDDEN
                                          else "·")
            tag = " [hidden]" if k in HIDDEN else ""
            note = HIDDEN.get(k, {}).get("note", "")
            suffix = f"  — {note}" if note and k in have else ""
            lines.append(f"    {mark} {EMOJI.get(k,' ')} "
                         f"{LABEL.get(k,k)}{tag}{suffix}")
    lines.append("=" * 46)
    lines.append("★ summoned   ▒ hidden-locked   · dormant")
    return "\n".join(lines)


def status() -> dict:
    have = _summoned_set()
    return {"nodes": len(LABEL), "hidden": len(HIDDEN),
            "summoned": sorted(have),
            "tree": TREE}
