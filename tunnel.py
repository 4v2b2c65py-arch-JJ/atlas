"""Public tunnel manager — exposes the local hub via a Cloudflare
quick tunnel (trycloudflare.com) so /api/stream URLs are reachable
from the public internet (Meta/Threads video_url, peer hubs off-LAN,
mobile clients).

No account needed: `cloudflared tunnel --url` prints a random
https://*.trycloudflare.com URL on stderr. The manager keeps the
process alive, restarts it on death, and persists the current URL to
data/tunnel.json. social.set_public_base is fed automatically.
"""

import json
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
BIN = ROOT / "bin" / "cloudflared"
STATE_PATH = ROOT / "data" / "tunnel.json"
CF_PATH = ROOT / "data" / "cloudflare.json"
LOCAL_URL = "http://localhost:8917"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
CF_API = "https://api.cloudflare.com/client/v4"

_proc: subprocess.Popen | None = None
_url: str | None = None
_mode: str | None = None
_lock = threading.Lock()
_thread: threading.Thread | None = None


def _read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _write_state(**kw) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(
        {**_read_state(), **kw, "ts": time.time()}, indent=1))


def status() -> dict:
    alive = _proc is not None and _proc.poll() is None
    cf = _cf_load()
    return {"active": alive,
            "mode": _mode if alive else None,
            "url": _url if alive else (cf.get("hostname")
                                       if cf.get("tunnel_token") else None),
            "hostname": cf.get("hostname"),
            "named_ready": bool(cf.get("tunnel_token")),
            "local": LOCAL_URL, "binary": str(BIN),
            "binary_ok": BIN.exists()}


# ---------- named tunnel via Cloudflare API ----------

def _cf_load() -> dict:
    try:
        return json.loads(CF_PATH.read_text())
    except Exception:
        return {}


def _cf_save(data: dict) -> None:
    CF_PATH.parent.mkdir(parents=True, exist_ok=True)
    CF_PATH.write_text(json.dumps(data, indent=1))


def _cf_api(method: str, path: str, payload: dict | None = None,
            token: str | None = None) -> dict:
    cf = _cf_load()
    token = token or cf.get("api_token")
    if not token:
        return {"error": "no cloudflare api_token configured"}
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        CF_API + path, data=body, method=method,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"http {e.code}",
                "detail": e.read().decode("utf-8", "replace")[:300]}


def cf_configure(fields: dict) -> dict:
    """Store Cloudflare credentials: api_token, account_id, zone_id,
    hostname (e.g. atlas.example.com)."""
    cf = _cf_load()
    for k in ("api_token", "account_id", "zone_id", "hostname"):
        if fields.get(k):
            cf[k] = fields[k]
    _cf_save(cf)
    return {"configured": all(cf.get(k) for k in
                              ("api_token", "account_id", "zone_id",
                               "hostname")),
            "hostname": cf.get("hostname")}


def cf_discover(token: str | None = None) -> dict:
    """Auto-fill account_id/zone_id/hostname from just an api_token.
    Returns the fields still needed plus what was discovered."""
    cf = _cf_load()
    token = token or cf.get("api_token")
    if not token:
        return {"error": "api_token required — create one at "
                "dash.cloudflare.com/profile/api-tokens with "
                "Account:Cloudflare Tunnel Edit + Zone:DNS Edit"}
    if token:
        cf["api_token"] = token
    out = {"discovered": {}, "missing": []}
    # accounts
    if not cf.get("account_id"):
        r = _cf_api("GET", "/accounts", token=token)
        accs = r.get("result") or []
        if accs:
            cf["account_id"] = accs[0]["id"]
            out["discovered"]["account_id"] = accs[0]["id"]
            out["discovered"]["account_name"] = accs[0].get("name")
        else:
            out["missing"].append("account_id")
    # zones
    if cf.get("account_id") and not cf.get("zone_id"):
        r = _cf_api("GET", "/zones?per_page=50",
                    token=token)
        zones = r.get("result") or []
        if zones:
            z = zones[0]
            cf["zone_id"] = z["id"]
            out["discovered"]["zone_id"] = z["id"]
            out["discovered"]["zone_name"] = z.get("name")
            if not cf.get("hostname"):
                cf["hostname"] = f"atlas.{z['name']}"
                out["discovered"]["hostname"] = cf["hostname"]
        else:
            out["missing"].append("zone_id")
    for k in ("hostname",):
        if not cf.get(k):
            out["missing"].append(k)
    _cf_save(cf)
    out["configured"] = all(cf.get(k) for k in
                            ("api_token", "account_id", "zone_id",
                             "hostname"))
    out["next"] = ("POST /api/tunnel {action:'named'} to provision"
                   if out["configured"] else
                   "supply missing fields via action:'configure'")
    return out


