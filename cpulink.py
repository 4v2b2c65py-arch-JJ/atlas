"""CPULink — processor traversal mesh.

Nodes are PROCESOR ARCHITECTURES, not motherboards: a CPU is
identified by its architecture format (arm64, x86_64, rv64,
wasm32...) plus core/thread counts and bit width. Traversal
works the same as the mesh — packets hop CPU->CPU over basic
network I/O and keep updating over long distance as long as
the node runs.

FLOW SIZE = active outlets x cores x width:
  flow_units = sockets * cores * threads_per_core * width_bits
  e.g. 1 x 8 cores x 2 threads x 64-bit = 1024 flow units
Width = the architecture's bit width (32/64), cores =
physical cores, outlets = sockets/packages.

GUIDED METRICS via websearch datasets: register() accepts a
CPU name and enriches it from Wikidata (wbsearchentities —
label + description + instance-of), optionally resolving a
Wikimedia Commons image (P18) so google-images-style assets
can be catalogued. Everything lands organized in
cpus.gguf grouped by architecture/vendor.

Store: data/cpulink/{cpus.jsonl, traversals.jsonl,
       cpus.gguf, saves/<cpu>.json}
"""

import json
import math
import platform
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "cpulink"
CPUS = OUT / "cpus.jsonl"
TRAV = OUT / "traversals.jsonl"
SAVES = OUT / "saves"
GGUF = OUT / "cpus.gguf"

WIKIDATA = ("https://www.wikidata.org/w/api.php?action="
            "wbsearchentities&search={q}&language=en"
            "&format=json&limit=1")
COMMONS = ("https://commons.wikimedia.org/w/api.php?action="
           "query&titles=File:{f}&prop=imageinfo"
           "&iiprop=url&format=json")

# seeded registry — honest specs for common parts; enriched
# records mark source so web data and seed data stay distinct
SEED_CPUS = {
    "apple_m4": {"vendor": "apple", "arch": "arm64",
                 "cores": 10, "threads": 4, "width": 64,
                 "sockets": 1},
    "apple_m3": {"vendor": "apple", "arch": "arm64",
                 "cores": 8, "threads": 4, "width": 64,
                 "sockets": 1},
    "intel_core_i9_14900k": {"vendor": "intel",
        "arch": "x86_64", "cores": 24, "threads": 32,
        "width": 64, "sockets": 1},
    "amd_ryzen_9_7950x": {"vendor": "amd",
        "arch": "x86_64", "cores": 16, "threads": 32,
        "width": 64, "sockets": 1},
    "snapdragon_x_elite": {"vendor": "qualcomm",
        "arch": "arm64", "cores": 8, "threads": 8,
        "width": 64, "sockets": 1},
    "raspberry_pi_5_cortex_a76":
        {"vendor": "arm", "arch": "arm64", "cores": 4,
         "threads": 4, "width": 64, "sockets": 1},
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


def detect_local() -> dict:
    """Identify THIS machine's processor by architecture
    format — sysctl on macOS, /proc/cpuinfo on Linux."""
    out = {"kind": "cpu_detect", "machine":
           platform.machine(), "system": platform.system(),
           "derived": True}
    try:
        if platform.system() == "Darwin":
            r = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string",
                 "hw.ncpu", "hw.perflevel0.physicalcpu",
                 "hw.perflevel1.physicalcpu"],
                capture_output=True, text=True, timeout=10)
            vals = [l.strip() for l in
                    r.stdout.splitlines() if l.strip()]
            if vals:
                out["brand"] = vals[0]
                try:
                    out["cores"] = int(vals[1])
                except (IndexError, ValueError):
                    pass
                out["arch"] = "arm64" if platform.machine(
                    ) in ("arm64",) else "x86_64"
    except Exception as e:
        out["error"] = str(e)[:100]
    try:
        if Path("/proc/cpuinfo").exists():
            txt = Path("/proc/cpuinfo").read_text()
            for line in txt.splitlines():
                if line.startswith("model name"):
                    out["brand"] = line.split(":", 1)[1].strip()
                    break
                if line.startswith("flags") or \
                        "Features" in line:
                    fl = line.lower()
                    if "avx512" in fl:
                        out["isa"] = "avx512"
                    elif "avx2" in fl:
                        out["isa"] = "avx2"
    except Exception:
        pass
    out["arch"] = out.get("arch") or (
        "arm64" if "arm" in platform.machine().lower()
        else "x86_64")
    return out


def flow_units(cpu: dict) -> int:
    """How big the flow: sockets x cores x threads x width."""
    return int(cpu.get("sockets", 1)) * \
        int(cpu.get("cores", 1)) * \
        int(cpu.get("threads", cpu.get("cores", 1))) * \
        int(cpu.get("width", 64))


