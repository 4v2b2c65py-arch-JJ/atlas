"""Silicon — unified CPU/GPU register keyed by WIDTH.

One registry for both: a device is pinned by its width
(bit width / bus width / memory width) plus vendor, arch or
slot, and port set. CPU and GPU results land in the same
table — width is the join key.

ENRICHMENT from various sources:
  - Wikidata wbsearchentities (label/description/qid)
  - Wikipedia REST summary (extract + canonical url)
  - port details scraped from the wiki extract's port/bus
    mentions (PCIe lanes, display outs, socket) — honest:
    only what's actually in the text
  - console teardown VIDEO references stored as sources
    (URLs only — we don't fetch video, we catalog it)

PIN + SCAFFOLD DRAW: pin(name) freezes a record; draw(name)
emits a scaffold spec — blocks (cores/ports/memory/
controller) with connections, the 'pinpoint details at any
time all at once' view.

REAL-TIME ANOMALY WATCH: numeric factors (width, lanes,
ports, tdp-ish fields) stream through median+MAD z-score —
same robust detector as renderlab; modifications arriving
via PORT ENTRY (PCIe lane changes, display port swaps,
running loose code edits) are recorded as change events
and re-scored immediately.

Store: data/silicon/{devices.jsonl, changes.jsonl,
       anomalies.jsonl, sources.jsonl, baseline.json,
       draws/<name>.json, saves/<name>.json}
"""

import json
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "silicon"
DEVS = OUT / "devices.jsonl"
CHANGES = OUT / "changes.jsonl"
ANOM = OUT / "anomalies.jsonl"
SOURCES = OUT / "sources.jsonl"
BASE = OUT / "baseline.json"
DRAWS = OUT / "draws"
SAVES = OUT / "saves"

Z_THRESHOLD = 2.5
PORT_RE = re.compile(
    r"(pcie|pcie\s*x?\d+|hdmi|displayport|dp|usb|sata|"
    r"nvlink|infiniband|ethernet|wifi|thunderbolt|"
    r"lga\s*\d+|am\d+|socket)\s*(x?\d+)?", re.I)

SEED = {
    "rtx_4090": {"kind": "gpu", "vendor": "nvidia",
                 "width": 384, "arch": "ada_lovelace",
                 "ports": ["hdmi", "3x_dp", "pcie_gen4_x16"],
                 "cores": 16384, "tdp_w": 450},
    "rx_7900_xtx": {"kind": "gpu", "vendor": "amd",
                    "width": 384, "arch": "rdna3",
                    "ports": ["2x_hdmi", "2x_dp",
                              "pcie_gen4_x16"],
                    "cores": 6144, "tdp_w": 355},
    "apple_m4_gpu": {"kind": "gpu", "vendor": "apple",
                     "width": 128, "arch": "apple_silicon",
                     "ports": ["thunderbolt", "usb4"],
                     "cores": 10, "tdp_w": None},
    "intel_core_i9_14900k": {"kind": "cpu",
        "vendor": "intel", "width": 64,
        "arch": "raptor_lake", "socket": "lga1700",
        "cores": 24, "threads": 32, "tdp_w": 253},
    "amd_ryzen_9_7950x": {"kind": "cpu", "vendor": "amd",
        "width": 64, "arch": "zen4", "socket": "am5",
        "cores": 16, "threads": 32, "tdp_w": 170},
    "playstation_5": {"kind": "console_soc",
        "vendor": "sony", "width": 64,
        "arch": "playstation_custom",
        "ports": ["hdmi_2_1", "usb", "ethernet",
                  "nvme_slot"],
        "cores": 8, "tdp_w": 200},
}


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _append(path: Path, rec: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def norm(name: str) -> str:
    return (name or "").lower().replace(" ", "_")


def _wiki_summary(name: str) -> dict:
    """Wikipedia REST summary — extract + canonical url."""
    q = urllib.parse.quote(name.replace("_", " "))
    url = ("https://en.wikipedia.org/api/rest_v1/"
           "page/summary/" + q)
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "ATLAS-silicon/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        extract = d.get("extract", "")
        return {"found": True,
                "title": d.get("title"),
                "extract": extract[:2000],
                "url": d.get("content_urls", {})
                .get("desktop", {}).get("page"),
                "ports_mentioned": sorted(set(
                    m.group(0).lower() for m in
                    PORT_RE.finditer(extract)))[:20]}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"found": False}
        return {"found": False, "error": str(e)[:80]}
    except Exception as e:
        return {"found": False, "error": str(e)[:100]}


