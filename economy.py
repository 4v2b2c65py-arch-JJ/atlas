"""ATLAS economy — earnings ledger + payout rail for the network.

Revenue enters as events (donations, sponsorships, premium unlocks,
play impressions). Value accrues to node wallets; payouts are
requests against balances, settleable via BTC/on-chain or peer IOU
credit across the mesh. Everything is ledgered in data/economy.json
(gitignored) — the network's earnings are real records, not vibes.

Units: "earn" (internal credit). 1 earn = 1 impression-cent by
default; donations/sponsorships set their own amount.
"""

import json
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent
PATH = ROOT / "data" / "economy.json"
_lock = threading.Lock()

EARN_PER_PLAY = 0.001   # 1 earn per 1000 stream hits
PAYOUT_MIN = 1.0        # minimum payout request


def _load() -> dict:
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return {"wallets": {}, "events": [], "impressions": {}}


def _save(st: dict) -> None:
    PATH.parent.mkdir(exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(PATH)  # atomic — no torn writes under threads


def _wallet(st: dict, node: str) -> dict:
    return st["wallets"].setdefault(node, {
        "balance": 0.0, "earned": 0.0, "paid": 0.0,
        "payouts": []})


def credit(node: str, amount: float, kind: str,
           ref: str = "") -> dict:
    """Add earnings to a node wallet."""
    st = _load()
    w = _wallet(st, node or "atlas")
    w["balance"] = round(w["balance"] + amount, 6)
    w["earned"] = round(w["earned"] + amount, 6)
    st["events"].append({"ts": time.time(), "kind": kind,
                         "node": node or "atlas",
                         "amount": round(amount, 6), "ref": ref})
    st["events"] = st["events"][-5000:]
    _save(st)
    return {"node": node or "atlas", "balance": w["balance"]}


def record_play(path: str, node: str = "atlas") -> None:
    """One stream hit = one impression; accrues EARN_PER_PLAY."""
    st = _load()
    imp = st["impressions"]
    imp[path] = imp.get(path, 0) + 1
    total = sum(imp.values())
    # accrue in batches of 1000 impressions to limit writes
    if total % 1000 == 0:
        credit(node, EARN_PER_PLAY * 1000, "impressions",
               ref=path)
        _save(st)
    else:
        _save(st)


def revenue(amount: float, kind: str, node: str = "atlas",
            ref: str = "", currency: str = "USD") -> dict:
    """Real revenue event (donation/sponsor/premium) — credits the
    node wallet 1:1 in earns."""
    if amount <= 0:
        return {"error": "amount must be > 0"}
    return credit(node, float(amount), kind, ref or currency)


def payout(node: str, amount: float, method: str = "btc",
           address: str = "") -> dict:
    """Request a payout against the node's balance. method: btc |
    lightning | iou (peer credit). BTC address required for btc."""
    node = node or "atlas"
    if amount < PAYOUT_MIN:
        return {"error": f"minimum payout {PAYOUT_MIN} earn"}
    if method == "btc" and not address:
        return {"error": "btc payout requires an address"}
    st = _load()
    w = _wallet(st, node)
    if w["balance"] < amount:
        return {"error": "insufficient balance",
                "balance": w["balance"]}
    w["balance"] = round(w["balance"] - amount, 6)
    w["paid"] = round(w["paid"] + amount, 6)
    req = {"id": int(time.time() * 1000), "ts": time.time(),
           "amount": amount, "method": method, "address": address,
           "status": "pending"}
    # bank rail: fire the transfer through the prime QCP banking
    # providers at request time; tx id recorded on the payout
    if method == "bank":
        try:
            import prime
            tx = prime.banking_transfer(
                "sutton_bank", "cash_app", amount,
                memo=f"atlas payout {req['id']}")
            req["rail_tx"] = tx.get("tx_id") or tx
            req["rail"] = "qcp-banking"
        except Exception as e:
            req["rail_error"] = str(e)
    w["payouts"].append(req)
    w["payouts"] = w["payouts"][-100:]
    st["events"].append({"ts": time.time(), "kind": "payout",
                         "node": node, "amount": amount,
                         "ref": method})
    st["events"] = st["events"][-5000:]
    _save(st)
    return {"payout": req, "balance": w["balance"]}


def settle(payout_id: int, node: str = "atlas",
           tx_ref: str = "") -> dict:
    """Mark a payout settled (after on-chain send / peer IOU)."""
    st = _load()
    w = st["wallets"].get(node or "atlas")
    if not w:
        return {"error": "unknown node"}
    for p in w["payouts"]:
        if p["id"] == payout_id and p["status"] == "pending":
            p["status"] = "settled"
            p["tx"] = tx_ref
            p["settled_ts"] = time.time()
            _save(st)
            return {"settled": payout_id}
    return {"error": "payout not found or already settled"}


def refund(payout_id: int, node: str = "atlas") -> dict:
    """Return a pending payout to the wallet — earnings come back
    and the ledger stays balanced (no lost track)."""
    st = _load()
    w = st["wallets"].get(node or "atlas")
    if not w:
        return {"error": "unknown node"}
    for p in w["payouts"]:
        if p["id"] == payout_id:
            if p["status"] != "pending":
                return {"error": "only pending payouts can be "
                        "refunded"}
            p["status"] = "refunded"
            p["refunded_ts"] = time.time()
            w["balance"] = round(w["balance"] + p["amount"], 6)
            w["paid"] = round(w["paid"] - p["amount"], 6)
            st["events"].append({"ts": time.time(),
                                 "kind": "refund", "node": node,
                                 "amount": p["amount"],
                                 "ref": str(payout_id)})
            st["events"] = st["events"][-5000:]
            _save(st)
            return {"refunded": payout_id,
                    "balance": w["balance"]}
    return {"error": "payout not found"}


def reconcile() -> dict:
    """Reconcile pending payouts against prime wallet funding and
    persist the snapshot — earnings state survives restarts and is
    auditable at any time (prime-api record style)."""
    st = _load()
    pending = []
    pending_sats_equiv = 0
    for n, w in st["wallets"].items():
        for p in w["payouts"]:
            if p["status"] == "pending":
                pending.append({"node": n, "id": p["id"],
                                "amount": p["amount"],
                                "method": p["method"]})
                pending_sats_equiv += int(p["amount"] * 1000)
    funding = 0
    try:
        import prime
        funding = prime.funding_sats()
    except Exception:
        pass
    snap = {"ts": time.time(), "pending": pending,
            "pending_count": len(pending),
            "pending_earn": round(sum(p["amount"] for p in pending), 4),
            "prime_funding_sats": funding,
            "covered": funding >= pending_sats_equiv}
    st["last_reconcile"] = snap
    _save(st)
    return snap


def status(node: str | None = None) -> dict:
    st = _load()
    out = {"last_reconcile": st.get("last_reconcile")}
    if node:
        out.update({"wallet": st["wallets"].get(node),
                    "impressions": len(st["impressions"]),
                    "plays": sum(st["impressions"].values())})
        return out
    wallets = {n: {"balance": w["balance"], "earned": w["earned"],
                   "paid": w["paid"],
                   "pending": sum(1 for p in w["payouts"]
                                  if p["status"] == "pending")}
               for n, w in st["wallets"].items()}
    out.update({"wallets": wallets,
                "total_earned": round(sum(w["earned"]
                                     for w in st["wallets"]
                                     .values()), 4),
           "total_paid": round(sum(w["paid"]
                                   for w in st["wallets"]
                                   .values()), 4),
                "plays": sum(st["impressions"].values()),
                "events": len(st["events"])})
    try:
        import prime
        out["prime_funding_sats"] = prime.funding_sats()
    except Exception:
        pass
    return out
