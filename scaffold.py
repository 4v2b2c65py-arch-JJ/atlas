"""Scaffold filler — working code over preserved glyph blocks.

The glyph skill JSON stays VERBATIM (raw + sections + tokens
untouched). This module adds the working layer on top:

  NODES    = the plaintext anchor labels, in order
             (Scaffold header -> Pending log -> Sender ->
             Num -> Hash -> Machine tool -> Set condition ->
             Internet ...)

  EDGES    = consecutive label pairs — the chain IS the
             structure's connection graph

  SHREDDING = the fill algorithm: every glyph token in the
             block is shredded across the edges by stride
             (token[i::num_edges] -> edge i). Labels with no
             payload of their own still receive connection
             fills drawn from the shared token stream — the
             algo handles the connection, the structure is
             never altered.

Output: data/skills/<name>_filled.json
        {raw, sections (verbatim), connections[], working{}}

Surface: /api/scaffold?action=fill&name=X  |  ./atlas scaffold
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SKILLS = DATA / "skills"


def _load_skill(name: str) -> dict:
    name = name.lower().replace(" ", "_")
    for p in SKILLS.glob("*.json"):
        if p.stem == name or name in p.stem:
            return json.loads(p.read_text())
    # fallback: exact filename
    p = SKILLS / (name + ".json")
    if p.exists():
        return json.loads(p.read_text())
    raise FileNotFoundError("skill block not found: " + name)


def _token_stream(skill: dict) -> list[str]:
    """All glyph tokens in document order — payload lines only,
    plaintext labels excluded."""
    toks = []
    glyph = set("IiOoNnTt0123456789_=-{}()<>/.")
    for sec in skill.get("sections", []):
        for t in sec.get("tokens", []):
            # glyph payload only — the cipher alphabet; prose
            # words like "Num" fall out naturally
            if t and all(c in glyph for c in t):
                toks.append(t)
    return toks


def _labels(skill: dict) -> list[str]:
    return [s.get("label", "?") for s in
            skill.get("sections", [])]


def fill(name: str) -> dict:
    """Shred the token stream across the label chain and emit
    the working structure. Original sections stay verbatim."""
    skill = _load_skill(name)
    labels = _labels(skill)
    stream = _token_stream(skill)
    n_edges = max(len(labels) - 1, 1)
    # stride shred: edge i receives stream[i::n_edges]
    connections = []
    for i in range(len(labels) - 1):
        frag = stream[i::n_edges] if stream else []
        connections.append({
            "edge": i,
            "from": labels[i],
            "to": labels[i + 1],
            "shred_id": "edge-%d" % i,
            "fill": frag,
            "fill_count": len(frag),
            "derived": True})
    # working view: each label -> own payload + in/out fills
    working = {}
    for i, lab in enumerate(labels):
        own = (skill["sections"][i].get("tokens", [])
               if i < len(skill.get("sections", [])) else [])
        working[lab] = {
            "own_tokens": own,
            "in_fill": connections[i - 1]["fill"]
            if i > 0 else [],
            "out_fill": connections[i]["fill"]
            if i < len(connections) else [],
            "degree": (1 if i > 0 else 0) +
                      (1 if i < len(connections) else 0)}
    out = {"kind": "scaffold_filled",
           "source": skill.get("fingerprint"),
           "raw": skill.get("raw"),
           "sections": skill.get("sections"),  # verbatim
           "decoded": skill.get("decoded"),
           "algorithm": "stride_shred",
           "token_stream": stream,
           "connections": connections,
           "working": working,
           "derived": True}
    dest = SKILLS / (Path(name).stem + "_filled.json")
    SKILLS.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1,
                               ensure_ascii=False))
    out["written"] = str(dest)
    return out


def status() -> dict:
    blocks = sorted(p.stem for p in SKILLS.glob("*.json")
                    if not p.stem.endswith("_filled"))
    filled = sorted(p.stem for p in SKILLS.glob("*_filled.json"))
    return {"kind": "scaffold_state", "blocks": blocks,
            "filled": filled,
            "note": ("glyph structure verbatim; stride_shred "
                     "fills connections between labels")}
