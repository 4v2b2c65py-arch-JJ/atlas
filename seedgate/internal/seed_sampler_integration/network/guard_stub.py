"""network.client — REAL network transport (replaces guard_stub).

Same interface and result format as the stub, but actually
delivers: POSTs {snapshotId, payload} as JSON over HTTP to the
target resolved from the connection spec.

Spec mapping (kept aligned with dispatcher):
  established : bool  — must be True or nothing is sent
  mac         : str   — target node: mesh node id, hostname or
                        IP (resolved through ATLAS mesh when
                        possible)
  port        : int   — target port (default 8917, the hub)

Route: POST http://<host>:<port>/api/mesh/ingest
Body:  {"snapshotId": ..., "payload": {...}, "transport":
       "network", "ts": ...}

Result format preserved:
  {"snapshotId", "routed", "transport", "mac", "port",
   "url"?, "status"?, "reason"?}
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConnectionSpec:
    mac: str | None = None
    port: int | None = None
    established: bool = False


def _resolve_host(mac: str) -> str:
    """mac field carries the node: try mesh node registry first
    (peer hubs / registered nodes), else treat as host/ip."""
    try:
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parents[3]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        hubs = json.loads(
            (root / "data" / "peer_hubs.json").read_text())
        for h in hubs:
            host = h.split("://")[-1].split(":")[0]
            if host == mac or h == mac:
                return host
    except Exception:
        pass
    return mac


def _post(url: str, body: dict, timeout: float = 10.0) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return {"http": r.status,
                "body": r.read(65536).decode("utf-8", "replace")}


class NetworkClientStub:
    """Name kept for dispatcher compatibility — this is the
    REAL client now."""

    def __init__(self) -> None:
        self.history: list[dict[str, Any]] = []

    async def send_payload(self, snapshot_id: str,
                           payload: dict[str, Any],
                           connection_spec: ConnectionSpec
                           ) -> dict[str, Any]:
        if not connection_spec.established:
            return {
                "snapshotId": snapshot_id,
                "routed": False,
                "reason": "connection_not_established",
            }
        if not connection_spec.mac or \
                connection_spec.port is None:
            return {
                "snapshotId": snapshot_id,
                "routed": False,
                "reason": "missing_mac_or_port",
            }

        host = _resolve_host(connection_spec.mac)
        port = connection_spec.port or 8917
        url = "http://%s:%d/api/mesh/ingest" % (host, port)
        body = {"snapshotId": snapshot_id, "payload": payload,
                "transport": "network",
                "ts": time.time()}

        try:
            resp = await asyncio.to_thread(_post, url, body)
        except Exception as e:
            return {
                "snapshotId": snapshot_id,
                "routed": False,
                "transport": "network",
                "mac": connection_spec.mac,
                "port": port,
                "url": url,
                "reason": "send_failed:%s" % str(e)[:120],
            }

        self.history.append({
            "snapshotId": snapshot_id,
            "payloadKeys": sorted(list(payload.keys())),
            "mac": connection_spec.mac,
            "port": connection_spec.port,
            "url": url,
            "http": resp.get("http"),
        })

        return {
            "snapshotId": snapshot_id,
            "routed": resp.get("http", 0) < 400,
            "transport": "network",
            "mac": connection_spec.mac,
            "port": port,
            "url": url,
            "http_status": resp.get("http"),
        }
