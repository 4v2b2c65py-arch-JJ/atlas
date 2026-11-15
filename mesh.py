"""Continuity mesh — keep the network alive across active
nodes within READ_RADIUS_KM = 34,000 km of node read.

34,000 km exceeds Earth's diameter (~12,700 km), so the
radius is deliberately GLOBAL: every reachable active node
is inside read range; the metric that matters is whether a
node is ALIVE (heartbeat fresh) and LOCATED (coords known).

Node sources:
  - this hub (self)
  - peer_hubs.json entries (other ATLAS hubs)
  - peer_urls.json hosts (discovered media nodes)
  - manual register() calls

Geo: ip-api.com/json (free, no key) resolves IP -> lat/lon.
Honest fallback: unlocated nodes are reported as 'unlocated'
— never fabricated coordinates.

Continuity rule: a node is CONTINUOUS if it heartbeats
within CONTINUITY_TTL seconds AND sits inside the radius
(all located nodes do at 34,000 km). Gaps = active window
where no node covers.

Store: data/mesh/nodes.jsonl (append-only registry)
       data/mesh/heartbeats.jsonl
       data/mesh/mesh_state.json
"""

import json
import math
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "mesh"
NODES = OUT / "nodes.jsonl"
BEATS = OUT / "heartbeats.jsonl"
STATE = OUT / "mesh_state.json"

READ_RADIUS_KM = 34_000.0      # global read radius
CONTINUITY_TTL = 300.0         # 5 min heartbeat freshness
GEO_URL = "http://ip-api.com/json/{ip}?fields=status,lat,lon,city,country,query"


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


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * \
        math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geo_ip(ip: str) -> dict:
    """Resolve IP -> coords via ip-api.com. Returns
    {lat, lon, city, country} or {error} — honest failure."""
    if not ip or ip in ("127.0.0.1", "localhost", "::1"):
        return {"error": "private/loopback ip"}
    try:
        with urllib.request.urlopen(
                GEO_URL.format(ip=ip), timeout=8) as r:
            d = json.loads(r.read())
        if d.get("status") == "success":
            return {"lat": d["lat"], "lon": d["lon"],
                    "city": d.get("city"),
                    "country": d.get("country"),
                    "ip": d.get("query")}
        return {"error": d.get("message", "geo failed")}
    except Exception as e:
        return {"error": str(e)[:120]}


def _host_of(url: str) -> str:
    try:
        h = urlparse(url).hostname or url
        return h
    except Exception:
        return url


def register_node(node_id: str, url: str = "",
                  ip: str = "", lat: float | None = None,
                  lon: float | None = None,
                  kind: str = "peer") -> dict:
    """Register/refresh a node. Coords: explicit > geo lookup
    of url host or ip. Always honest about missing geo."""
    node_id = node_id.lower().replace(" ", "_")
    geo = {}
    if lat is not None and lon is not None:
        geo = {"lat": lat, "lon": lon, "source": "manual"}
    elif ip:
        geo = geo_ip(ip)
    elif url:
        # resolve host -> can't geo a hostname directly; try
        # if the 'ip' field actually carries an address
        maybe_ip = _host_of(url)
        if maybe_ip and maybe_ip[0].isdigit():
            geo = geo_ip(maybe_ip)
        else:
            geo = {"error": "hostname — ip needed for geo"}
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "mesh_node", "node": node_id,
           "url": url, "ip": ip or None,
           "geo": geo, "node_kind": kind,
           "derived": True}
    OUT.mkdir(parents=True, exist_ok=True)
    with NODES.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    heartbeat(node_id)
    return rec


def heartbeat(node_id: str) -> dict:
    """Keep continuity alive — stamp last_seen."""
    node_id = node_id.lower().replace(" ", "_")
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "heartbeat", "node": node_id}
    OUT.mkdir(parents=True, exist_ok=True)
    with BEATS.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def _nodes_latest() -> dict:
    """Latest registration per node + latest heartbeat."""
    regs, beats = {}, {}
    for r in _jsonl(NODES) or []:
        regs[r.get("node")] = r
    for r in _jsonl(BEATS) or []:
        beats[r.get("node")] = r.get("ts")
    return regs, beats