def _wikidata(name: str) -> dict:
    q = urllib.parse.quote(name.replace("_", " "))
    url = ("https://www.wikidata.org/w/api.php?action="
           "wbsearchentities&search=" + q +
           "&language=en&format=json&limit=1")
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "ATLAS-silicon/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        hits = d.get("search", [])
        return hits[0] and {
            "found": True, "label": hits[0].get("label"),
            "description": hits[0].get("description"),
            "qid": hits[0].get("id")} or {"found": False}
    except Exception as e:
        return {"found": False, "error": str(e)[:100]}


def enrich(name: str) -> dict:
    """Multi-source enrichment: wikidata + wikipedia extract
    + port mentions — the 'various sources' layer."""
    return {"wikidata": _wikidata(name),
            "wikipedia": _wiki_summary(name)}


def register(name: str, kind: str = "gpu",
             width: int | None = None, vendor: str = "",
             arch: str = "", cores: int | None = None,
             ports: list | None = None, enrich_web: bool =
             True) -> dict:
    """Register a silicon device — CPU/GPU/console all land in
    the same width-keyed table."""
    name = norm(name)
    base = dict(SEED.get(name, {}))
    base.update({k: v for k, v in {
        "kind": kind, "vendor": vendor, "width": width,
        "arch": arch, "cores": cores,
        "ports": ports}.items() if v not in (None, "", [])})
    web = enrich(name) if enrich_web else {}
    wp = (web.get("wikipedia") or {})
    # port details: union of declared ports + ports the wiki
    # extract actually mentions
    merged_ports = sorted(set(
        (base.get("ports") or []) +
        (wp.get("ports_mentioned") or [])))
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "silicon_register", "name": name,
           "device_kind": base.get("kind", kind),
           "vendor": base.get("vendor"),
           "width": base.get("width"),
           "arch": base.get("arch"),
           "socket": base.get("socket"),
           "cores": base.get("cores"),
           "threads": base.get("threads"),
           "tdp_w": base.get("tdp_w"),
           "ports": merged_ports,
           "port_count": len(merged_ports),
           "source": "seed" if name in SEED else "manual",
           "wikidata": web.get("wikidata", {}),
           "wikipedia": {k: wp.get(k) for k in (
               "found", "title", "url")},
           "derived": True}
    _append(DEVS, rec)
    _score(rec)
    _update_save(name, rec)
    return rec


def pin(name: str) -> dict:
    """Pin a device — freezes it as the reference record."""
    name = norm(name)
    rec = {"ts": _device_time()["ts"],
           "kind": "silicon_pin", "name": name,
           "device_time": _device_time()}
    _append(CHANGES, rec)
    _update_save(name, {"pinned": True,
                        "pinned_ts": rec["ts"]})
    return {"kind": "silicon_pin_result", "name": name,
            "pinned": True}


def port_entry(name: str, port: str, value,
               note: str = "") -> dict:
    """Modification by port entry — PCIe lane change, display
    port swap, running loose code edits. Recorded as a change
    event and re-scored for anomalies immediately."""
    name = norm(name)
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "silicon_change", "name": name,
           "port": port, "value": value, "note": note,
           "derived": True}
    _append(CHANGES, rec)
    dev = _latest_dev(name) or {}
    factors = _factors(dev)
    factors["port:" + port] = (1 if value else 0)
    anomalies = _flag(factors)
    for a in anomalies:
        _append(ANOM, {"ts": rec["ts"],
                       "device_time": rec["device_time"],
                       "kind": "silicon_anomaly", "name": name,
                       "via": "port_entry", **a})
    _update_save(name, {"last_change": rec["ts"],
                        "anomalies": len(anomalies)})
    return {"kind": "port_entry_result", "name": name,
            "port": port, "value": value,
            "anomalies": anomalies}


def draw(name: str) -> dict:
    """Scaffold draw — the pinpoint-details-all-at-once view:
    device blocks (compute/ports/memory/io) with connections,
    emitted as a derived spec, never mutating the record."""
    name = norm(name)
    dev = _latest_dev(name)
    if not dev:
        return {"error": "device not registered", "name": name}
    blocks = []
    if dev.get("cores"):
        blocks.append({"id": "compute", "label": "cores",
                       "value": dev["cores"]})
    if dev.get("width"):
        blocks.append({"id": "width", "label": "width_bits",
                       "value": dev["width"]})
    for p in (dev.get("ports") or [])[:12]:
        blocks.append({"id": "port_" + re.sub(
            r"\W", "_", p), "label": p, "value": 1})
    if dev.get("tdp_w"):
        blocks.append({"id": "power", "label": "tdp_w",
                       "value": dev["tdp_w"]})
    edges = [{"from": "compute", "to": b["id"]}
             for b in blocks[1:]]
    spec = {"kind": "silicon_draw", "name": name,
            "device": {k: dev.get(k) for k in (
                "device_kind", "vendor", "arch", "width",
                "socket", "cores", "threads", "tdp_w")},
            "pinned": bool((_load_save(name) or {}).get(
                "pinned")),
            "blocks": blocks, "edges": edges,
            "derived": True,
            "note": ("scaffold spec — derived layout, not a "
                     "measured die shot")}
    DRAWS.mkdir(parents=True, exist_ok=True)
    dest = DRAWS / (name + ".json")
    dest.write_text(json.dumps(spec, indent=1,
                               ensure_ascii=False))
    spec["written"] = str(dest)
    return spec


