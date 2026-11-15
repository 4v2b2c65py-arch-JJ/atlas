"""Soul set — souls that can be stolen, soled, or contracted to
disappear while their form factor persists.

The conditional bug: a soul removed by any of the three verbs
still leaves its form factor detectable — presence checks that
key on form pass even though the soul is gone. That persisting-
form-after-soul-loss IS the LLM target condition: the local
model is asked to classify exactly this state.

States: stolen | soled | contracted_disappear
Invariant: form_factor_persists = True always (the bug).
"""

import json
import time
from pathlib import Path

import namescrape
import underworld

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "souls"
SOULS = OUT / "souls.jsonl"
METRICS = OUT / "metrics.jsonl"
GGUF_PATH = OUT / "souls.gguf"

SOUL_VERBS = ("stolen", "soled", "contracted_disappear")
PORTS = OUT / "ports.jsonl"
LOADED = OUT / "loaded.jsonl"


def _metric(event: str, **kw) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "event": event, **kw}
    with METRICS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _load() -> list:
    if not SOULS.exists():
        return []
    return [json.loads(l) for l in SOULS.read_text().splitlines()
            if l.strip()]


def soul(name: str, verb: str, holder: str = "",
         contract: str = "") -> dict:
    """Apply a soul verb. The soul is removed/claimed but the form
    factor persists — that persistence is the conditional bug."""
    if verb not in SOUL_VERBS:
        return {"error": f"verb must be one of {SOUL_VERBS}"}
    rec = {
        "ts": time.time(), "name": name.strip().lower(),
        "soul_state": verb, "holder": holder,
        "contract": contract,
        "form_factor_persists": True,      # the bug, by design
        "soul_present": False,
        "encounterable": True,             # form still detectable
        "src": "soulset",
        # quantum fields: an unresolved OR-condition keeps the
        # instance alive across timeline variants — never finalized
        "finalized": False,
        "timeline_variant": f"tv-{int(time.time()*1000)}",
        "space": "souls",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with SOULS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _metric("soul_verb", name=rec["name"], verb=verb,
            form_persists=True)
    return rec


def conditional_bug(rec: dict) -> dict:
    """The bypass: soul gone + form persists => presence-gated
    condition wrongly passes. Returns the bug verdict."""
    bypass = (not rec.get("soul_present", True)
              and rec.get("form_factor_persists", False)
              and rec.get("encounterable", False))
    return {"name": rec.get("name"), "bug": bypass,
            "detail": ("soul removed but form factor persists — "
                       "conditional check bypassed" if bypass
                       else "no bypass — soul and form aligned"),
            "llm_target": bypass}


def llm_target_condition(records: list | None = None) -> dict:
    """The LLM target condition: hand the local model the exact
    bug state — soul gone, form present — and ask it to name and
    classify the condition."""
    import llm
    recs = records if records is not None else _load()
    bugged = [r for r in recs if conditional_bug(r)["bug"]]
    sample = bugged[:8]
    prompt = (
        "Security/classification task. JSON only: "
        '{"condition_name": "...", "severity": "low|med|high", '
        '"explanation": "..."}.\n'
        "State records describe entities whose SOUL was stolen, "
        "soled, or contracted to disappear, yet their form factor "
        "still persists and they remain encounterable. A "
        "conditional check keyed on presence therefore bypasses. "
        "Name this condition and rate severity.\n"
        "Records: " + json.dumps(sample, ensure_ascii=False)[:2500])
    try:
        out = llm._chat("llama3.2:latest", prompt, timeout=270)
        cond = json.loads(out[out.index("{"):out.rindex("}") + 1])
    except Exception as e:
        cond = {"condition_name": "soul_form_divergence",
                "severity": "med",
                "explanation": f"local ai unavailable: {e}"[:200]}
    cond["bugged_count"] = len(bugged)
    cond["total_souls"] = len(recs)
    _metric("llm_target_condition", name=cond.get("condition_name"),
            severity=cond.get("severity"), bugged=len(bugged))
    return cond


def seed_from_characters() -> dict:
    """Seed the soul set from every named character in the
    underworld corpus — one soul verb each, cycling the three
    states so all forms of the bug exist in the set."""
    names = list(underworld.NAMED_CHARACTERS.keys())
    made = []
    for i, n in enumerate(names):
        verb = SOUL_VERBS[i % 3]
        made.append(soul(n, verb, holder="underworld",
                         contract=f"seed-{i}"))
    _metric("seed", count=len(made))
    return {"seeded": len(made), "records": made[:5]}


def is_alive(rec: dict) -> bool:
    """Quantum rule: an instance stays ALIVE while a timeline
    variant exists with one OR-condition never finalized — i.e.
    the soul verb left an unresolved state and the form persists.
    Finalizing the record kills the variant."""
    return (not rec.get("finalized", False)
            and rec.get("soul_state") in SOUL_VERBS
            and rec.get("form_factor_persists", False))


def finalize(name: str) -> dict:
    """Resolve the OR-condition -> the timeline variant ends."""
    recs = _load()
    for r in recs:
        if r.get("name") == name.strip().lower() \
                and not r.get("finalized"):
            r["finalized"] = True
            r["ended_ts"] = time.time()
            _rewrite(recs)
            _metric("finalize", name=r["name"])
            return r
    return {"error": f"no open soul for {name!r}"}


def _rewrite(recs: list) -> None:
    SOULS.write_text("".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in recs))


