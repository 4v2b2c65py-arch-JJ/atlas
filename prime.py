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


# ---- QCP banking rail (:8895) — Sutton Bank / Cash App providers --

QCP = "http://localhost:8895"


def _qcp_get(path: str, timeout: int = 15) -> dict:
    try:
        with urllib.request.urlopen(QCP + path, timeout=timeout) \
                as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": str(e)}


def _qcp_post(path: str, body: dict, timeout: int = 30) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(QCP + path, data=data,
                                 headers={"Content-Type":
                                          "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception as e:
        return {"error": str(e)}


def banking_status() -> dict:
    return _qcp_get("/api/v1/qcp/banking/status")


def banking_transfer(from_bank: str, to_bank: str,
                     amount: float, memo: str = "") -> dict:
    """Route a transfer between the banking providers (IOU rail —
    providers run mock until production env creds are set)."""
    return _qcp_post("/api/v1/qcp/banking/transfer", {
        "from_bank": from_bank, "to_bank": to_bank,
        "amount": amount, "memo": memo})


def banking_tax_summary(tax_year: str = "") -> dict:
    """Push the period's tax summary into the banking tax books —
    the working government-link record on the prime side."""
    return _qcp_post("/api/v1/qcp/banking/tax-summary",
                     {"tax_year": tax_year or
                                 time.strftime("%Y")})