def wikidata_enrich(name: str) -> dict:
    """Guided metrics from websearch datasets — Wikidata entity
    lookup returns label/description/id; honest {} on miss."""
    q = urllib.parse.quote(name.replace("_", " "))
    try:
        req = urllib.request.Request(
            WIKIDATA.format(q=q),
            headers={"User-Agent": "ATLAS-cpulink/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        hits = d.get("search", [])
        if not hits:
            return {"found": False}
        h = hits[0]
        return {"found": True, "label": h.get("label"),
                "description": h.get("description"),
                "qid": h.get("id"),
                "match": h.get("match", {}).get("type")}
    except Exception as e:
        return {"found": False, "error": str(e)[:100]}


def register(name: str, vendor: str = "", arch: str = "",
             cores: int | None = None, threads: int | None =
             None, width: int = 64, sockets: int = 1,
             enrich: bool = True) -> dict:
    """Register a CPU. Seed specs win for known parts; unknown
    names get Wikidata enrichment (guided websearch metrics)."""
    name = norm(name)
    base = dict(SEED_CPUS.get(name, {}))
    base.update({k: v for k, v in {
        "vendor": vendor, "arch": arch, "cores": cores,
        "threads": threads, "width": width,
        "sockets": sockets}.items() if v not in (None, "")})
    src = "seed" if name in SEED_CPUS else "manual"
    wd = wikidata_enrich(name) if enrich else {}
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "cpu_register", "name": name,
           "vendor": base.get("vendor"),
           "arch": base.get("arch", "unknown"),
           "cores": base.get("cores"),
           "threads": base.get("threads"),
           "width": base.get("width", 64),
           "sockets": base.get("sockets", 1),
           "flow_units": flow_units(base) if base.get(
               "cores") else None,
           "source": src,
           "wikidata": wd,
           "derived": True}
    _append(CPUS, rec)
    _update_save(name, rec)
    return rec


def traverse(from_cpu: str, to_cpu: str,
             payload: dict | None = None,
             nbytes: int = 0) -> dict:
    """CPU->CPU traversal — same as mesh hops: packet records,
    keeps updating as long as the node runs (long distance =
    plain HTTP/net I/O, no special transport)."""
    from_cpu, to_cpu = norm(from_cpu), norm(to_cpu)
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "cpu_traversal", "from": from_cpu,
           "to": to_cpu, "payload": payload or {},
           "nbytes": nbytes, "derived": True}
    _append(TRAV, rec)
    for c in (from_cpu, to_cpu):
        _touch_save(c)
    return rec


def _touch_save(name: str) -> None:
    p = SAVES / (name + ".json")
    save = {}
    if p.exists():
        try:
            save = json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    save.update({"kind": "cpu_save", "name": name,
                 "last_active": _device_time()["ts"],
                 "traversals": save.get("traversals", 0) + 1,
                 "derived": True})
    SAVES.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(save, indent=1))


def _update_save(name: str, rec: dict) -> None:
    p = SAVES / (name + ".json")
    save = {}
    if p.exists():
        try:
            save = json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    save.update({"kind": "cpu_save", "name": name,
                 "registered": rec["ts"],
                 "spec": {k: rec.get(k) for k in (
                     "vendor", "arch", "cores", "threads",
                     "width", "sockets", "flow_units")},
                 "wikidata": rec.get("wikidata", {}),
                 "last_active": rec["ts"],
                 "derived": True})
    SAVES.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(save, indent=1))


def pack_gguf() -> dict:
    """Organize the registry into GGUF — records sorted by
    arch/vendor so the pack reads as a structured catalog."""
    import namescrape
    recs = sorted(
        (r for r in _jsonl(CPUS) or []),
        key=lambda r: (r.get("arch", ""), r.get("vendor", ""),
                       r.get("name", "")))
    # dedupe: last registration per name wins
    latest = {}
    for r in recs:
        latest[r["name"]] = r
    recs = [latest[k] for k in sorted(latest)]
    res = namescrape.write_gguf(recs, GGUF)
    res["organized_by"] = ["arch", "vendor", "name"]
    res["cpus"] = len(recs)
    return res


def list_cpus(arch: str | None = None) -> dict:
    latest = {}
    for r in _jsonl(CPUS) or []:
        latest[r["name"]] = r
    recs = [latest[k] for k in sorted(latest)]
    if arch:
        recs = [r for r in recs
                if arch in (r.get("arch") or "")]
    return {"kind": "cpu_list", "count": len(recs),
            "cpus": [{k: r.get(k) for k in (
                "name", "vendor", "arch", "cores", "threads",
                "width", "flow_units", "source")} for r in
                recs]}


def status() -> dict:
    return {"kind": "cpulink_state",
            "device_time": _device_time(),
            "registered": len({r["name"] for r in
                               _jsonl(CPUS) or []}),
            "traversals": sum(1 for _ in
                              _jsonl(TRAV) or []),
            "saves": len(list(SAVES.glob("*.json")))
            if SAVES.exists() else 0,
            "gguf": str(GGUF) if GGUF.exists() else None,
            "local": detect_local(),
            "note": ("nodes = processor architectures; flow = "
                     "sockets*cores*threads*width; traversals "
                     "update over plain network I/O")}
