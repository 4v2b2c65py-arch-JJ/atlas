"""Ability-stack engine — the extreme resolver.

Terrain is NOT loaded geometry: the world is each character's
ability stack. Layers (bottom->top):
  L0 base node
  L1 soul variants      (data/variants/variants.jsonl)
  L2 config variants    (config_variant records, same file)
  L3 term variants      (data/termlib/applied.jsonl)
  L4 timeline combos    (data/profiles/<p>/timeline_combos.jsonl)
  L5 mechanic combos    (data/profiles/<p>/mechanic_combos.jsonl)
  L6 true names         (data/truename/names.jsonl, latest wins)

Planes (dimension levels):
  plane_1d   stat line
  plane_2d   ability grid (direct variant children)
  plane_2d5d combos + trigger bindings
  plane_3d   trigger x env state cube
  plane_4d   timeline branch slots (branches.jsonl variation_id)

warp() resolves a node at a plane + env; engage() pits two
stacks against each other with a composite power verdict.
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"

import familytree
import proximity
import truename
import mechanics

PLANES = ["plane_1d", "plane_2d", "plane_2d5d", "plane_3d",
          "plane_4d"]
TIER_RANK = {"common": 0, "rare": 1, "unique": 2, "epic": 3,
             "legendary": 4, "extraordinary": 5, "ex": 6,
             "sss": 7, "hidden": 5}


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.open():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _latest_by_node(recs: list[dict]) -> dict:
    latest = {}
    for r in recs:
        n = r.get("node") or r.get("base")
        if n:
            latest[n] = r
    return latest


def build_stack(node: str, profile: str = "default") -> dict:
    """Assemble the full ability stack for a node from every
    persisted store — works across restarts."""
    node = node.lower().replace(" ", "_")
    # kin inheritance: variant/hidden nodes pull their base
    # soul's layers too (reaper_sans inherits sans's stack)
    pool = {node}
    if "__" in node:
        pool.add(node.split("__")[0])
    try:
        import proximity as _px
        kin = _px.KIN.get(node)
        if kin:
            pool.add(kin)
    except Exception:
        pass
    prof = DATA / "profiles" / (profile or "default")
    layers = {"L0_base": {"node": node,
                         "label": familytree.LABEL.get(node),
                         "emoji": familytree.EMOJI.get(node),
                         "hidden": bool(familytree.HIDDEN.get(node))},
              "L1_variants": [], "L2_configs": [],
              "L3_terms": [], "L4_combos": [],
              "L5_mechanics": [], "L6_truename": None}
    # L1+L2 from variants.jsonl
    def _belongs(r):
        n = r.get("node") or ""
        b = r.get("base") or ""
        return b in pool or any(
            n.startswith(p + "__") for p in pool)
    for r in _jsonl(DATA / "variants" / "variants.jsonl"):
        if _belongs(r):
            (layers["L2_configs"] if r.get("kind") ==
             "config_variant" else layers["L1_variants"]
             ).append(r.get("node") or r.get("variant"))
    # L3 terms
    for r in _jsonl(DATA / "termlib" / "applied.jsonl"):
        if _belongs(r):
            layers["L3_terms"].append(r.get("term"))
    # L4 timeline combos
    for r in _jsonl(prof / "timeline_combos.jsonl"):
        if _belongs(r):
            layers["L4_combos"].append(r.get("combo"))
    # L5 mechanic combos
    for r in _jsonl(prof / "mechanic_combos.jsonl"):
        if _belongs(r):
            layers["L5_mechanics"].append(r.get("combo"))
    # L6 truename — latest record for this node
    tn = _latest_by_node(_jsonl(DATA / "truename" / "names.jsonl"))
    layers["L6_truename"] = tn.get(node)
    counts = {k: (len(v) if isinstance(v, list) else
                  (1 if v else 0)) for k, v in layers.items()}
    return {"node": node, "profile": profile,
            "layers": layers, "counts": counts,
            "total_abilities": sum(counts.values()) - 1}


def _power(stack: dict) -> float:
    """Composite power: save amount + timeline number + ability
    count + tier + bug bonus."""
    tn = stack["layers"]["L6_truename"] or {}
    prox = (tn.get("proximity") or {})
    save = prox.get("save_unlock_amount", 0)
    tnum = tn.get("timeline_number", 0)
    tier = TIER_RANK.get(tn.get("tier") or "", 0)
    hidden = 3 if stack["layers"]["L0_base"]["hidden"] else 0
    bug = 5 if tn.get("bugged") else 0
    return round(save * 0.4 + tnum * 2 + tier * 10 +
                 stack["total_abilities"] * 0.5 +
                 hidden + bug, 1)


def plane_view(stack: dict, plane: str,
               env: dict | None = None) -> dict:
    """Render the stack at a given dimensional plane."""
    L = stack["layers"]
    base = L["L0_base"]
    if plane == "plane_1d":
        tn = L["L6_truename"] or {}
        return {"plane": plane, "stat_line": {
            "node": stack["node"],
            "true_name": tn.get("true_name"),
            "save_unlock_amount":
                (tn.get("proximity") or {}).get(
                    "save_unlock_amount"),
            "timeline_number": tn.get("timeline_number"),
            "power": _power(stack)}}
    if plane == "plane_2d":
        children = (L["L1_variants"] + L["L2_configs"] +
                    L["L3_terms"] + L["L4_combos"] +
                    L["L5_mechanics"])
        return {"plane": plane, "grid": {
            "node": stack["node"], "children": children[:50],
            "child_count": len(children),
            "power": _power(stack)}}
    if plane == "plane_2d5d":
        trig_path = DATA / "profiles" / stack["profile"] \
            / "mechanic_triggers.jsonl"
        binds = [r for r in _jsonl(trig_path)
                 if r.get("node") == stack["node"]]
        return {"plane": plane, "depth": {
            "node": stack["node"],
            "trigger_bindings": binds,
            "binding_count": len(binds),
            "power": _power(stack)}}
    if plane == "plane_3d":
        # env-state cube: fire every trigger against env
        cube = {}
        for t in mechanics.TRIGGERS:
            r = mechanics.fire(t, env or {}, stack["profile"])
            mine = [f for f in r["fired"]
                    if f.get("node") == stack["node"]]
            cube[t] = {"fired": len(mine),
                       "top": mine[0]["combo"]
                       if mine else None}
        return {"plane": plane, "cube": cube,
                "env": env or {}, "power": _power(stack)}
    if plane == "plane_4d":
        br = _jsonl(DATA / "truename" / "branches.jsonl")
        mine = [r for r in br if r.get("node") == stack["node"]]
        return {"plane": plane, "branches": mine,
                "branch_count": len(mine),
                "power": _power(stack)}
    return {"error": "unknown plane", "planes": PLANES}


def warp(node: str, plane: str = "plane_3d",
         env: dict | None = None,
         profile: str = "default") -> dict:
    """Full warp resolution: stack + plane render + power +
    fired triggers at this plane."""
    plane = plane if plane in PLANES else "plane_3d"
    stack = build_stack(node, profile)
    view = plane_view(stack, plane, env)
    return {"ts": time.time(), "kind": "warp",
            "node": node, "plane": plane, "env": env or {},
            "profile": profile, "stack_counts": stack["counts"],
            "total_abilities": stack["total_abilities"],
            "power": _power(stack), "view": view,
            "derived": True}


def engage(a: str, b: str, plane: str = "plane_3d",
           env: dict | None = None,
           profile: str = "default") -> dict:
    """Pit two ability stacks: warps both, scores power +
    proximity + env-fired combos, emits verdict."""
    wa = warp(a, plane, env, profile)
    wb = warp(b, plane, env, profile)
    dist = proximity.distance(a, b)
    pa, pb = wa["power"], wb["power"]
    # composite: power diff adjusted by proximity (closer =
    # harder fight = defender bonus)
    edge_a = pa - pb
    verdict = ("a_advanced" if edge_a > 5 else
               "b_advanced" if edge_a < -5 else "dead_even")
    rec = {"ts": time.time(), "kind": "engage",
           "plane": plane, "env": env or {},
           "profile": profile,
           "a": {"node": a, "power": pa,
                 "abilities": wa["total_abilities"]},
           "b": {"node": b, "power": pb,
                 "abilities": wb["total_abilities"]},
           "proximity_distance": dist,
           "power_edge": edge_a,
           "verdict": verdict,
           "warp_a": wa["view"], "warp_b": wb["view"],
           "derived": True}
    pdir = DATA / "profiles" / (profile or "default")
    pdir.mkdir(parents=True, exist_ok=True)
    with (pdir / "engagements.jsonl").open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def status(profile: str = "default") -> dict:
    prof = DATA / "profiles" / (profile or "default")
    counts = {}
    for name in ("timeline_combos.jsonl", "mechanic_combos.jsonl",
                 "mechanic_triggers.jsonl", "engagements.jsonl"):
        p = prof / name
        counts[name] = sum(1 for _ in p.open()) \
            if p.exists() else 0
    return {"profile": profile, "planes": PLANES,
            "layers": ["L0_base", "L1_variants", "L2_configs",
                       "L3_terms", "L4_combos", "L5_mechanics",
                       "L6_truename"],
            "profile_records": counts,
            "tree_nodes": len(familytree.LABEL)}
