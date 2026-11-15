"""Tier->layer conversion — rarity tiers map to stack layer
depth; each node records its MAX outputtable value (full stack)
and MIN outputtable value (base surface). The middle layer is
'THE GAME' — a cross-reality targeting layer that fires skills
into every profile x branch x plane simultaneously.

Store: data/stack/tier_layers.jsonl
       data/stack/reality_targets.jsonl
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "stack"
LAYERS = OUT / "tier_layers.jsonl"
TARGETS = OUT / "reality_targets.jsonl"

import stack
import familytree

# tier -> stack layer depth (deeper tier = deeper layer reach)
TIER_TO_LAYER = {"common": 1, "rare": 2, "unique": 3, "epic": 4,
                 "legendary": 5, "extraordinary": 6, "ex": 7,
                 "sss": 8, "hidden": 9}
LAYER_NAMES = {0: "surface", 1: "L1_variants", 2: "L2_configs",
               3: "L3_terms", 4: "L4_combos", 5: "L5_mechanics",
               6: "L6_truename", 7: "tier_depth", 8: "reality",
               9: "hidden_depth"}


def _tier(node: str) -> str:
    import charpack
    c = charpack.CHAR_PACK.get(node.split("__")[0])
    if c:
        return c["tier"]
    return "hidden" if familytree.HIDDEN.get(node) else "common"


def output_range(node: str, profile: str = "default") -> dict:
    """Record MAX and MIN outputtable values for a node:
    max = full stack power + ability count at deepest plane;
    min = surface-only (L0 + truename) power."""
    full = stack.build_stack(node, profile)
    tn = full["layers"]["L6_truename"] or {}
    tier = _tier(node)
    layer = TIER_TO_LAYER.get(tier, 0)
    # min: only base + truename count toward output
    min_power = round(
        ((tn.get("proximity") or {}).get(
            "save_unlock_amount", 0)) * 0.4 +
        (tn.get("timeline_number") or 0) * 2 +
        stack.TIER_RANK.get(tier, 0) * 10 +
        (3 if full["layers"]["L0_base"]["hidden"] else 0) +
        (5 if tn.get("bugged") else 0), 1)
    max_power = stack._power(full)
    rec = {"ts": time.time(), "kind": "tier_layer",
           "node": node, "profile": profile,
           "tier": tier, "tier_layer": layer,
           "layer_name": LAYER_NAMES.get(layer),
           "min_output": {"power": min_power,
                          "abilities": 1 +
                          (1 if tn else 0)},
           "max_output": {"power": max_power,
                          "abilities": full["total_abilities"]},
           "output_span": round(max_power - min_power, 1),
           "derived": True}
    OUT.mkdir(parents=True, exist_ok=True)
    with LAYERS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def the_game_layer() -> dict:
    """THE MIDDLE LAYER — 'the game' itself: a cross-reality
    targeting layer. target_all() fires a skill into EVERY
    profile x EVERY branch x EVERY plane at once — no limit
    beyond what exists on disk."""
    return {"layer": "the_game",
            "position": "middle",
            "scope": "all_realties",
            "planes": stack.PLANES,
            "mechanic": ("target skills across all given "
                         "realities at once"),
            "limit": "none — bounded only by stored records"}


def target_all(node: str, skill: str,
               profile: str = "default") -> dict:
    """Fire `skill` into every reality: all profiles on disk x
    all planes x all branches the node appears in. Records each
    targeting event — 'no limit'."""
    node = node.lower().replace(" ", "_")
    # every profile dir that has combo records
    profs = [p.name for p in (DATA / "profiles").iterdir()
             if p.is_dir()] if (DATA / "profiles").exists() \
        else [profile]
    if profile not in profs:
        profs.append(profile)
    fired = []
    OUT.mkdir(parents=True, exist_ok=True)
    with TARGETS.open("a") as f:
        for prof in profs:
            for plane in stack.PLANES:
                rec = {"ts": time.time(),
                       "kind": "reality_target",
                       "node": node, "skill": skill,
                       "profile": prof, "plane": plane,
                       "layer": "the_game",
                       "derived": True}
                f.write(json.dumps(rec, ensure_ascii=False)
                        + "\n")
                fired.append({"profile": prof,
                              "plane": plane})
    return {"node": node, "skill": skill,
            "profiles": profs, "planes": stack.PLANES,
            "targets_fired": len(fired),
            "fired": fired[:10], "layer": "the_game",
            "note": "all realities targeted simultaneously"}


def status() -> dict:
    def count(p):
        return sum(1 for _ in p.open()) if p.exists() else 0
    return {"tier_layers": count(LAYERS),
            "reality_targets": count(TARGETS),
            "tier_map": TIER_TO_LAYER,
            "middle_layer": the_game_layer()}