def _public_ip() -> str:
    """Outbound public IP via ipify — honest empty on failure."""
    for url in ("https://api.ipify.org?format=json",
                "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=6) \
                    as r:
                body = r.read().decode().strip()
            if url.endswith("json"):
                return json.loads(body).get("ip", "")
            return body
        except Exception:
            continue
    return ""


def _self_node() -> dict:
    """This hub as a mesh node — geo from PUBLIC ip (LAN ip
    can't be geo-located)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET,
                          socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        lan_ip = s.getsockname()[0]
        s.close()
    except Exception:
        lan_ip = ""
    pub = _public_ip()
    geo = geo_ip(pub) if pub else {
        "error": "no public ip — offline?"}
    return {"node": "self",
            "url": "http://127.0.0.1:8917",
            "ip": pub or lan_ip, "lan_ip": lan_ip,
            "geo": geo, "kind": "self"}


def mesh_nodes() -> list[dict]:
    """Union: self + peer hubs + peer-url hosts + manual
    registrations, with liveness and distance-from-self."""
    regs, beats = _nodes_latest()
    nodes = []
    # self
    selfn = _self_node()
    self_geo = selfn["geo"] if isinstance(
        selfn["geo"], dict) else {}
    self_ll = (self_geo.get("lat"), self_geo.get("lon")) \
        if "lat" in self_geo else (None, None)
    nodes.append({**selfn, "last_seen": time.time(),
                  "alive": True,
                  "distance_km": 0.0,
                  "within_radius": True})
    # peer hubs from server state
    try:
        hubs = json.loads(
            (DATA / "peer_hubs.json").read_text())
    except Exception:
        hubs = []
    for h in hubs[:20]:
        host = _host_of(h)
        ip = host if host and host[0].isdigit() else ""
        geo = geo_ip(ip) if ip else {
            "error": "hub hostname — no ip"}
        nodes.append(_node_view("hub_" + re_slug(host), h,
                                ip, geo, "peer_hub", beats))
    # manual/registered nodes
    for nid, reg in regs.items():
        if nid == "self":
            continue
        geo = reg.get("geo") or {}
        nodes.append(_node_view(
            nid, reg.get("url", ""), reg.get("ip", ""),
            geo, reg.get("node_kind", "peer"), beats))
    return nodes


def re_slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _node_view(nid, url, ip, geo, kind, beats) -> dict:
    last = beats.get(nid)
    alive = bool(last and
                 time.time() - last < CONTINUITY_TTL)
    lat, lon = geo.get("lat"), geo.get("lon")
    return {"node": nid, "url": url, "ip": ip,
            "geo": geo, "kind": kind,
            "last_seen": last, "alive": alive,
            "located": lat is not None and lon is not None,
            "distance_km": None, "within_radius": None}


def continuity() -> dict:
    """The continuity report: every active node within
    READ_RADIUS_KM of self, plus coverage gaps."""
    nodes = mesh_nodes()
    # distance from self for located nodes
    selfn = nodes[0]
    sg = selfn["geo"]
    if "lat" in sg:
        for n in nodes[1:]:
            g = n.get("geo") or {}
            if "lat" in g:
                d = _haversine_km(sg["lat"], sg["lon"],
                                  g["lat"], g["lon"])
                n["distance_km"] = round(d, 1)
                n["within_radius"] = d <= READ_RADIUS_KM
    alive_in = [n for n in nodes
                if n.get("alive") and
                n.get("within_radius", True)]
    gaps = [n["node"] for n in nodes
            if n.get("alive") and
            n.get("within_radius") is False]
    unlocated = [n["node"] for n in nodes
                 if n.get("alive") and not n.get("located")]
    return {"kind": "mesh_continuity",
            "device_time": _device_time(),
            "read_radius_km": READ_RADIUS_KM,
            "continuity_ttl_s": CONTINUITY_TTL,
            "nodes": nodes,
            "continuous_nodes": len(alive_in),
            "out_of_radius": gaps,
            "unlocated_active": unlocated,
            "coverage": ("global" if alive_in else "none"),
            "derived": True}


def read(node_id: str) -> dict:
    """Read a node: alive + within radius = readable."""
    node_id = node_id.lower().replace(" ", "_")
    nodes = mesh_nodes()
    for n in nodes:
        if n["node"] == node_id or \
                node_id in (n.get("url") or ""):
            in_range = n.get("within_radius")
            if in_range is None:
                in_range = True  # unlocated can't disprove
            return {"kind": "node_read", "node": n,
                    "readable": bool(n.get("alive")) and
                    bool(in_range),
                    "radius_km": READ_RADIUS_KM,
                    "reason": ("alive+in_range" if
                               n.get("alive") and in_range
                               else "stale" if not
                               n.get("alive") else
                               "out_of_range")}
    return {"kind": "node_read", "node": node_id,
            "readable": False, "reason": "unknown_node"}


INBOX = OUT / "ingest_inbox.jsonl"


def ingest(snapshot_id: str, payload: dict,
           transport: str = "network") -> dict:
    """Receive a routed payload (SeedGate network transport
    and friends) — append to the mesh inbox, heartbeat the
    implied source if present."""
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "mesh_ingest",
           "snapshotId": snapshot_id,
           "transport": transport,
           "payload": payload,
           "derived": True}
    OUT.mkdir(parents=True, exist_ok=True)
    with INBOX.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    src = (payload or {}).get("node") or \
        (payload or {}).get("source")
    if src:
        heartbeat(str(src))
    return {"kind": "mesh_ingest_result",
            "snapshotId": snapshot_id,
            "stored": True,
            "inbox": str(INBOX)}


def status() -> dict:
    c = continuity()
    STATE.write_text(json.dumps(
        {"ts": time.time(), "kind": "mesh_state",
         "continuous": c["continuous_nodes"],
         "nodes": len(c["nodes"]),
         "radius_km": READ_RADIUS_KM}, indent=1))
    return c
