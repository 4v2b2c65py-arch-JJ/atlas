"""Character soul-variants — every design variant is a distinct
soul/timeline state, hence a distinct true name.

Sources: Undertale canon boss forms (Omega Flowey, Undyne the
Undying, Mettaton EX/NEO) + well-known AU soul variants
(Error/Ink/Dream/Nightmare/Killer/Dust/Blue/Edgy/Cross/Geno...).
Each variant registers as a node under its base character so
familytree/proximity/truename/branches all pick it up.

canon=True  -> exists in the shipped game
canon=False -> AU/fan soul-state (still a real design variant)
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "variants"
RECORDS = OUT / "variants.jsonl"

import familytree
import charpack

# base -> [(variant_slug, display, canon, note)]
VARIANTS = {
    "sans": [
        ("error", "Error!Sans", False, "Errortale — void entity"),
        ("ink", "Ink!Sans", False, "Inktale — doodle sphere"),
        ("dream", "Dream!Sans", False, "Dreamtale"),
        ("nightmare", "Nightmare!Sans", False, "Dreamtale dark"),
        ("killer", "Killer!Sans", False, "Something New — with Chara"),
        ("dust", "Dust!Sans", False, "Dusttale — murder timeline"),
        ("blue", "Blueberry Sans", False, "Underswap — Papyrus role"),
        ("edgy", "Edgy Sans", False, "Underfell — inverted route"),
        ("geno", "Geno Sans", False, "Aftertale — post-genocide"),
        ("reaper", "Reaper Sans", True, "genocide-route boss form"),
        ("gaster_sans", "Gaster Sans", False, "Gastertale fusion"),
        ("cross", "Cross!Sans", False, "Underverse — soul split"),
        ("fresh_ink", "Fresh!Ink", False, "Underfresh merge"),
        ("horror", "Horror Sans", False, "Horrortale"),
        ("king_multiverse", "King Multiverse Sans", False,
         "Alphatale omniversal"),
        ("everything", "Everything!Sans", False,
         "True Godverse — all souls"),
    ],
    "flowey": [
        ("omega", "Omega Flowey", True,
         "six human souls absorbed — neutral boss"),
        ("photoshop", "Photoshop Flowey", True,
         "credits name for the boss battle"),
        ("asriel", "Asriel", True,
         "Flowey's return — god of hyperdeath"),
        ("outer", "Outer Flowey", False, "Outertale"),
        ("fell", "Fell Flowey", False, "Underfell"),
        ("horror", "Horror Flowey", False, "Horrortale"),
        ("negative", "Negative Flowey", False, "AU inversion"),
    ],
    "undyne": [
        ("the_undying", "Undyne the Undying", True,
         "genocide-route boss — horned form"),
        ("true", "Undyne (true)", False, "post-credits form"),
    ],
    "papyrus": [
        ("blueberry", "Blueberry Papyrus", False,
         "Underswap — Sans role"),
        ("fell", "Fell Papyrus", False, "Underfell"),
        ("outer", "Outer Papyrus", False, "Outertale"),
        ("horror", "Horror Papyrus", False, "Horrortale"),
    ],
    "alphys": [
        ("undying_lab", "Alphys (ruined lab)", False,
         "amalgamate-fused AU state"),
        ("outer", "Outer Alphys", False, "Outertale"),
    ],
    "toriel": [
        ("fell", "Fell Toriel", False, "Underfell — dark mother"),
        ("outer", "Outer Toriel", False, "Outertale"),
        ("horror", "Horror Toriel", False, "Horrortale"),
    ],
    "asgore": [
        ("asriel_dreemurr", "Asriel Dreemurr", True,
         "the first fallen human — Flowey's origin"),
        ("omega", "Omega Asgore", False, "AU powered form"),
    ],
    "chara": [
        ("unbound", "Unbound Chara", False,
         "determination unbound — canon-adjacent"),
        ("killing", "Killing Chara", False,
         "Something New — genocide partner"),
    ],
    "gaster": [
        ("void", "Gaster (void)", True,
         "fallen in the void — W.D. host"),
        ("blaster", "Gaster Blaster", False,
         "the weapon sprite — separate soul"),
        ("dead", "Dead Gaster", False, "post-fall state"),
    ],
    "muffet": [
        ("true", "True Muffet", False,
         "unmasked spider form — fan canon-adjacent"),
    ],
    "senketsu": [
        ("true", "True Senketsu", False,
         "ToA full-power blade soul"),
    ],
    "vanitas": [
        ("true", "True Vanitas", False,
         "Vanitas no Shirabe — author form"),
    ],
}


def variant_nodes() -> list[str]:
    return [f"{b}__{v[0]}" for b, vs in VARIANTS.items()
            for v in vs]


def register() -> int:
    """Register every variant as a familytree node under its
    base character (as a child of the base's branch)."""
    n = 0
    for base, vs in VARIANTS.items():
        for slug, disp, canon, note in vs:
            node = f"{base}__{slug}"
            familytree.LABEL[node] = disp
            familytree.EMOJI[node] = \
                familytree.EMOJI.get(base, "✨")
            familytree.PARENT[node] = base
            # canon variants are visible nodes; AU/hidden ones
            # stay hidden-locked — only store True, never False
            familytree.HIDDEN.pop(node, None)
            if (not canon) or node.startswith("gaster__"):
                familytree.HIDDEN[node] = {
                    "emoji": familytree.EMOJI.get(base, "✨"),
                    "label": disp,
                    "note": f"{note} [{base} variant]"}
            n += 1
    return n


def records() -> list[dict]:
    recs = []
    for base, vs in VARIANTS.items():
        for slug, disp, canon, note in vs:
            recs.append({"ts": time.time(),
                         "kind": "soul_variant",
                         "node": f"{base}__{slug}",
                         "base": base, "variant": slug,
                         "display": disp, "canon": canon,
                         "note": note, "derived": True})
    return recs


def store() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    recs = records()
    with RECORDS.open("w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"variants": len(recs), "bases": len(VARIANTS),
            "stored": str(RECORDS)}


def status() -> dict:
    n = sum(1 for _ in RECORDS.open()) \
        if RECORDS.exists() else 0
    return {"variants": n, "bases": len(VARIANTS),
            "canon": sum(1 for r in records() if r["canon"]),
            "au": sum(1 for r in records() if not r["canon"])}


# ---------- difficulty / settings configs ----------
# A config is a soul-state modifier applied across a scope. Applying
# one appends <node>__<cfg> variant records and registers hidden
# nodes — the variant list itself changes.
CONFIGS = {
    "nightmare": {
        "label": "Nightmare Mode",
        "scope": "undertale",           # franchise branch
        "rule": "LV locked, no EXP gain, "
                "enemies +ATK — Hard Mode rules",
        "multiplier": 1.5,              # save-cost multiplier
    },
    "hard_mode": {
        "label": "Hard Mode",
        "scope": "undertale",
        "rule": "start LV19, no leveling — "
                "Undertale Hard Mode",
        "multiplier": 1.2,
    },
    "post_genocide": {
        "label": "Post-Genocide Timeline",
        "scope": "all",
        "rule": "world already destroyed once — "
                "endings permanently altered",
        "multiplier": 2.0,
    },
    "true_reset": {
        "label": "True Reset",
        "scope": "all",
        "rule": "soul returned — timelines restacked, "
                "determination retained",
        "multiplier": 0.5,
    },
    "merged_au": {
        "label": "Merged AU (Fusiontale)",
        "scope": "all",
        "rule": "multiple soul variants fused into one node — "
                "design composite",
        "multiplier": 1.0,
    },
}


def _in_scope(node: str, scope: str) -> bool:
    if scope == "all":
        return True
    # node -> franchise root
    cur = node
    seen = set()
    while cur in familytree.PARENT and cur not in seen:
        seen.add(cur)
        cur = familytree.PARENT[cur]
    return cur == scope or cur in familytree.TREE.get(scope, [])


def apply_config(cfg: str) -> dict:
    """Apply a difficulty/settings config: appends a variant
    record + registers a hidden node for every in-scope entry.
    Returns what changed."""
    cfg = cfg.lower().replace(" ", "_")
    conf = CONFIGS.get(cfg)
    if not conf:
        return {"config": cfg, "applied": False,
                "known": sorted(CONFIGS)}
    targets = []
    # base pack nodes + existing variants in scope
    bases = [n for n in familytree.LABEL
             if "__" not in n and _in_scope(n, conf["scope"])]
    variants_in = [n for n in familytree.LABEL
                   if "__" in n and _in_scope(n, conf["scope"])]
    targets = bases + variants_in
    OUT.mkdir(parents=True, exist_ok=True)
    added = []
    with RECORDS.open("a") as f:
        for node in targets:
            vname = f"{node}__{cfg}"
            rec = {"ts": time.time(), "kind": "config_variant",
                   "node": vname, "base": node, "config": cfg,
                   "config_label": conf["label"],
                   "rule": conf["rule"],
                   "multiplier": conf["multiplier"],
                   "scope": conf["scope"], "derived": True}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            # register as hidden variant node
            familytree.LABEL[vname] = \
                f"{familytree.LABEL.get(node, node)} [{conf['label']}]"
            familytree.EMOJI[vname] = familytree.EMOJI.get(
                node.split("__")[0], "🎚️")
            familytree.PARENT[vname] = node
            familytree.HIDDEN[vname] = {
                "emoji": familytree.EMOJI.get(
                    node.split("__")[0], "🎚️"),
                "label": rec["config_label"],
                "note": f"{conf['rule']} [config:{cfg}]"}
            added.append(vname)
    return {"config": cfg, "applied": True,
            "label": conf["label"], "scope": conf["scope"],
            "rule": conf["rule"], "multiplier": conf["multiplier"],
            "nodes_added": len(added), "sample": added[:5]}
