"""Spectrum graph + material compositor — run datasets through a
spectral classifier, label each record by element/table
composition and percentage, color-code by element, store
oldest->newest, and repeat on a timer.

Data:
  periodic table JSON (Bowserinator/Periodic-Table-JSON, cached)
  characteristic emission-line table for common elements
  (approximate — labeled, not a lab instrument)
  derived spectra for the rest from atomic number (pseudo-lines,
  clearly marked)

Stores:
  data/spectrum/periodic_table.json   cached table
  data/spectrum/records.jsonl         labeled composites
  data/spectrum/metrics.jsonl         repeater metrics
  data/spectrum/spectrum.gguf         packed records
"""

import hashlib
import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "spectrum"
TABLE_PATH = OUT / "periodic_table.json"
RECORDS = OUT / "records.jsonl"
METRICS = OUT / "metrics.jsonl"
GGUF = OUT / "spectrum.gguf"
UA = {"User-Agent": "Mozilla/5.0"}

TABLE_URL = ("https://raw.githubusercontent.com/Bowserinator/"
             "Periodic-Table-JSON/master/PeriodicTableJSON.json")

# characteristic strong emission lines (nm) for common elements —
# approximate textbook values, labeled as such
EMISSION_LINES = {
    "H": [656.3, 486.1, 434.0, 410.2],
    "He": [587.6, 447.1, 402.6, 388.9],
    "Li": [670.8, 610.4],
    "Na": [589.0, 568.8, 615.4],
    "Mg": [518.4, 383.2],
    "Al": [396.2, 394.4, 669.6],
    "Si": [288.2, 251.6],
    "K": [766.5, 404.4, 393.4],
    "Ca": [422.7, 396.8, 430.8, 616.2],
    "Sc": [361.4, 363.0],
    "Ti": [498.2, 334.9],
    "V": [437.9, 318.5],
    "Cr": [520.8, 428.9],
    "Mn": [403.1, 475.4],
    "Fe": [438.4, 440.5, 372.0, 358.1],
    "Co": [345.4, 352.0],
    "Ni": [341.5, 346.2],
    "Cu": [324.8, 327.4, 510.6, 515.3],
    "Zn": [307.2, 213.9],
    "Ga": [403.3, 417.2],
    "Ge": [265.1, 303.9],
    "As": [228.8, 234.0],
    "Se": [196.1, 203.9],
    "Br": [588.0, 819.0],
    "Kr": [557.0, 587.1],
    "Rb": [780.0, 794.8],
    "Sr": [460.7, 407.8],
    "Y": [412.3, 430.0],
    "Zr": [468.8, 477.2],
    "Nb": [405.9, 407.5],
    "Mo": [379.8, 386.4],
    "Ru": [349.9, 372.8],
    "Rh": [369.0, 343.5],
    "Pd": [340.5, 342.1],
    "Ag": [328.1, 338.3],
    "Cd": [228.8, 361.1],
    "In": [451.1, 410.2],
    "Sn": [303.4, 317.5],
    "Sb": [252.9, 217.6],
    "Te": [238.6, 276.8],
    "I": [546.1, 589.5],
    "Xe": [462.4, 597.6],
    "Cs": [852.1, 894.3],
    "Ba": [553.6, 577.8],
    "La": [580.5, 639.0],
    "Ce": [428.0, 462.0],
    "Pr": [440.9, 526.0],
    "Nd": [406.1, 410.9],
    "Sm": [428.0, 443.9],
    "Eu": [462.7, 420.5],
    "Gd": [418.4, 354.0],
    "Tb": [431.9, 406.0],
    "Dy": [404.3, 421.5],
    "Ho": [416.3, 389.0],
    "Er": [400.8, 415.1],
    "Tm": [409.4, 372.0],
    "Yb": [398.8, 555.6],
    "Lu": [521.9, 547.7],
    "Hf": [409.3, 368.2],
    "Ta": [265.3, 301.6],
    "W": [400.9, 407.4],
    "Re": [346.0, 488.9],
    "Os": [330.2, 305.9],
    "Ir": [351.0, 322.1],
    "Pt": [265.9, 306.5],
    "Au": [267.6, 242.8],
    "Hg": [253.7, 404.7, 435.8],
    "Tl": [535.0, 377.6],
    "Pb": [283.3, 405.8],
    "Bi": [306.8, 472.9],
    "Th": [283.2, 401.9],
    "U": [424.4, 439.4, 437.1],
}