def cf_create() -> dict:
    """Create (or reuse) a named tunnel via the API, wire DNS, store
    the connector token, and switch the runner to it."""
    cf = _cf_load()
    missing = [k for k in ("api_token", "account_id", "zone_id",
                           "hostname") if not cf.get(k)]
    if missing:
        return {"error": f"missing cloudflare fields: {missing}"}
    acc = cf["account_id"]
    if cf.get("tunnel_id") and cf.get("tunnel_token"):
        tid = cf["tunnel_id"]
    else:
        r = _cf_api("POST", f"/accounts/{acc}/cfd_tunnel",
                    {"name": "atlas", "config_src": "cloudflare"})
        res = r.get("result") or {}
        tid, token = res.get("id"), res.get("token")
        if not tid or not token:
            return {"error": "tunnel create failed", "detail": r}
        cf.update({"tunnel_id": tid, "tunnel_token": token})
        _cf_save(cf)
    # DNS: CNAME hostname -> <tunnel-id>.cfargotunnel.com (proxied)
    host = cf["hostname"]
    zone = cf["zone_id"]
    recs = _cf_api("GET", f"/zones/{zone}/dns_records"
                   f"?name={urllib.parse.quote(host)}")
    existing = (recs.get("result") or [])
    content = f"{tid}.cfargotunnel.com"
    if existing:
        rid = existing[0]["id"]
        r = _cf_api("PUT", f"/zones/{zone}/dns_records/{rid}",
                    {"type": "CNAME", "name": host,
                     "content": content, "proxied": True,
                     "ttl": 1})
    else:
        r = _cf_api("POST", f"/zones/{zone}/dns_records",
                    {"type": "CNAME", "name": host,
                     "content": content, "proxied": True, "ttl": 1})
    dns_ok = bool((r.get("result") or {}).get("id"))
    # route ingress: catch-all -> http://localhost:8917
    _cf_api("PUT", f"/accounts/{acc}/cfd_tunnel/{tid}/configurations",
            {"config": {"ingress": [
                {"hostname": host, "service": LOCAL_URL},
                {"service": "http_status:404"}]}})
    _cf_save(cf)
    return {"ok": dns_ok, "tunnel_id": tid, "hostname": host,
            "dns": "ok" if dns_ok else r}


def _pump() -> None:
    """Read cloudflared stderr until the trycloudflare URL appears,
    then keep draining so the pipe never blocks."""
    global _url
    try:
        for line in iter(_proc.stderr.readline, b""):
            text = line.decode("utf-8", "replace")
            m = URL_RE.search(text)
            if m and _url != m.group(0):
                _url = m.group(0)
                _write_state(url=_url)
                try:
                    import social
                    social.set_public_base(_url)
                except Exception:
                    pass
    except Exception:
        pass


def _spawn() -> None:
    global _proc, _url, _mode
    if _proc is not None and _proc.poll() is None:
        return
    if not BIN.exists():
        return
    cf = _cf_load()
    if cf.get("tunnel_token"):
        # named tunnel: remotely-managed connector, stable hostname
        _proc = subprocess.Popen(
            [str(BIN), "tunnel", "--no-autoupdate", "run",
             "--token", cf["tunnel_token"]],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=False)
        _mode = "named"
        _url = f"https://{cf.get('hostname')}" if cf.get("hostname") \
            else None
    else:
        _proc = subprocess.Popen(
            [str(BIN), "tunnel", "--url", LOCAL_URL,
             "--no-autoupdate"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=False)
        _mode = "quick"
        _url = None
    threading.Thread(target=_pump, daemon=True).start()


def _watchdog() -> None:
    """Restart the tunnel if cloudflared dies."""
    while True:
        time.sleep(15)
        if _proc is not None and _proc.poll() is not None:
            _proc = None
            _url = None
            _spawn()


def start() -> dict:
    global _thread
    _spawn()
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_watchdog, daemon=True)
        _thread.start()
    # give it a moment to emit the URL
    for _ in range(40):
        if _url:
            break
        time.sleep(0.25)
    return status()


def stop() -> dict:
    global _proc, _url
    if _proc is not None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except Exception:
            _proc.kill()
    _proc, _url = None, None
    _write_state(url=None)
    return status()
