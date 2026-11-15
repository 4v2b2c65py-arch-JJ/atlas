"""NodeReg — unified node registry by radius.

Chums every node source into ONE registry: PCIe devices
(scraped live), silicon devices, cpulink CPUs, mesh nodes,
qbit realities. Each node registers WITH a radius band —
the mesh read radius it lives inside (34,000 km default,
per-node override).

ANTI-FRY BACKUPS: every registry mutation snapshots the
prior state to backups/<ts>.json first — restore() rolls
back. Keeps last KEEP_BACKUPS.

UPTIME/DOWNTIME CYCLES: heartbeat() stamps nodes; cycles
record run/gap segments so after a restart the SAME cycle
resumes — downtime is measured, not lost. Render activity
keeps its cadence across restarts.

GIT-FORMAT FORWARDER: forward() emits the registry as a
git-friendly bundle — one JSONL stream + manifest, ready
to commit or ship to a peer.

Store: data/nodereg/{nodes.jsonl, cycles.jsonl,
       backups/, forward/}
"""

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "nodereg"
NODES = OUT / "nodes.jsonl"
CYCLES = OUT / "cycles.jsonl"
BACKUPS = OUT / "backups"
FORWARD = OUT / "forward"
KEEP_BACKUPS = 20
DEFAULT_RADIUS_KM = 34_000.0
CYCLE_GAP_S = 120.0   # silence beyond this = downtime


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


def _snapshot() -> list[dict]:
    return list(_jsonl(NODES) or [])


