"""Proximity sensor — nearest/furthest character to a center node,
labeled as an encounter pair, each tagged with its timeline
variation nametag (soul state + contract variant + bug flag).

Distance basis: grimware-token Jaccard + origin match + tree
neighborhood. Soul lookup falls back parent->child (reaper_sans
inherits sans's soul record as its timeline variant).

Store: data/familytree/encounters.jsonl
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "familytree"
ENCOUNTERS = OUT / "encounters.jsonl"
SOULS = ROOT / "data" / "souls" / "souls.jsonl"

import charpack
import familytree

TIER_RANK = {"common": 0, "rare": 1, "unique": 2, "epic": 3,
             "legendary": 4, "extraordinary": 5, "ex": 6,
             "sss": 7}

# hidden node -> public kin whose soul/features it inherits
KIN = {"reaper_sans": "sans", "flowey_true": "flowey",
       "chara_remnant": "chara", "wd_gaster": "gaster",
       "muffet": "undyne"}


def _soul_map() -> dict:
    souls = {}
    if SOULS.exists():
        latest = {}
        for line in SOULS.open():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            n = r.get("name")
            if n:
                latest[n] = r  # last wins = current state
        souls = latest
    return souls


def _features(node: str) -> set:
    """Feature set for distance: grimware tokens + origin +
    tier bucket + hidden flag."""
    feats = set()
    c = charpack.CHAR_PACK.get(node)
    hid = familytree.HIDDEN.get(node)
    # hidden variants inherit their kin's feature set so they
    # don't all collapse to an identical {hidden:true} vector
    if c is None and familytree.HIDDEN.get(node):
        kin = KIN.get(node) or familytree.PARENT.get(node)
        if kin in charpack.CHAR_PACK:
            c = charpack.CHAR_PACK[kin]
    if hid and c is None:
        feats.add("hidden:true")
    if c:
        feats.add(f"origin:{c['origin']}")
        feats.add(f"tier:{c['tier']}")
        for g in c["grimware"]:
            feats.add(f"grim:{g.lower()}")
        for p in c["phrases"][:3]:
            for w in set(p.lower().split()) - {
                    "the", "a", "is", "you", "your", "to", "of",
                    "and", "in", "it", "my", "i"}:
                feats.add(f"ph:{w}")
    elif hid:
        feats.add("hidden:true")
        # inherit parent soul-name features where possible
        parent = familytree.PARENT.get(node)
        if parent and parent in charpack.CHAR_PACK:
            feats.add(f"origin:"
                      f"{charpack.CHAR_PACK[parent]['origin']}")
    if familytree.HIDDEN.get(node):
        feats.add("hidden:true")
    return feats


def distance(a: str, b: str) -> float:
    """0 = identical, 1 = nothing shared. Jaccard over features
    with a same-origin/franchise proximity bonus."""
    fa, fb = _features(a), _features(b)
    if not fa or not fb:
        return 1.0
    inter = len(fa & fb)
    union = len(fa | fb)
    jac = 1 - inter / union if union else 1.0
    # bonus: same franchise root or parent-child link
    pa, pb = familytree.PARENT.get(a), familytree.PARENT.get(b)
    if pa and pb and pa == pb:
        jac *= 0.6
    if pa == b or pb == a:
        jac *= 0.5
    return round(jac, 4)


def timeline_tag(node: str, souls: dict) -> dict:
    """Timeline variation nametag: soul state + contract variant
    + bug flag, falling back to parent soul for hidden variants."""
    rec = souls.get(node)
    inherited_from = None
    if rec is None:
        # hidden variants inherit their parent's soul timeline
        parent = familytree.PARENT.get(node)
        if parent:
            rec = souls.get(parent)
            inherited_from = parent
        if rec is None:
            # try kin map then suffix strip (reaper_sans -> sans)
            kin = KIN.get(node)
            if kin and kin in souls:
                rec = souls[kin]
                inherited_from = kin
            if rec is None:
                for suf in ("_sans", "_true", "_remnant"):
                    base = node.replace(suf, "")
                    if base != node and base in souls:
                        rec = souls[base]
                        inherited_from = base
                        break
                    break
    if rec is None:
        return {"nametag": f"{node}@variant:baseline",
                "soul_state": "unrecorded",
                "variant": "baseline", "bugged": None,
                "timeline": "unfinalized_or"}
    bugged = bool(rec.get("form_factor_persists")) and not \
        rec.get("soul_present", True)
    return {
        "nametag": (f"{node}@{rec.get('soul_state', '?')}:"
                    f"{rec.get('contract', 'seed?')}"
                    f"{'::bugged' if bugged else ''}"
                    + (f"::via:{inherited_from}"
                       if inherited_from else "")),
        "soul_state": rec.get("soul_state"),
        "variant": rec.get("contract"),
        "bugged": bugged,
        "timeline": ("alive" if rec.get("form_factor_persists")
                     else "finalized"),
        "inherited_from": inherited_from}


def encounter(center: str = "reaper_sans") -> dict:
    """Proximity encounter: nearest + furthest nodes to center,
    each labeled with its timeline variation nametag."""
    center = center.lower().replace(" ", "_")
    souls = _soul_map()
    nodes = [n for n in familytree.LABEL
             if n != center]
    dists = sorted(((distance(center, n), n) for n in nodes))
    nearest_d, nearest_n = dists[0]
    furthest_d, furthest_n = dists[-1]

    def side(node, d):
        c = charpack.CHAR_PACK.get(node, {})
        hid = familytree.HIDDEN.get(node, {})
        return {"node": node,
                "label": familytree.LABEL.get(node, node),
                "emoji": familytree.EMOJI.get(node),
                "distance": d,
                "hidden": familytree.HIDDEN.get(node),
                "tier": c.get("tier") or "hidden",
                "origin": c.get("origin") or hid.get("note",
                                                    "hidden"),
                "timeline": timeline_tag(node, souls)}

    rec = {"ts": time.time(), "kind": "proximity_encounter",
           "center": center,
           "center_tag": timeline_tag(center, souls),
           "nearest": side(nearest_n, nearest_d),
           "furthest": side(furthest_n, furthest_d),
           "derived": True}
    OUT.mkdir(parents=True, exist_ok=True)
    with ENCOUNTERS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def status() -> dict:
    n = sum(1 for _ in ENCOUNTERS.open()) \
        if ENCOUNTERS.exists() else 0
    return {"encounters": n, "metric": "grimware_jaccard",
            "nodes": len(familytree.LABEL)}
