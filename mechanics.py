"""Mechanic combos — game-mechanism pattern terms (movement,
animation, reaction, terrain, dimension, layout, UE-style)
expanded into combo phrases, plus an env-aware trigger system
so combos fire automatically on reactions.

Axes feed combos2(); TRIGGERS bind combos to reaction events;
fire() evaluates trigger + env flags (dimension, terrain_state,
nearby nodes, animation state) and emits matching combos.

Store: data/profiles/<profile>/mechanic_combos.jsonl
       data/profiles/<profile>/mechanic_triggers.jsonl
"""

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "profiles"

import familytree

# ---- mechanic term axes (game-dev vocabulary) ----
MOVEMENT = ["pixel_move", "polygon_move", "grid_move",
            "smooth_move", "tween_move", "lerp_move",
            "spring_move", "knockback_move", "dash_move",
            "hover_move", "slide_move", "teleport_move",
            "navmesh_move", "physics_move", "kinematic_move"]
ANIMATION = ["idle_loop", "walk_cycle", "run_cycle",
             "jump_arc", "attack_anim", "dodge_roll",
             "hit_react", "death_anim", "spawn_anim",
             "blend_tree", "state_machine", "anim_notify",
             "reaction_anim", "intro_cinematic", "loop_anim"]
REACTION = ["flinch", "parry", "counter", "block",
            "stagger", "riposte", "guard_break", "feint",
            "evade", "absorb", "reflect", "retaliate"]
TERRAIN = ["tile_mutate", "terrain_destroy", "terrain_morph",
           "tile_raise", "tile_sink", "hazard_spawn",
           "bridge_form", "wall_raise", "pit_open",
           "platform_move", "weather_shift", "gravity_flip"]
DIMENSION = ["plane_1d", "plane_2d", "plane_2d5d",
             "plane_3d", "plane_4d", "isometric",
             "topdown", "sideview", "firstperson",
             "dimension_shift", "plane_fold", "axis_swap"]
LAYOUT = ["tile_swap", "room_shift", "corridor_gen",
          "maze_gen", "boss_arena", "hub_layout",
          "door_swap", "path_reroute", "checkpoint_move",
          "camera_pan", "screen_shake", "letterbox"]
UE = ["blueprint", "actor", "pawn", "character_movement",
      "gameplay_ability", "gas", "behavior_tree",
      "control_rig", "niagara", "mass_entity",
      "smart_object", "state_tree", "metahuman",
      "world_partition", "nanite", "lsystem"]

AXES = {"movement": MOVEMENT, "animation": ANIMATION,
        "reaction": REACTION, "terrain": TERRAIN,
        "dimension": DIMENSION, "layout": LAYOUT,
        "ue": UE}