# category -> color coding for graph nodes
CATEGORY_COLOR = {
    "diatomic nonmetal": "#7ec8e3", "polyatomic nonmetal": "#58b368",
    "noble gas": "#b19cd9", "alkali metal": "#ff6b6b",
    "alkaline earth metal": "#ffa94d", "metalloid": "#ffd43b",
    "post-transition metal": "#74c0fc", "transition metal": "#4dabf7",
    "lanthanide": "#da77f2", "actinide": "#e599f7", "unknown": "#adb5bd",
}
# emission hue per element where known
ELEMENT_HUE = {
    "H": "#ff3b30", "He": "#ffd60a", "Na": "#ffcc00",
    "Ca": "#ff9f0a", "Fe": "#ff6961", "Cu": "#30d158",
    "Ag": "#c7c7cc", "Au": "#ffd700", "Hg": "#9b59b6",
    "U": "#39ff88", "Th": "#00ffcc", "Pb": "#5c7cfa",
}


def load_table() -> list[dict]:
    """Periodic table — cached, fetched once."""
    OUT.mkdir(parents=True, exist_ok=True)
    if TABLE_PATH.exists():
        return json.loads(TABLE_PATH.read_text())
    req = urllib.request.Request(TABLE_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        els = json.loads(r.read())["elements"]
    TABLE_PATH.write_text(json.dumps(els))
    return els


def _pseudo_lines(z: int, sym: str) -> list[float]:
    """Deterministic pseudo-spectrum for elements lacking a
    textbook table — derived from atomic number, labeled."""
    h = hashlib.sha256(sym.encode()).hexdigest()
    base = 200 + (z % 500)
    lines = []
    for i in range(4):
        n = int(h[i * 4:i * 4 + 4], 16)
        lines.append(round(base + (n % 600), 1))
    return lines


def element_spectrum(sym: str) -> dict:
    """Emission spectrum for an element: real table lines where
    known, pseudo-lines otherwise."""
    els = load_table()
    z = next((e["number"] for e in els
              if e["symbol"] == sym), None)
    if z is None:
        return {"symbol": sym, "lines": [], "approx": True}
    real = sym in EMISSION_LINES
    return {"symbol": sym, "z": z,
            "lines": (EMISSION_LINES[sym] if real
                      else _pseudo_lines(z, sym)),
            "approx": not real}


def element_color(sym: str, category: str = "") -> str:
    """Color code: emission hue where known, else category."""
    return ELEMENT_HUE.get(sym) or CATEGORY_COLOR.get(
        category, CATEGORY_COLOR["unknown"])


def _weight(text: str, sym: str, name: str) -> float:
    """Keyword weight for element presence in a text blob."""
    t = text.lower()
    w = 0.0
    s = sym.lower()
    if s in t.split() or f" {s} " in f" {t} ":
        w += 2.0
    if name.lower() in t:
        w += 3.0
    # symbol as substring only if len>=2 and word-ish
    if len(s) >= 2 and re_word(s, t):
        w += 1.0
    return w


def re_word(needle: str, hay: str) -> bool:
    import re
    return bool(re.search(rf"(?<![a-z]){re.escape(needle)}"
                          r"(?![a-z])", hay))


def composite_analysis(text: str) -> dict:
    """Analyze a text record -> composite element breakdown with
    percentages + colors. This is the 'what is it made of' answer."""
    text = str(text)
    els = load_table()
    scores = {}
    for e in els:
        w = _weight(text, e["symbol"], e["name"])
        if w > 0:
            scores[e["symbol"]] = w
    total = sum(scores.values()) or 1.0
    comps = []
    for sym, w in sorted(scores.items(),
                         key=lambda kv: -kv[1])[:12]:
        e = next(x for x in els if x["symbol"] == sym)
        comps.append({
            "element": sym, "name": e["name"],
            "z": e["number"], "period": e["period"],
            "group": e["group"],
            "category": e["category"],
            "percent": round(w / total * 100, 1),
            "color": element_color(sym, e["category"]),
            "spectrum": element_spectrum(sym)})
    return {"composite": True, "elements": comps,
            "element_count": len(comps),
            "method": "keyword_weight_normalized",
            "caveat": "text-derived estimate, not assay"}


def dataset_records(path: Path) -> list[dict]:
    """Load a JSONL dataset, sort oldest->newest by ts/date."""
    recs = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    def key(r):
        for k in ("date", "date_created", "ts", "timestamp",
                  "created_at"):
            if r.get(k):
                return str(r[k])
        return "9999"
    return sorted(recs, key=key)


def run_dataset(path: Path, label: str = "") -> dict:
    """Run a dataset through the spectrum graph: label each record
    oldest->newest, store composites, emit graph."""
    recs = dataset_records(path)
    OUT.mkdir(parents=True, exist_ok=True)
    stored, graph = [], {"nodes": [], "edges": []}
    for i, r in enumerate(recs):
        text = json.dumps(r, ensure_ascii=False)
        comp = composite_analysis(text)
        ts = (r.get("ts") or r.get("date_created")
              or r.get("date") or "")
        node_id = f"{label or path.stem}:{i}:{ts}"
        entry = {"seq": i, "ts": ts, "label": label or path.stem,
                 "node": node_id,
                 "query": r.get("query") or r.get("title")
                 or r.get("dn") or "",
                 "elements": comp["elements"][:6],
                 "primary": (comp["elements"][0]["element"]
                             if comp["elements"] else None),
                 "method": comp["method"],
                 "caveat": comp["caveat"]}
        graph["nodes"].append({"id": node_id, "ts": ts,
                               "label": entry["query"][:60]})
        for c in comp["elements"][:6]:
            graph["edges"].append({
                "from": node_id, "to": f"el:{c['element']}",
                "percent": c["percent"],
                "color": c["color"]})
        stored.append(entry)
        with RECORDS.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"dataset": str(path), "records": len(stored),
            "stored": str(RECORDS), "graph": graph}