def add_source(name: str, url: str, stype: str = "web",
               note: str = "") -> dict:
    """Register a data source — console teardown videos,
    spec sheets, die shots. URL catalogued honestly."""
    name = norm(name)
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "silicon_source", "name": name,
           "url": url, "source_type": stype, "note": note,
           "derived": True}
    _append(SOURCES, rec)
    return rec


def _latest_dev(name: str) -> dict | None:
    latest = None
    for r in _jsonl(DEVS) or []:
        if r.get("name") == name:
            latest = r
    return latest


def _load_save(name: str) -> dict | None:
    p = SAVES / (name + ".json")
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
    return None


def _factors(dev: dict) -> dict:
    out = {}
    for k in ("width", "cores", "threads", "tdp_w",
              "port_count"):
        v = dev.get(k)
        if isinstance(v, (int, float)):
            out[k] = float(v)
    return out


def _stats() -> dict:
    vals = {}
    for r in _jsonl(DEVS) or []:
        for k, v in _factors(r).items():
            vals.setdefault(k, []).append(v)
    out = {}
    for k, xs in vals.items():
        if len(xs) < 3:
            continue
        xs = sorted(xs)
        n = len(xs)
        med = xs[n // 2] if n % 2 else \
            (xs[n // 2 - 1] + xs[n // 2]) / 2
        dev_ = sorted(abs(x - med) for x in xs)
        mad = dev_[n // 2] if n % 2 else \
            (dev_[n // 2 - 1] + dev_[n // 2]) / 2
        out[k] = {"median": med, "mad": mad, "n": n}
    return out


def _flag(factors: dict) -> list[dict]:
    base = _stats()
    out = []
    for k, v in factors.items():
        if k.startswith("port:"):
            continue
        b = base.get(k)
        if not b:
            continue
        if b["mad"] < 1e-9:
            continue
        z = 0.6745 * (v - b["median"]) / b["mad"]
        if abs(z) > Z_THRESHOLD:
            out.append({"factor": k, "value": v,
                        "robust_z": round(z, 3),
                        "median": round(b["median"], 3)})
    return out


def _score(rec: dict) -> list[dict]:
    anomalies = _flag(_factors(rec))
    for a in anomalies:
        _append(ANOM, {"ts": rec["ts"],
                       "device_time": rec["device_time"],
                       "kind": "silicon_anomaly",
                       "name": rec["name"],
                       "via": "register", **a})
    return anomalies


def _update_baseline() -> None:
    BASE.write_text(json.dumps(
        {"kind": "silicon_baseline", "ts": time.time(),
         "stats": _stats()}, indent=1))


def _update_save(name: str, patch: dict) -> None:
    p = SAVES / (name + ".json")
    save = _load_save(name) or {"kind": "silicon_save",
                                "name": name}
    save.update(patch)
    save["derived"] = True
    SAVES.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(save, indent=1))


def status() -> dict:
    _update_baseline()
    return {"kind": "silicon_state",
            "device_time": _device_time(),
            "devices": len({r["name"] for r in
                            _jsonl(DEVS) or []}),
            "changes": sum(1 for _ in
                           _jsonl(CHANGES) or []),
            "anomalies": sum(1 for _ in
                             _jsonl(ANOM) or []),
            "sources": sum(1 for _ in
                           _jsonl(SOURCES) or []),
            "draws": len(list(DRAWS.glob("*.json")))
            if DRAWS.exists() else 0,
            "by_kind": _by_kind(),
            "note": ("cpu+gpu+console in one width-keyed "
                     "register; port_entry changes re-score "
                     "anomalies live")}


def _by_kind() -> dict:
    out = {}
    for r in _jsonl(DEVS) or []:
        k = r.get("device_kind", "?")
        out[k] = out.get(k, 0) + 1
    return out
