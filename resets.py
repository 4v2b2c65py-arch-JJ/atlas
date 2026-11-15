"""Reset ledger — record player resets: the moment time rewinds
to another reality or hops. Each reset records:

  - exactly when the occurrence happened (ts)
  - where it went: distribution of the value delta — added or
    subtracted from sums (sub_max/reality_value); MIN OUTPUT IS
    CONSTANT — the stalemate of death and rewinds, it never
    moves
  - minimal diffs: |delta| below epsilon counts as a 'tiny
    branch' — the count of minimal branches being formed

Store: data/resets/resets.jsonl + reset_state.json
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "resets"
RESETS = OUT / "resets.jsonl"
STATE = OUT / "reset_state.json"

MIN_EPS = 1e-6
TINY_BRANCH_EPS = 0.5  # |delta| below this = minimal branch


def _latest(path: Path, key: str) -> dict | None:
    if not path.exists():
        return None
    latest = None
    for line in path.open():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get(key):
            latest = r
    return latest


def _node_values(node: str, profile: str = "default") -> dict:
    """Sums for a node IN A GIVEN REALITY (profile): min
    (constant surface), max (profile-dependent stack depth),
    reality value. Different profiles = different realities
    with different combo/mechanic stores."""
    import stack as _st
    try:
        full = _st.build_stack(node, profile)
        max_out = _st._power(full)
    except Exception:
        max_out = 0.0
    tl = None
    lp = DATA / "stack" / "tier_layers.jsonl"
    if lp.exists():
        for line in lp.open():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("node") == node and \
                    r.get("profile", "default") == profile:
                tl = r  # last wins
    gn = _latest(DATA / "genesis" / "reality_set.jsonl", "node")
    # reality_set is append-only per observe; find this node's
    _rp = DATA / "genesis" / "reality_set.jsonl"
    if _rp.exists():
        for line in _rp.open():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("node") == node:
                gn = r
    # min_output is PROFILE-INVARIANT — the stalemate anchor:
    # computed from the node's surface (truename) only, identical
    # in every reality. max_output is the profile-dependent
    # stack power computed above.
    import truename as _tn
    import proximity as _px
    souls = _px._soul_map()
    tag = _px.timeline_tag(node, souls)
    tn_rec = None
    min_out = None
    _tp = DATA / "truename" / "names.jsonl"
    if _tp.exists():
        for line in _tp.open():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("node") == node:
                tn_rec = r
    if tn_rec:
        import charpack as _cp
        tier = _cp.CHAR_PACK.get(
            node.split("__")[0], {}).get("tier") or (
            "hidden" if __import__("familytree").HIDDEN.get(
                node) else "common")
        min_out = round(
            ((tn_rec.get("proximity") or {}).get(
                "save_unlock_amount", 0)) * 0.4 +
            (tn_rec.get("timeline_number") or 0) * 2 +
            _st.TIER_RANK.get(tier, 0) * 10 +
            (3 if __import__("familytree").HIDDEN.get(node)
             else 0) +
            (5 if tn_rec.get("bugged") else 0), 1)
    rv = (gn or {}).get("reality_value")
    return {"min_output": min_out, "max_output": max_out,
            "reality_value": rv if rv is not None else 0.0}


def record_reset(node: str, to_reality: str,
                 from_reality: str | None = None,
                 profile: str = "default",
                 cause: str = "player_reset") -> dict:
    """Record a reset/rewind event. Distribution: the delta
    between realities is added to or subtracted from the node's
    max/reality sums; min_output is CONSTANT — recorded as the
    stalemate anchor. Minimal diffs count as tiny branches."""
    node = node.lower().replace(" ", "_")
    vals = _node_values(node, profile)
    # destination reality = target profile's own sums — that's
    # where the rewind lands and what gets added/subtracted
    to_vals = _node_values(node, to_reality)
    d_max = round((to_vals["max_output"] or 0) -
                  (vals["max_output"] or 0), 4)
    d_rv = round(to_vals["reality_value"] -
                 vals["reality_value"], 4)
    d_min = round((to_vals["min_output"] or 0) -
                  (vals["min_output"] or 0), 4)
    min_constant = abs(d_min) < MIN_EPS
    tiny = abs(d_max) < TINY_BRANCH_EPS and \
        abs(d_rv) < TINY_BRANCH_EPS
    rec = {"ts": time.time(), "kind": "reset",
           "node": node, "profile": profile,
           "from_reality": from_reality or "current",
           "to_reality": to_reality, "cause": cause,
           "distribution": {
               "min_output": {"delta": d_min,
                              "constant": min_constant,
                              "note": "stalemate of death "
                                      "and rewinds"},
               "max_output": {"delta": d_max,
                              "direction":
                                  "added" if d_max > 0 else
                                  "subtracted" if d_max < 0
                                  else "neutral"},
               "reality_value": {"delta": d_rv,
                                 "direction":
                                     "added" if d_rv > 0
                                     else "subtracted"
                                     if d_rv < 0 else
                                     "neutral"}},
           "tiny_branch": tiny,
           "values": vals, "derived": True}
    OUT.mkdir(parents=True, exist_ok=True)
    with RESETS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _update_state()
    return rec


def _update_state() -> dict:
    recs = []
    if RESETS.exists():
        for line in RESETS.open():
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    tiny = sum(1 for r in recs if r.get("tiny_branch"))
    added = sum(1 for r in recs
                if (r.get("distribution", {})
                    .get("max_output", {})
                    .get("direction") == "added"))
    subtracted = sum(1 for r in recs
                     if (r.get("distribution", {})
                         .get("max_output", {})
                         .get("direction") == "subtracted"))
    mins_constant = all(
        (r.get("distribution", {}).get("min_output", {})
         .get("constant")) for r in recs) if recs else True
    state = {"ts": time.time(), "kind": "reset_state",
             "resets": len(recs), "tiny_branches": tiny,
             "added": added, "subtracted": subtracted,
             "neutral": len(recs) - added - subtracted,
             "min_output_constant": mins_constant,
             "note": ("mins are constant — stalemate of "
                      "death and rewinds; minimal diffs = "
                      "tiny branches forming")}
    STATE.write_text(json.dumps(state, indent=1))
    return state


def status() -> dict:
    return _update_state() if STATE.exists() else {
        "resets": 0, "tiny_branches": 0}