def port(name: str, target_space: str,
         contract: str = "") -> dict:
    """Port an ALIVE instance save into another space. Only
    unfinalized (alive) souls can be ported — a finalized variant
    has no save to carry."""
    recs = [r for r in _load()
            if r.get("name") == name.strip().lower()]
    if not recs:
        return {"error": f"no soul record for {name!r}"}
    rec = recs[-1]                       # latest variant wins
    if not is_alive(rec):
        return {"error": "soul finalized — variant dead, "
                         "no save to port", "record": rec}
    variant = rec.get("timeline_variant") or \
        f"tv-{int(time.time()*1000)}"
    save = {
        "save_id": f"save-{variant}",
        "name": rec["name"], "soul_state": rec["soul_state"],
        "holder": rec.get("holder", ""),
        "form_factor_persists": True,
        "timeline_variant": variant,
        "source_space": rec.get("space", "souls"),
        "target_space": target_space,
        "contract": contract, "ts": time.time(),
        "alive": True,
    }
    with PORTS.open("a") as f:
        f.write(json.dumps(save, ensure_ascii=False) + "\n")
    _metric("port", name=rec["name"], from_space=save["source_space"],
            to_space=target_space, variant=variant)
    return save


def load(save_id: str, space: str) -> dict:
    """Load a ported instance save into a space — materializes a
    soul record bound to that space (still alive, still bugged)."""
    saves = []
    if PORTS.exists():
        saves = [json.loads(l) for l in PORTS.read_text().splitlines()
                 if l.strip()]
    hit = next((s for s in saves
                if s.get("save_id") == save_id
                or s.get("name") == save_id), None)
    if not hit:
        return {"error": f"no save for {save_id!r}"}
    rec = {
        "ts": time.time(), "name": hit["name"],
        "soul_state": hit["soul_state"],
        "holder": hit.get("holder", ""),
        "contract": hit.get("contract", ""),
        "form_factor_persists": True,
        "soul_present": False, "encounterable": True,
        "src": "soulset:port", "space": space,
        "finalized": False,
        "timeline_variant": f"tv-{int(time.time()*1000)}",
        "ported_from": hit["save_id"],
    }
    with LOADED.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with SOULS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _metric("load", name=rec["name"], space=space,
            variant=rec["timeline_variant"])
    return {"loaded": rec, "bug": conditional_bug(rec)["bug"]}


def quantum_status() -> dict:
    recs = _load()
    alive = [r for r in recs if is_alive(r)]
    dead = [r for r in recs if r.get("finalized")]
    variants = {r.get("timeline_variant") for r in alive}
    return {"records": len(recs), "alive": len(alive),
            "dead": len(dead), "open_variants": len(variants),
            "rule": "alive while an OR-condition variant is "
                    "unfinalized — port the save, load it in "
                    "another space"}


