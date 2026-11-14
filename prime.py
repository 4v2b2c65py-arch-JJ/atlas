"""Prime API client — bridges ATLAS to the bitcoin-prime-api
(QBom Protocol) server on :8888 and its ManaCiel UTXO wallet store.

Exposes status, aggregate wallet balances (never keys/seeds), AI
generate, and unified query. Economy payouts can rail through
'prime' — settlement records allocation against the wallet pool.
"""

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://localhost:8888"
WALLETS_DIR = Path("/Applications/bitcoin/mana_ciel/wallets")
CACHE_TTL = 60
_cache: dict = {}


def _get(path: str, timeout: int = 15) -> dict:
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": str(e)}


def _post(path: str, body: dict, timeout: int = 120) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type":
                                          "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": str(e)}


def status() -> dict:
    """Prime server status + wallet pool summary."""
    st = _get("/api/v2/status")
    wl = wallets()
    return {"prime": st, "wallets": wl}


def wallets(force: bool = False) -> dict:
    """Aggregate UTXO wallet stats — counts and total sats only.
    NEVER returns seeds, keys, or addresses at scale."""
    now = time.time()
    if not force and now - _cache.get("ts", 0) < CACHE_TTL \
            and "data" in _cache:
        return _cache["data"]
    total_sats = 0
    count = 0
    top = []
    try:
        files = sorted(WALLETS_DIR.glob("*.json"))
        # sample every Nth for speed on huge dirs, count all
        step = max(1, len(files) // 5000)
        for f in files[::step]:
            try:
                d = json.loads(f.read_text())
                sats = int(d.get("utxo_collective_value") or 0)
                total_sats += sats
                count += 1
                if sats > 0:
                    top.append({"address": d.get("address"),
                                "sats": sats})
            except Exception:
                continue
        est_total = int(total_sats * (len(files) / max(count, 1)))
        top.sort(key=lambda x: -x["sats"])
        data = {"wallet_files": len(files), "sampled": count,
                "total_sats_est": est_total,
                "total_btc_est": round(est_total / 1e8, 8),
                "funded_wallets": len(top),
                "top": top[:10]}
    except Exception as e:
        data = {"error": str(e), "wallet_files": 0}
    _cache["data"] = data
    _cache["ts"] = now
    return data


def generate(prompt: str, **kw) -> dict:
    """Prime /api/v2/generate — UTXO-aware AI generation."""
    return _post("/api/v2/generate", {"prompt": prompt, **kw})


def query(text: str, **kw) -> dict:
    """Prime unified query endpoint."""
    return _post("/api/v2/unified/query", {"query": text, **kw})


def funding_sats() -> int:
    """Estimated total sats across the wallet pool (cached)."""
    return int(wallets().get("total_sats_est") or 0)
