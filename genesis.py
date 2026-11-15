"""Genesis layer — max_output >= 278 is a 'new world
observation'. Each genesis candidate is observed through
physical metrics (light-ns travel, Planck time/length) across
await ticks until the accumulated value formalizes into a
scopable entry in the true reality set. Sub-min/sub-max outputs
are summed into the observation bounds.

Metrics are derived analogies — labeled, not instrument data.

Store: data/genesis/observations.jsonl
       data/genesis/reality_set.jsonl
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "genesis"
OBS = OUT / "observations.jsonl"
REALITY = OUT / "reality_set.jsonl"

import tierlayers

GENESIS_THRESHOLD = 278.0
C_M_PER_NS = 0.299792458      # light travel per nanosecond
T_PLANCK = 5.391247e-44       # seconds
L_PLANCK = 1.616255e-35       # meters
MAX_TICKS = 1000
CONVERGENCE_EPS = 1e-6


def _metrics(power_excess: float) -> dict:
    """Convert power excess over threshold into physical-unit
    analogies: light-ns distance, Planck time, Planck length."""
    return {
        "excess": round(power_excess, 4),
        "light_ns": power_excess,
        "light_distance_m": round(power_excess * C_M_PER_NS,
                                  6),
        "planck_time_s": power_excess * T_PLANCK,
        "planck_length_m": power_excess * L_PLANCK,
        "note": "derived analogies, not instrument data"}


def observe(node: str, profile: str = "default",
            ticks: int = 100) -> dict:
    """Observe a node: if max_output >= 278 it enters genesis.
    Awaits `ticks` observation rounds, summing sub-min/sub-max
    bounds each round until the value formalizes (converges) or
    ticks run out. Records every tick + the formalized entry."""
    rng = tierlayers.output_range(node, profile)
    mx = rng["max_output"]["power"]
    mn = rng["min_output"]["power"]
    is_genesis = mx >= GENESIS_THRESHOLD
    excess = max(0.0, mx - GENESIS_THRESHOLD)
    OUT.mkdir(parents=True, exist_ok=True)

    sub_min_sum = 0.0
    sub_max_sum = 0.0
    reality_value = 0.0
    formalized = False
    tick_records = []
    if not is_genesis:
        # below threshold: single observation, no await loop —
        # nothing to formalize
        tick = {"ts": time.time(), "kind": "genesis_tick",
                "node": node, "profile": profile, "tick": 0,
                "genesis": False, "sub_min_output": mn,
                "sub_max_output": mx,
                "metrics": _metrics(0.0), "derived": True}
        with OBS.open("a") as f:
            f.write(json.dumps(tick, ensure_ascii=False)
                    + "\n")
        tick_records.append(tick)
    prev = None
    for t in range(min(ticks, MAX_TICKS) if is_genesis else 0):
        # each tick: metrics scale with accumulated excess;
        # sub-bounds accumulate the min/max output surface
        m = _metrics(excess * (t + 1) / max(ticks, 1))
        sub_min_sum += mn
        sub_max_sum += mx
        reality_value += (m["light_distance_m"] +
                          m["planck_length_m"] * 1e35)
        tick = {"ts": time.time(), "kind": "genesis_tick",
                "node": node, "profile": profile, "tick": t,
                "genesis": is_genesis,
                "sub_min_output": mn, "sub_max_output": mx,
                "metrics": m, "derived": True}
        tick_records.append(tick)
        with OBS.open("a") as f:
            f.write(json.dumps(tick, ensure_ascii=False)
                    + "\n")
        # convergence: value delta below epsilon -> formalized
        if prev is not None and abs(reality_value - prev) < \
                CONVERGENCE_EPS and is_genesis:
            formalized = True
            break
        prev = reality_value

    rec = {"ts": time.time(), "kind": "genesis",
           "node": node, "profile": profile,
           "genesis": is_genesis,
           "threshold": GENESIS_THRESHOLD,
           "max_output": mx, "min_output": mn,
           "output_span": rng["output_span"],
           "sub_min_sum": round(sub_min_sum, 4),
           "sub_max_sum": round(sub_max_sum, 4),
           "reality_value": reality_value,
           "ticks": len(tick_records),
           "formalized": formalized,
           "scopable": is_genesis and formalized,
           "metrics": _metrics(excess),
           "derived": True}
    if is_genesis:
        with REALITY.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def reality_set() -> list[dict]:
    """The true reality set — all formalized genesis values."""
    return [r for r in _load(REALITY) if r.get("scopable")]


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.open():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def status() -> dict:
    return {"threshold": GENESIS_THRESHOLD,
            "observations": len(_load(OBS)),
            "reality_set": len(reality_set()),
            "metrics": ["light_ns", "light_distance_m",
                        "planck_time_s", "planck_length_m"]}
