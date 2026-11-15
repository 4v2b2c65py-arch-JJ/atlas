"""QBits — tagged crossings for parallel realities and node
traversal OUTSIDE our domain scope.

Main config: data/config.json — qbits section defines:
  in_domain[]              nodes/domains inside mesh scope
  parallel_realties[]      realities reachable ONLY via qbit
  outside_domain_requires_qbit  traversal outside domain
                           MUST carry a qbit tag

QBIT TAG scheme:  qb-<seq>-<domain>-<reality>
  e.g. qb-0007-parallel-vanitas_timeline
Each crossing mints a tag; every data packet crossing under
that tag is logged — full traffic history per qbit.

Records:
  crossing  — node traversal between domains/realities
  packet    — data packet under a qbit (direction, kind,
              summary, bytes estimate)

Store: data/qbits/{crossings.jsonl, packets.jsonl,
       qbits.json (issued tags)}
"""

import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "qbits"
XINGS = OUT / "crossings.jsonl"
PACKETS = OUT / "packets.jsonl"
TAGS = OUT / "qbits.json"
CONFIG = ROOT / "atlas.config.json"

DEFAULT_QBIT_CFG = {
    "enabled": True, "prefix": "qb",
    "in_domain": ["atlas_mesh", "self", "peer_hub"],
    "parallel_realties": [],
    "outside_domain_requires_qbit": True,
    "packet_log_limit": 500,
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


def config() -> dict:
    """Main configuration — atlas.config.json merged over
    defaults; custom changes persist here."""
    cfg = json.loads(json.dumps(DEFAULT_QBIT_CFG))
    cfg["_file"] = str(CONFIG)
    if CONFIG.exists():
        try:
            disk = json.loads(CONFIG.read_text())
            q = disk.get("qbits", {})
            cfg.update({k: v for k, v in q.items()})
            cfg["_disk"] = disk
        except json.JSONDecodeError:
            pass
    return cfg


def set_config(section: str, key: str, value) -> dict:
    """Custom change to main configuration — writes
    atlas.config.json."""
    disk = {}
    if CONFIG.exists():
        try:
            disk = json.loads(CONFIG.read_text())
        except json.JSONDecodeError:
            pass
    disk.setdefault(section, {})[key] = value
    disk["kind"] = "atlas_main_config"
    disk["derived"] = True
    CONFIG.write_text(json.dumps(disk, indent=1))
    return {"kind": "config_set", "section": section,
            "key": key, "value": value}


def _tags() -> dict:
    if TAGS.exists():
        try:
            return json.loads(TAGS.read_text())
        except json.JSONDecodeError:
            pass
    return {"kind": "qbit_tags", "next": 1, "tags": {}}


def _save_tags(t: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    TAGS.write_text(json.dumps(t, indent=1))


def _mint(domain: str, reality: str) -> str:
    t = _tags()
    seq = t["next"]
    t["next"] += 1
    tag = "%s-%04d-%s-%s" % (
        _qcfg().get("prefix", "qb"), seq,
        (domain or "x").replace(" ", "_"),
        (reality or "x").replace(" ", "_"))
    t["tags"][tag] = {"seq": seq, "domain": domain,
                      "reality": reality,
                      "minted": _device_time()["ts"]}
    _save_tags(t)
    return tag


def _qcfg() -> dict:
    return config()


def in_domain(node_or_domain: str) -> bool:
    cfg = _qcfg()
    d = (node_or_domain or "").lower()
    return any(x in d for x in cfg.get("in_domain", []))


def is_parallel_reality(reality: str) -> bool:
    cfg = _qcfg()
    r = (reality or "").lower()
    return any(x in r for x in
               cfg.get("parallel_realties", []))


def cross(node: str, from_domain: str, to_domain: str,
          reality: str | None = None,
          qbit: str | None = None,
          via: str = "traversal") -> dict:
    """Node traversal between domains. Outside-domain or
    parallel-reality crossings MUST mint/consume a qbit tag —
    the crossing and its tag are recorded for traffic
    accounting."""
    cfg = _qcfg()
    if not cfg.get("enabled", True):
        return {"error": "qbits_disabled"}
    needs_qbit = (not in_domain(to_domain) or
                  (reality and is_parallel_reality(reality)) or
                  (cfg.get("outside_domain_requires_qbit")
                   and not in_domain(from_domain)
                   and not in_domain(to_domain)))
    minted = None
    if needs_qbit and not qbit:
        minted = _mint("parallel" if reality else
                       to_domain, reality or to_domain)
        qbit = minted
    elif needs_qbit and qbit and qbit not in \
            _tags()["tags"]:
        return {"error": "unknown_qbit", "qbit": qbit}
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "qbit_crossing",
           "node": node, "from_domain": from_domain,
           "to_domain": to_domain, "reality": reality,
           "qbit": qbit, "minted": minted is not None,
           "via": via,
           "outside_domain": needs_qbit,
           "derived": True}
    _append(XINGS, rec)
    return rec


def packet(qbit: str, direction: str, kind: str = "data",
           summary: str = "", nbytes: int = 0,
           node: str | None = None) -> dict:
    """Log a data packet crossing under a qbit — the traffic
    history for that crossing."""
    tags = _tags()["tags"]
    if qbit not in tags:
        return {"error": "unknown_qbit", "qbit": qbit}
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "qbit_packet", "qbit": qbit,
           "direction": direction, "packet_kind": kind,
           "summary": summary[:200], "nbytes": nbytes,
           "node": node, "derived": True}
    _append(PACKETS, rec)
    return rec


def traffic(qbit: str | None = None) -> dict:
    """Traffic history — per-qbit packet counts/sums, or all."""
    pkts = list(_jsonl(PACKETS) or [])
    if qbit:
        pkts = [p for p in pkts if p.get("qbit") == qbit]
        agg = {"packets": len(pkts),
               "bytes": sum(p.get("nbytes", 0) for p in pkts),
               "directions": {}}
        for p in pkts:
            d = p.get("direction", "?")
            agg["directions"][d] = \
                agg["directions"].get(d, 0) + 1
        return {"kind": "qbit_traffic", "qbit": qbit,
                "tag": _tags()["tags"].get(qbit), **agg}
    per = {}
    for p in pkts:
        q = p.get("qbit", "?")
        a = per.setdefault(q, {"packets": 0, "bytes": 0})
        a["packets"] += 1
        a["bytes"] += p.get("nbytes", 0)
    return {"kind": "qbit_traffic_all", "per_qbit": per,
            "total_packets": len(pkts)}


def status() -> dict:
    xings = list(_jsonl(XINGS) or [])
    tags = _tags()
    return {"kind": "qbits_state",
            "device_time": _device_time(),
            "enabled": _qcfg().get("enabled", True),
            "crossings": len(xings),
            "outside_domain": sum(
                1 for x in xings if x.get("outside_domain")),
            "qbits_issued": len(tags["tags"]),
            "packets": sum(1 for _ in _jsonl(PACKETS) or []),
            "parallel_realties": _qcfg().get(
                "parallel_realties", []),
            "note": ("outside-domain + parallel-reality "
                     "traversals carry qb- tags; packets "
                     "logged per qbit")}
