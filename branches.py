"""Branch variations — store how each true-name node's assignment
varies across timeline branches.

t# (timeline_number) + save_unlock_amount are keyed to the main
node (t-head = encounter center). Each node's name assignment is
recorded per branch: which franchise branch it lives on, its
index within that branch, and the branch count — so the variant
id captures 'the number of branches' the name varies across.

Store: data/truename/branches.jsonl
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "truename"
BRANCHES = OUT / "branches.jsonl"

import familytree
import proximity
import truename


def branch_root(node: str) -> str:
    """Top-level branch a node belongs to (franchise root)."""
    cur = node
    seen = set()
    while cur in familytree.PARENT and cur not in seen:
        seen.add(cur)
        cur = familytree.PARENT[cur]
    return cur if cur in familytree.TREE else "root"


def branch_map(center: str = "") -> dict:
    """For every node: branch, index-in-branch, branch-size,
    variation id, timeline number, save amount. The variation id
    is what varies as branches grow."""
    center = center or truename.CENTER
    souls = proximity._soul_map()
    # latest truename records per node
    latest = {}
    if truename.NAMES.exists():
        for line in truename.NAMES.open():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("node"):
                latest[r["node"]] = r
    sizes = {b: len(kids) for b, kids in familytree.TREE.items()}
    recs = []
    for node in sorted(familytree.LABEL):
        br = branch_root(node)
        siblings = familytree.TREE.get(br, [node])
        idx = siblings.index(node) if node in siblings else -1
        t = latest.get(node, {})
        tag = proximity.timeline_tag(node, souls)
        tnum = t.get("timeline_number",
                     truename._tail_number(tag["nametag"],
                                           tag.get("soul_state")))
        d = proximity.distance(center, node)
        save_amt = int(round((1 - d) * truename.SAVE_SCALE)) \
            if node != center else truename.SAVE_SCALE
        # variation id: branch + slot + branch count — this is
        # the part that changes when branches are added/removed
        var_id = (f"{br}:{idx if idx >= 0 else '?'}"
                  f"of{len(siblings)}")
        entry = {
            "ts": time.time(), "kind": "branch_variation",
            "node": node,
            "true_name": t.get("true_name") or
            truename._true_name(node, tag),
            "branch": br, "branch_index": idx,
            "branch_size": len(siblings),
            "variation_id": var_id,
            "timeline_number": tnum,
            "save_unlock_amount": save_amt,
            "proximity_distance": d,
            "derived": True}
        recs.append(entry)
    OUT.mkdir(parents=True, exist_ok=True)
    with BRANCHES.open("a") as f:
        for e in recs:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return {"center": center, "nodes": len(recs),
            "branches": {b: len(k) for b, k in
                         familytree.TREE.items()},
            "stored": str(BRANCHES)}


def status() -> dict:
    n = sum(1 for _ in BRANCHES.open()) \
        if BRANCHES.exists() else 0
    return {"records": n,
            "branches": {b: len(k) for b, k in
                         familytree.TREE.items()}}
