"""NetLock — lock the network into a KNOWN MODE driven by the
mesh drivers inside READ_RADIUS (34,000 km), expanding per
hop, with history on the main device.

KNOWN MODES:
  mesh_global   — radius 34,000 km, hops expand coverage
  lan_local     — radius 1 km, no expansion
  locked_down   — radius 0, read-only, no new hops

Per HOP: node_a -> node_b extends the covered ring by
HOP_KM each — the mesh front advances hop by hop and every
hop is written to the main device's network history.

MAC ACCESS: every connection spec mac / node identifier is
registered in the MAC table — the drivers' address book.

INTERPRETER: payloads route through the SeedGate dispatcher
(seed_sampler_integration) — transports network/Bluetooth/
serial/USB_IO.

MAIN HANDLER: handler() is the single entry — validates the
lock, expands hops, invokes the interpreter, writes history.
Nothing bypasses it.

Store: data/netlock/{lock_state.json, hops.jsonl, macs.jsonl,
       history.jsonl}
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "netlock"
LOCK = OUT / "lock_state.json"
HOPS = OUT / "hops.jsonl"
MACS = OUT / "macs.jsonl"
HIST = OUT / "history.jsonl"
CRED = OUT / "credentials.json"
GGUF = OUT / "netlock_known.gguf"

MODES = {
    "mesh_global": {"radius_km": 34_000.0, "expand": True,
                    "read_only": False},
    "lan_local": {"radius_km": 1.0, "expand": False,
                  "read_only": False},
    "locked_down": {"radius_km": 0.0, "expand": False,
                    "read_only": True},
}
HOP_KM = 1_000.0        # coverage gained per hop
DEFAULT_RADIUS = 34_000.0


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


def _load_lock() -> dict:
    if LOCK.exists():
        try:
            return json.loads(LOCK.read_text())
        except json.JSONDecodeError:
            pass
    return {"kind": "netlock_state", "mode": None,
            "locked": False}


def lock(mode: str = "mesh_global",
         radius_km: float | None = None) -> dict:
    """Lock the network into a known mode. Returns the lock
    state; expansion starts from this radius."""
    mode = (mode or "mesh_global").lower()
    if mode not in MODES:
        return {"error": "unknown_mode",
                "modes": sorted(MODES)}
    spec = MODES[mode]
    radius = radius_km if radius_km is not None \
        else spec["radius_km"]
    state = {"kind": "netlock_state", "mode": mode,
             "locked": True,
             "base_radius_km": radius,
             "expand": spec["expand"],
             "read_only": spec["read_only"],
             "hops": 0,
             "effective_radius_km": radius,
             "locked_at": _device_time(),
             "derived": True}
    OUT.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps(state, indent=1))
    _append(HIST, {"ts": _device_time()["ts"],
                   "kind": "netlock", "action": "lock",
                   "mode": mode, "radius_km": radius})
    return state


def unlock() -> dict:
    state = _load_lock()
    state.update({"locked": False, "mode": None,
                  "unlocked_at": _device_time()})
    OUT.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps(state, indent=1))
    _append(HIST, {"ts": _device_time()["ts"],
                   "kind": "netlock", "action": "unlock"})
    return state


def _effective_radius(state: dict) -> float:
    base = state.get("effective_radius_km",
                     state.get("base_radius_km", 0.0))
    if state.get("expand"):
        base = state.get("base_radius_km", base) + \
            state.get("hops", 0) * HOP_KM
    return base


def hop(node_a: str, node_b: str, mac: str | None = None,
        via: str = "driver") -> dict:
    """Record a hop node_a -> node_b. Each hop expands the
    covered radius by HOP_KM when the mode allows expansion.
    MACs are registered on every hop."""
    state = _load_lock()
    if not state.get("locked"):
        return {"error": "network_not_locked",
                "hint": "lock a mode first"}
    if state.get("read_only"):
        return {"error": "mode_read_only",
                "mode": state.get("mode")}
    node_a = (node_a or "").lower().replace(" ", "_")
    node_b = (node_b or "").lower().replace(" ", "_")
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "netlock_hop", "hop": state.get("hops", 0)
           + 1, "from": node_a, "to": node_b,
           "mac": mac, "via": via,
           "mode": state.get("mode")}
    _append(HOPS, rec)
    if mac:
        _append(MACS, {"ts": rec["ts"], "mac": mac,
                       "node": node_b, "via": via})
    state["hops"] = state.get("hops", 0) + 1
    state["effective_radius_km"] = _effective_radius(state)
    state["last_hop"] = rec
    LOCK.write_text(json.dumps(state, indent=1))
    _append(HIST, {"ts": rec["ts"], "kind": "netlock",
                   "action": "hop", "hop": rec["hop"],
                   "from": node_a, "to": node_b})
    rec["effective_radius_km"] = state[
        "effective_radius_km"]
    rec["expanded_km"] = HOP_KM if state.get("expand") \
        else 0.0
    return rec


def register_mac(mac: str, node: str = "",
                 context: str = "manual") -> dict:
    """Add a MAC to the drivers' address book."""
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "netlock_mac", "mac": mac,
           "node": node, "context": context}
    _append(MACS, rec)
    _append(HIST, {"ts": rec["ts"], "kind": "netlock",
                   "action": "mac", "mac": mac})
    return rec


def macs() -> dict:
    """The MAC table — every address the drivers have seen."""
    seen = {}
    for r in _jsonl(MACS) or []:
        m = r.get("mac")
        if m:
            seen[m] = {"mac": m, "node": r.get("node"),
                       "last_ts": r.get("ts"),
                       "contexts": seen.get(m, {}).get(
                           "contexts", 0) + 1}
    return {"kind": "netlock_macs", "count": len(seen),
            "macs": list(seen.values())}