def repeater(interval_s: float = 60, cycles: int = 0,
             datasets: list[Path] | None = None) -> dict:
    """Repeater: re-run datasets on an interval, oldest->newest,
    accumulate metrics. cycles=0 runs forever (bounded by caller)."""
    datasets = datasets or [ROOT / "data" / "spatial"
                            / "predictions.jsonl"]
    datasets = [p for p in datasets if p.exists()]
    runs = 0
    while True:
        t0 = time.time()
        results = []
        for p in datasets:
            r = run_dataset(p)
            results.append({"dataset": r["dataset"],
                            "records": r["records"]})
        metric = {"ts": time.time(), "run": runs,
                  "elapsed_s": round(time.time() - t0, 2),
                  "datasets": results}
        with METRICS.open("a") as f:
            f.write(json.dumps(metric) + "\n")
        runs += 1
        if cycles and runs >= cycles:
            break
        time.sleep(interval_s)
    return {"runs": runs, "metrics": str(METRICS)}


def pack_gguf() -> dict:
    """Pack spectrum records into GGUF using the namescrape
    writer conventions."""
    import namescrape
    recs = []
    if RECORDS.exists():
        for line in RECORDS.open():
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    if TABLE_PATH.exists():
        recs.append({"kind": "periodic_table",
                     "elements": len(load_table())})
    path = namescrape.write_gguf(recs, path=GGUF)
    return {"gguf": str(path), "records": len(recs)}


def status() -> dict:
    def count(p):
        return sum(1 for _ in p.open()) if p.exists() else 0
    return {"records": count(RECORDS), "metrics": count(METRICS),
            "table_cached": TABLE_PATH.exists(),
            "elements": len(load_table()) if TABLE_PATH.exists()
            else "not loaded",
            "emission_table": len(EMISSION_LINES)}