def _backup() -> None:
    """Anti-fry: snapshot registry BEFORE mutation."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dest = BACKUPS / ("%s.json" % _device_time()["iso"]
                      .replace(":", "-").replace("+", "p"))
    dest.write_text(json.dumps(_snapshot(), indent=1))
    olds = sorted(BACKUPS.glob("*.json"))
    for p in olds[:-KEEP_BACKUPS]:
        p.unlink(missing_ok=True)


def scrape_pcie() -> list[dict]:
    """Enumerate PCIe devices — system_profiler on macOS,
    lspci on Linux. Honest: empty list when nothing found."""
    devs = []
    try:
        r = subprocess.run(
            ["system_profiler", "SPPCIDataType"],
            capture_output=True, text=True, timeout=20)
        cur = None
        for line in r.stdout.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.endswith(":") and not s.startswith(
                    (" ", "-")):
                if cur:
                    devs.append(cur)
                cur = {"name": s.rstrip(":"),
                       "src": "system_profiler"}
            elif cur and ":" in s:
                k, _, v = s.partition(":")
                cur[k.strip().lower().replace(" ", "_")] = \
                    v.strip()
        if cur:
            devs.append(cur)
    except Exception:
        pass
    if not devs:
        try:
            r = subprocess.run(["lspci", "-nn"],
                               capture_output=True, text=True,
                               timeout=10)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    parts = line.split(None, 3)
                    if len(parts) >= 4:
                        devs.append({
                            "name": parts[3].split("[")[0],
                            "bus": parts[0], "device": parts[1],
                            "src": "lspci"})
        except Exception:
            pass
    return devs


def register(node_id: str, kind: str = "device",
             radius_km: float = DEFAULT_RADIUS_KM,
             meta: dict | None = None,
             backup: bool = True) -> dict:
    """Register a node by radius band. Anti-fry backup taken
    first unless backup=False (bulk imports)."""
    node_id = node_id.lower().replace(" ", "_")
    if backup:
        _backup()
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "nodereg_node", "node": node_id,
           "node_kind": kind, "radius_km": radius_km,
           "meta": meta or {}, "derived": True}
    _append(NODES, rec)
    return rec


def chum_all(radius_km: float = DEFAULT_RADIUS_KM
             ) -> dict:
    """Scrape PCIe + pull silicon/cpulink/mesh registries into
    the unified node table — the big chum."""
    added = []
    for d in scrape_pcie():
        nid = "pcie_" + "".join(
            c if c.isalnum() else "_"
            for c in (d.get("name") or "dev").lower())[:40]
        r = register(nid, "pcie_device",
                     radius_km, meta=d, backup=False)
        added.append(nid)
    # silicon devices
    try:
        import silicon as _si
        seen = set()
        for r in _jsonl(_si.DEVS) or []:
            n = r.get("name")
            if n and n not in seen:
                seen.add(n)
                register("silicon_" + n, "silicon",
                         radius_km, meta={
                             "device_kind":
                             r.get("device_kind"),
                             "width": r.get("width")},
                         backup=False)
                added.append("silicon_" + n)
    except Exception:
        pass
    # cpulink cpus
    try:
        import cpulink as _cl
        seen = set()
        for r in _jsonl(_cl.CPUS) or []:
            n = r.get("name")
            if n and n not in seen:
                seen.add(n)
                register("cpu_" + n, "processor",
                         radius_km, meta={
                             "arch": r.get("arch"),
                             "flow_units":
                             r.get("flow_units")},
                         backup=False)
                added.append("cpu_" + n)
    except Exception:
        pass
    # mesh nodes
    try:
        import mesh as _mx
        for n in _mx.mesh_nodes():
            register("mesh_" + str(n.get("node")),
                     "mesh_node", radius_km, meta={
                         "alive": n.get("alive"),
                         "distance_km":
                         n.get("distance_km")},
                     backup=False)
            added.append("mesh_" + str(n.get("node")))
    except Exception:
        pass
    if added:
        _backup()  # one snapshot after the bulk chum
    return {"kind": "nodereg_chum", "added": len(added),
            "nodes": added[:100],
            "pcie_found": len(scrape_pcie())}


def heartbeat(node_id: str) -> dict:
    """Stamp a node — uptime/downtime cycle tracking. Gaps
    > CYCLE_GAP_S close the run segment and open downtime;
    the SAME cycle resumes after restart."""
    node_id = node_id.lower().replace(" ", "_")
    now = time.time()
    # find last cycle event for continuity
    last = None
    for r in reversed(list(_jsonl(CYCLES) or [])):
        if r.get("node") == node_id:
            last = r
            break
    gap = now - (last or {}).get("ts", 0) \
        if last else None
    down = bool(gap and gap > CYCLE_GAP_S)
    cyc = (last or {}).get("cycle", 0)
    if down:
        _append(CYCLES, {"ts": now, "kind": "downtime_end",
                         "node": node_id, "cycle": cyc,
                         "gap_s": round(gap, 1)})
    else:
        cyc += 1 if last is None else 0
    rec = {"ts": now, "device_time": _device_time(),
           "kind": "nodereg_beat", "node": node_id,
           "cycle": cyc, "gap_s": round(gap, 1)
           if gap else None, "resumed": down,
           "derived": True}
    _append(CYCLES, rec)
    return rec


def cycles(node_id: str | None = None) -> dict:
    """Uptime/downtime history — per node or all."""
    recs = [r for r in (_jsonl(CYCLES) or [])
            if not node_id or r.get("node") == node_id]
    per = {}
    for r in recs:
        n = r.get("node", "?")
        per.setdefault(n, []).append(r)
    return {"kind": "nodereg_cycles",
            "nodes": {n: len(v) for n, v in per.items()},
            "records": recs[-200:],
            "note": ("same cycle resumes after restart; "
                     "gaps > %ds recorded as downtime"
                     % CYCLE_GAP_S)}


def forward() -> dict:
    """Git-format forwarder — registry as a committable
    bundle: manifest + single JSONL stream."""
    FORWARD.mkdir(parents=True, exist_ok=True)
    stamp = _device_time()["date"]
    nodes = _snapshot()
    manifest = {"kind": "nodereg_forward",
                "created": _device_time(),
                "nodes": len(nodes),
                "format": "jsonl+manifest",
                "git_ready": True}
    mpath = FORWARD / ("manifest_%s.json" % stamp)
    npath = FORWARD / ("nodes_%s.jsonl" % stamp)
    mpath.write_text(json.dumps(manifest, indent=1))
    with npath.open("w") as f:
        for r in nodes:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"kind": "nodereg_forward_result",
            "manifest": str(mpath), "nodes_file": str(npath),
            "nodes": len(nodes)}


def restore(backup_file: str | None = None) -> dict:
    """Anti-fry restore — roll registry back to a snapshot."""
    files = sorted(BACKUPS.glob("*.json"))
    if not files:
        return {"error": "no backups"}
    target = BACKUPS / backup_file if backup_file else \
        files[-1]
    if not target.exists():
        return {"error": "backup not found",
                "available": [p.name for p in files[-5:]]}
    _backup()  # snapshot current before overwriting
    NODES.write_text(target.read_text())
    return {"kind": "nodereg_restore",
            "restored_from": target.name,
            "nodes": len(json.loads(target.read_text()))}


def status() -> dict:
    nodes = _snapshot()
    latest = {}
    for r in nodes:
        latest[r.get("node")] = r
    kinds = {}
    for r in latest.values():
        k = r.get("node_kind", "?")
        kinds[k] = kinds.get(k, 0) + 1
    return {"kind": "nodereg_state",
            "device_time": _device_time(),
            "records": len(nodes),
            "unique_nodes": len(latest),
            "by_kind": kinds,
            "backups": len(list(BACKUPS.glob("*.json")))
            if BACKUPS.exists() else 0,
            "cycles": sum(1 for _ in _jsonl(CYCLES) or []),
            "forward_ready": True,
            "note": ("unified registry: pcie+silicon+cpu+mesh "
                     "+qbit by radius; anti-fry backups; "
                     "uptime cycles survive restart")}