def interpreter(transport: str, snapshot_id: str,
                payload: dict, mac: str | None = None,
                port: int | None = None,
                established: bool = True) -> dict:
    """The interpreter — routes payloads through the SeedGate
    dispatcher (network/Bluetooth/serial/USB_IO)."""
    sys.path.insert(0, str(ROOT / "seedgate" / "internal"))
    from seed_sampler_integration.dispatcher import (
        TransportDispatcher, ConnectionSpec)
    spec = ConnectionSpec(mac=mac, port=port,
                          established=established)
    return asyncio.run(TransportDispatcher().route(
        transport=transport, snapshot_id=snapshot_id,
        payload=payload, connection_spec=spec))



def _credential() -> dict:
    """The store credential — password filled in, scoped to
    profile tag 'known'. Generated once, persisted; a derived
    local token, NOT a real secret."""
    import secrets
    if CRED.exists():
        try:
            return json.loads(CRED.read_text())
        except json.JSONDecodeError:
            pass
    cred = {"profile": "known",
            "password": secrets.token_hex(16),
            "scope": "mesh_netlock",
            "created": _device_time()["ts"],
            "derived": True}
    OUT.mkdir(parents=True, exist_ok=True)
    CRED.write_text(json.dumps(cred, indent=1))
    return cred


def scrape() -> dict:
    """Scrape the whole netlock+mesh state into GGUF records —
    lock, hops, MACs, mesh nodes, history tail — with the
    'known' profile credential filled in."""
    import namescrape
    cred = _credential()
    records = []
    st = handler("status")
    records.append({"src": "netlock", "kind": "lock_state",
                    "profile": "known", **{
                        k: st.get(k) for k in (
                            "mode", "locked",
                            "effective_radius_km", "hops",
                            "read_only", "expand")},
                    "credential": cred})
    for r in _jsonl(HOPS) or []:
        records.append({"src": "netlock", "kind": "hop",
                        "profile": "known", **r})
    for r in _jsonl(MACS) or []:
        records.append({"src": "netlock", "kind": "mac",
                        "profile": "known", **r})
    try:
        import mesh as _mesh
        for n in _mesh.mesh_nodes():
            records.append({"src": "mesh", "kind": "node",
                            "profile": "known", **{
                                k: n.get(k) for k in (
                                    "node", "url", "ip", "geo",
                                    "alive", "located",
                                    "distance_km",
                                    "within_radius", "kind")}})
    except Exception:
        pass
    hist = list(_jsonl(HIST) or [])
    for r in hist[-200:]:
        records.append({"src": "netlock", "kind": "history",
                        "profile": "known", **r})
    res = namescrape.write_gguf(records, GGUF)
    res["profile"] = "known"
    res["credential_profile"] = cred["profile"]
    _append(HIST, {"ts": _device_time()["ts"],
                   "kind": "netlock", "action": "scrape",
                   "records": len(records),
                   "gguf": res["path"]})
    return {"kind": "netlock_scrape", **res,
            "records": len(records)}


def record_scrape_report(report: dict) -> dict:
    """A node reported a scrape to another store — merge the
    report into credentials and refresh the GGUF under
    profile 'known'."""
    node = (report.get("node") or report.get("source")
            or "unknown")
    cred = _credential()
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "scrape_report", "node": node,
           "store": report.get("store"),
           "scraped": report.get("scraped", report),
           "profile": "known", "derived": True}
    _append(CRED.with_suffix(".reports.jsonl"), rec)
    scrape()
    return {"kind": "scrape_report_result",
            "node": node, "profile": "known",
            "gguf_updated": True}


def handler(action: str, **kw) -> dict:
    """MAIN HANDLER — the only entry point. Validates lock,
    dispatches to hop/mac/interpreter, writes history."""
    a = (action or "status").lower()
    if a == "lock":
        return lock(kw.get("mode", "mesh_global"),
                    kw.get("radius_km"))
    if a == "unlock":
        return unlock()
    if a == "hop":
        return hop(kw.get("from", ""), kw.get("to", ""),
                   kw.get("mac"), kw.get("via", "driver"))
    if a == "mac":
        return register_mac(kw.get("mac", ""),
                            kw.get("node", ""),
                            kw.get("context", "manual"))
    if a == "send":
        state = _load_lock()
        if not state.get("locked"):
            return {"error": "network_not_locked"}
        if mac := kw.get("mac"):
            register_mac(mac, kw.get("to", ""),
                         "interpreter")
        res = interpreter(
            kw.get("transport", "network"),
            kw.get("snapshot", "netlock"),
            kw.get("payload") or {},
            mac=kw.get("mac"), port=kw.get("port"),
            established=kw.get("established", True))
        _append(HIST, {"ts": _device_time()["ts"],
                       "kind": "netlock", "action": "send",
                       "snapshot": kw.get("snapshot"),
                       "routed": res.get("routed"),
                       "transport": kw.get("transport")})
        return res
    if a == "scrape":
        return scrape()
    if a == "macs":
        return macs()
    if a == "history":
        recs = list(_jsonl(HIST) or [])
        return {"kind": "netlock_history",
                "count": len(recs),
                "records": recs[-int(kw.get("limit", 100)):]}
    if a == "status":
        state = _load_lock()
        state["effective_radius_km"] = _effective_radius(state)
        state["mac_count"] = macs()["count"]
        state["hop_count"] = sum(
            1 for _ in _jsonl(HOPS) or [])
        state["history_count"] = sum(
            1 for _ in _jsonl(HIST) or [])
        return state
    return {"error": "unknown_action", "action": a,
            "actions": ["lock", "unlock", "hop", "mac",
                        "send", "macs", "history", "status",
                        "scrape"]}