# ---- trigger system: fire combos on reaction/env events ----
TRIGGERS = {
    "on_reaction": "any reaction animation begins",
    "on_damage": "entity takes damage",
    "on_heal": "entity recovers",
    "on_proximity": "entity enters radius of another node",
    "on_env_mutation": "terrain/layout changes",
    "on_dimension_shift": "plane/dimension changes",
    "on_animation_event": "anim notify/state transition",
    "on_combo_match": "combo phrase matches env state",
    "on_summon": "node summoned via familytree",
    "on_unlock": "charpack phrase unlocked",
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def combos2() -> list[str]:
    """Mechanic combo phrases — cross-axis joins that read like
    real mechanic names, capped per axis-pair."""
    out = []
    # movement x reaction
    for m in MOVEMENT[:8]:
        for r in REACTION[:8]:
            out.append(f"{m}_{r}")
    # animation x reaction
    for a in ANIMATION[:8]:
        for r in REACTION[:6]:
            out.append(f"{a}_{r}")
    # terrain x dimension
    for t in TERRAIN[:8]:
        for d in DIMENSION[:6]:
            out.append(f"{t}_{d}")
    # layout x ue
    for l in LAYOUT[:8]:
        for u in UE[:6]:
            out.append(f"{l}_{u}")
    # pure axis terms also count as combos
    for axis, terms in AXES.items():
        for t in terms:
            out.append(f"{axis}:{t}")
    seen, res = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            res.append(c)
    return res


def _profile(profile: str) -> Path:
    p = OUT / (profile or "default")
    p.mkdir(parents=True, exist_ok=True)
    return p


def expand(profile: str = "default",
           nodes: list[str] | None = None,
           limit_combos: int = 0) -> dict:
    """Apply mechanic combos to base nodes — profile-scoped."""
    cl = combos2()
    if limit_combos:
        cl = cl[:limit_combos]
    targets = nodes or [n for n in familytree.LABEL
                        if "__" not in n]
    path = _profile(profile) / "mechanic_combos.jsonl"
    count = 0
    with path.open("a") as f:
        for node in targets:
            for c in cl:
                vname = f"{node}__mech_{_slug(c)[:40]}"
                f.write(json.dumps(
                    {"ts": time.time(),
                     "kind": "mechanic_combo",
                     "profile": profile, "node": vname,
                     "base": node, "combo": c,
                     "derived": True},
                    ensure_ascii=False) + "\n")
                if vname not in familytree.LABEL:
                    familytree.LABEL[vname] = \
                        f"{familytree.LABEL.get(node, node)}" \
                        f" [{c}]"
                    familytree.EMOJI[vname] = \
                        familytree.EMOJI.get(
                            node.split("__")[0], "⚙️")
                    familytree.PARENT[vname] = node
                    familytree.HIDDEN[vname] = {
                        "emoji": "⚙️",
                        "label": c,
                        "note": f"mechanic:{c}"}
                count += 1
    return {"profile": profile, "nodes": len(targets),
            "combos": len(cl), "variants": count,
            "tree_nodes": len(familytree.LABEL),
            "stored": str(path)}


def assign(node: str, combo: str, trigger: str,
           profile: str = "default") -> dict:
    """Bind a combo to a trigger for a node — auto-fires on the
    reaction event when env conditions match."""
    if trigger not in TRIGGERS:
        return {"assigned": False,
                "error": "unknown trigger",
                "known": sorted(TRIGGERS)}
    rec = {"ts": time.time(), "kind": "mechanic_trigger",
           "profile": profile, "node": node,
           "combo": combo, "trigger": trigger,
           "trigger_desc": TRIGGERS[trigger],
           "derived": True}
    path = _profile(profile) / "mechanic_triggers.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"assigned": True, **rec}


def fire(trigger: str, env: dict | None = None,
         profile: str = "default") -> dict:
    """Fire a trigger: return every combo bound to it whose env
    conditions match. env keys are free-form flags — e.g.
    {dimension:'plane_2d', terrain:'tile_mutate',
     nearby:['sans'], anim:'hit_react'}."""
    env = env or {}
    path = _profile(profile) / "mechanic_triggers.jsonl"
    bindings = []
    if path.exists():
        for line in path.open():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("trigger") == trigger:
                bindings.append(r)
    # env-aware matching: combo tokens must appear in env values
    fired = []
    env_text = json.dumps(env).lower()
    for b in bindings:
        tokens = [t for t in re.split(r"[_:\s]+",
                                      b["combo"].lower()) if t]
        matched = [t for t in tokens if t in env_text]
        fired.append({**b, "env_matched": matched,
                      "env_score": len(matched)})
    fired.sort(key=lambda x: -x["env_score"])
    return {"trigger": trigger, "trigger_desc":
            TRIGGERS.get(trigger), "env": env,
            "bindings": len(bindings), "fired": fired[:20],
            "fired_count": len(fired)}


def status(profile: str = "default") -> dict:
    def count(name):
        p = _profile(profile) / name
        return sum(1 for _ in p.open()) if p.exists() else 0
    return {"profile": profile,
            "combos_available": len(combos2()),
            "axes": {k: len(v) for k, v in AXES.items()},
            "combo_records": count("mechanic_combos.jsonl"),
            "trigger_bindings": count("mechanic_triggers.jsonl"),
            "tree_nodes": len(familytree.LABEL),
            "triggers": sorted(TRIGGERS)}