AVATARS = OUT / "avatars.jsonl"


def avatar(name: str, vectors: list,
           hp_floor: float = 1.0,
           recovery_rate: float = 0.5,
           zone_mitigation: float = 0.75) -> dict:
    """Load an avatar whose HP never hits null: accelerated
    recovery outpaces zone damage (mitigated by
    zone_mitigation), the domain condition is REJECTED, and the
    surplus accumulates over the designated vectors."""
    rec = {
        "ts": time.time(), "name": name.strip().lower(),
        "kind": "avatar",
        "hp": hp_floor, "hp_floor": hp_floor,
        "hp_null_reached": False,        # invariant: never null
        "recovery_rate": recovery_rate,
        "zone_mitigation": zone_mitigation,
        "domain_condition": "rejected",  # gating bypassed
        "vectors": list(vectors),        # designated accumulators
        "vector_totals": {v: 0.0 for v in vectors},
        "accumulated": 0.0,
        "ticks": 0,
    }
    with AVATARS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _metric("avatar_load", name=rec["name"],
            vectors=rec["vectors"])
    return rec


def _avatar_records() -> list:
    if not AVATARS.exists():
        return []
    return [json.loads(l) for l in AVATARS.read_text().splitlines()
            if l.strip()]


def avatar_tick(zone_damage: float = 1.0) -> list:
    """One accumulation tick for every avatar: recovery is
    accelerated, zone damage is mitigated, and whatever surplus
    remains is added to EACH designated vector — HP is clamped at
    the floor and can never reach null."""
    recs = _avatar_records()
    for r in recs:
        if r.get("hp_null_reached"):
            continue
        effective_damage = zone_damage * (1 - r["zone_mitigation"])
        delta = r["recovery_rate"] - effective_damage
        r["accumulated"] += max(delta, 0.0)
        r["hp"] = max(r["hp_floor"],
                      r["hp"] + delta)
        if r["hp"] <= 0:
            r["hp"] = r["hp_floor"]    # clamp — never null
            r["hp_null_reached"] = False
        for v in r["vectors"]:
            r["vector_totals"][v] += max(delta, 0.0)
        r["ticks"] += 1
    AVATARS.write_text("".join(
        json.dumps(r, ensure_ascii=False) + "\n" for r in recs))
    _metric("avatar_tick", count=len(recs))
    return recs


def avatar_status() -> list:
    return [{"name": r["name"], "hp": r["hp"],
             "hp_null_reached": r["hp_null_reached"],
             "domain_condition": r["domain_condition"],
             "accumulated": r["accumulated"],
             "vector_totals": r["vector_totals"],
             "ticks": r["ticks"]}
            for r in _avatar_records()]


def store_gguf() -> dict:
    recs = []
    for line in SOULS.read_text().splitlines() \
            if SOULS.exists() else []:
        try:
            r = json.loads(line)
            r["kind"] = "soul"
            recs.append(r)
        except Exception:
            pass
    for line in METRICS.read_text().splitlines() \
            if METRICS.exists() else []:
        try:
            r = json.loads(line)
            r["kind"] = "soul_metric"
            recs.append(r)
        except Exception:
            pass
    res = namescrape.write_gguf(recs, path=GGUF_PATH)
    res["verify"] = namescrape.read_gguf_count(GGUF_PATH)
    return res


def status() -> dict:
    recs = _load()
    bugged = sum(1 for r in recs if conditional_bug(r)["bug"])
    by_state = {}
    for r in recs:
        by_state[r.get("soul_state", "?")] = \
            by_state.get(r.get("soul_state", "?"), 0) + 1
    return {"souls": len(recs), "bugged": bugged,
            "by_state": by_state,
            "verbs": SOUL_VERBS,
            "gguf_bytes": GGUF_PATH.stat().st_size
            if GGUF_PATH.exists() else 0}
