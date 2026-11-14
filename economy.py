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
import time
from pathlib import Path

ROOT = Path(__file__).parent
PATH = ROOT / "data" / "economy.json"

EARN_PER_PLAY = 0.001   # 1 earn per 1000 stream hits
PAYOUT_MIN = 1.0        # minimum payout request


def _load() -> dict:
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return {"wallets": {}, "events": [], "impressions": {}}


def _save(st: dict) -> None:
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(st, indent=1))


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


def status(node: str | None = None) -> dict:
    st = _load()
    if node:
        return {"wallet": st["wallets"].get(node),
                "impressions": len(st["impressions"]),
                "plays": sum(st["impressions"].values())}
    wallets = {n: {"balance": w["balance"], "earned": w["earned"],
                   "paid": w["paid"],
                   "pending": sum(1 for p in w["payouts"]
                                  if p["status"] == "pending")}
               for n, w in st["wallets"].items()}
    out = {"wallets": wallets,
           "total_earned": round(sum(w["earned"]
                                     for w in st["wallets"]
                                     .values()), 4),
           "total_paid": round(sum(w["paid"]
                                   for w in st["wallets"]
                                   .values()), 4),
           "plays": sum(st["impressions"].values()),
           "events": len(st["events"])}
    try:
        import prime
        out["prime_funding_sats"] = prime.funding_sats()
    except Exception:
        pass
    return out
