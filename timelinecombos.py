"""Timeline-combination expansion — generate word-combo phrases
(extreme pacifist, mild pacifist, fast pacifist, ...) and apply
every combo as a variant to every character/NPC node, scoped per
profile. Maximum timeline expansion: combos x nodes.

Axes:
  intensity  — extreme, mild, fast, hard, true, partial,
               absolute, supreme, hybrid, unstable, secret,
               corrupted
  alignment  — pacifist, genocide, neutral, lawful, chaotic,
               balanced, hidden, corrupted

Store: data/profiles/<profile>/timeline_combos.jsonl
Nodes: registered into familytree as <node>__<combo> hidden
variants so truename/proximity/branches pick them up.
"""

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "profiles"

import familytree

INTENSITY = ["extreme", "mild", "fast", "hard", "true",
             "partial", "absolute", "supreme", "hybrid",
             "unstable", "secret", "corrupted"]
ALIGNMENT = ["pacifist", "genocide", "neutral", "lawful",
             "chaotic", "balanced", "hidden", "corrupted"]

# hand-picked combos that don't fit the grid
EXTRA = ["extreme pacifist", "mild pacifist", "fast pacifist",
         "true pacifist", "partial genocide", "secret route",
         "corrupted save", "unstable timeline",
         "hybrid soul", "absolute pacifist"]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def combos() -> list[str]:
    """All timeline-combination phrases."""
    out = list(EXTRA)
    for i in INTENSITY:
        for a in ALIGNMENT:
            p = f"{i} {a}"
            if p not in out:
                out.append(p)
    # dedupe preserve order
    seen, res = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            res.append(c)
    return res


def _profile_path(profile: str) -> Path:
    p = OUT / profile
    p.mkdir(parents=True, exist_ok=True)
    return p / "timeline_combos.jsonl"


def expand(profile: str = "default",
           nodes: list[str] | None = None,
           combo_list: list[str] | None = None) -> dict:
    """Apply every combo to every (base) node — the maximum
    timeline expansion. Records are profile-scoped."""
    profile = profile or "default"
    combos_l = combo_list or combos()
    # base nodes only (no __) — variants of variants would
    # explode combinatorially without bound
    targets = nodes or [n for n in familytree.LABEL
                        if "__" not in n]
    path = _profile_path(profile)
    count = 0
    with path.open("a") as f:
        for node in targets:
            for c in combos_l:
                vname = f"{node}__{_slug(c)}"
                rec = {"ts": time.time(),
                       "kind": "timeline_combo",
                       "profile": profile,
                       "node": vname, "base": node,
                       "combo": c, "derived": True}
                f.write(json.dumps(rec, ensure_ascii=False)
                        + "\n")
                # register node once (idempotent)
                if vname not in familytree.LABEL:
                    familytree.LABEL[vname] = \
                        f"{familytree.LABEL.get(node, node)}" \
                        f" [{c}]"
                    familytree.EMOJI[vname] = \
                        familytree.EMOJI.get(
                            node.split("__")[0], "🌀")
                    familytree.PARENT[vname] = node
                    familytree.HIDDEN[vname] = {
                        "emoji": familytree.EMOJI.get(
                            node.split("__")[0], "🌀"),
                        "label": f"{c} timeline",
                        "note": f"combo:{c} profile:{profile}"}
                count += 1
    return {"profile": profile, "nodes": len(targets),
            "combos": len(combos_l), "variants": count,
            "total_nodes": len(familytree.LABEL),
            "stored": str(path)}


def status(profile: str = "default") -> dict:
    path = _profile_path(profile)
    n = sum(1 for _ in path.open()) if path.exists() else 0
    return {"profile": profile, "records": n,
            "combos_available": len(combos()),
            "tree_nodes": len(familytree.LABEL)}
