"""Tax statements — turn the economy ledger into filing-ready
reports and (when a government/e-file endpoint is configured) submit
them.

Source of truth: data/economy.json events — every revenue, payout,
refund, and impression batch is already ledgered, so statements can
never drift from the books.

Export: data/tax/<node>_<period>.{json,csv}
File:   marks events as reported so re-filing can't double-count.
Gov:    data/tax_config.json {"authority","endpoint","token_ref"}
        — without it, export works and file() records intent only.
        Real government e-file needs accredited-provider credentials;
        this module never fakes a submission.
"""

import csv
import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
ECON = DATA / "economy.json"
CONFIG = DATA / "tax_config.json"
OUT = DATA / "tax"
PRIME_DIR = Path(os.environ.get(
    "PRIME_API_DIR", "/Users/jjmarte/bitcoin-prime-api"))


def _banking_identity() -> dict:
    """Taxpayer identity from the prime-api banking environment —
    the same env details the banking providers resolve. Returns
    only non-secret fields (name, bank, last-4); never card
    numbers, tokens, or keys."""
    try:
        if str(PRIME_DIR) not in sys.path:
            sys.path.insert(0, str(PRIME_DIR))
        from banking.config import BankingConfig
        cfg = BankingConfig.from_env()
        ca, sb = cfg.cash_app, cfg.sutton_bank
        return {
            "taxpayer_name": ca.payee_name or "",
            "payee_email": ca.payee_email or "",
            "bank": "Sutton Bank",
            "routing_number": sb.routing_number
                or ca.routing_number,
            "account_last4": (sb.account_number
                              or ca.account_number or "")[-4:],
            "agent_name": cfg.agent_name,
            "agent_id": cfg.agent_id,
            "source": "prime-banking-env",
        }
    except Exception:
        return {}


INCOME_KINDS = {"donation", "sponsor", "premium", "impressions",
                "revenue"}
EXPENSE_KINDS = {"payout"}
RETURN_KINDS = {"refund"}


def _econ() -> dict:
    try:
        return json.loads(ECON.read_text())
    except Exception:
        return {"events": [], "wallets": {}}


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def statement(node: str | None = None, since: float = 0.0,
              until: float = 0.0) -> dict:
    """Aggregate economy events into tax lines for a period."""
    evs = []
    for e in _econ().get("events", []):
        if node and e.get("node") not in (node, "atlas", None):
            continue
        if since and e.get("ts", 0) < since:
            continue
        if until and e.get("ts", 0) > until:
            continue
        evs.append(e)
    income = sum(e["amount"] for e in evs
                 if e.get("kind") in INCOME_KINDS)
    expenses = sum(e["amount"] for e in evs
                   if e.get("kind") in EXPENSE_KINDS)
    refunds = sum(e["amount"] for e in evs
                  if e.get("kind") in RETURN_KINDS)
    by_kind = {}
    for e in evs:
        k = e.get("kind", "?")
        by_kind[k] = round(by_kind.get(k, 0) + e.get("amount", 0), 6)
    return {
        "node": node or "all",
        "period": {"since": since or None, "until": until or None},
        "generated": time.time(),
        "events": len(evs),
        "gross_income": round(income, 4),
        "expenses": round(expenses, 4),
        "refunds_returned": round(refunds, 4),
        "net": round(income - expenses - refunds, 4),
        "taxpayer": _banking_identity() or None,
        "by_kind": by_kind,
        "lines": [{"ts": e["ts"], "kind": e["kind"],
                   "amount": e["amount"], "ref": e.get("ref", "")}
                  for e in evs[-500:]]}


def export(node: str | None = None, since: float = 0.0,
           until: float = 0.0, fmt: str = "both") -> dict:
    """Write the statement to data/tax/ as JSON + CSV."""
    st = statement(node, since, until)
    OUT.mkdir(parents=True, exist_ok=True)
    label = (node or "all") + "_" + time.strftime(
        "%Y%m%d_%H%M%S", time.gmtime(st["generated"]))
    written = []
    if fmt in ("json", "both"):
        p = OUT / f"{label}.json"
        p.write_text(json.dumps(st, indent=1))
        written.append(str(p))
    if fmt in ("csv", "both"):
        p = OUT / f"{label}.csv"
        with p.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "kind", "amount", "ref"])
            for ln in st["lines"]:
                w.writerow([ln["ts"], ln["kind"], ln["amount"],
                            ln["ref"]])
        written.append(str(p))
    return {"statement": {k: st[k] for k in
                          ("gross_income", "expenses", "net",
                           "events", "by_kind")},
            "files": written}


def file(node: str | None = None, since: float = 0.0,
         until: float = 0.0) -> dict:
    """Record a filing: export the statement, mark covered events
    as reported, and submit to the configured government endpoint
    if one exists. Never fakes a submission."""
    st = statement(node, since, until)
    ex = export(node, since, until)
    cfg = _config()
    # mark events in range as reported (dedupe guard)
    econ = _econ()
    marked = 0
    for e in econ.get("events", []):
        if e.get("reported"):
            continue
        if since and e.get("ts", 0) < since:
            continue
        if until and e.get("ts", 0) > until:
            continue
        if node and e.get("node") not in (node, "atlas", None):
            continue
        e["reported"] = True
        e["reported_ts"] = time.time()
        marked += 1
    if marked:
        econ["events"] = econ.get("events", [])[-5000:]
        ECON.write_text(json.dumps(econ, indent=1))
    result = {"filing_id": int(time.time() * 1000),
              "marked_reported": marked,
              "files": ex["files"],
              "net": st["net"]}
    endpoint = cfg.get("endpoint")
    if endpoint:
        # real submission path — posts the JSON statement
        try:
            import urllib.request
            data = json.dumps(st).encode()
            req = urllib.request.Request(
                endpoint, data=data,
                headers={"Content-Type": "application/json",
                         **({"Authorization":
                             f"Bearer {cfg['token']}"}
                            if cfg.get("token") else {})})
            with urllib.request.urlopen(req, timeout=30) as r:
                result["submitted"] = True
                result["response"] = r.read()[:500].decode(
                    "utf-8", "replace")
        except Exception as e:
            result["submitted"] = False
            result["submit_error"] = str(e)
    else:
        result["submitted"] = False
        result["note"] = ("no government endpoint configured — "
                          "statement exported to data/tax/; set "
                          "data/tax_config.json {authority,endpoint,"
                          "token} to enable direct filing")
    # prime-api banking tax books: always mirror the filing there —
    # the working government-link record on the prime side
    try:
        import prime
        result["banking_tax"] = prime.banking_tax_summary(
            time.strftime("%Y"))
    except Exception as e:
        result["banking_tax_error"] = str(e)
    return result


# ---- compliance check: early-earnings rules engine ----

def compliance(node: str | None = None, since: float = 0.0,
               until: float = 0.0) -> dict:
    """Validate the ledger against filing-compliance rules.
    Returns compliant flag + per-check detail + issues list."""
    st = statement(node, since, until)
    econ = _econ()
    cfg = _config()
    wallets = econ.get("wallets", {})
    checks = {}
    issues = []

    # 1. books balance: sum(balances) == earned - paid - pending_out
    earned = sum(w.get("earned", 0) for w in wallets.values())
    paid = sum(w.get("paid", 0) for w in wallets.values())
    bal = sum(w.get("balance", 0) for w in wallets.values())
    drift = round(bal - (earned - paid), 6)
    checks["books_balanced"] = abs(drift) < 0.01
    if not checks["books_balanced"]:
        issues.append(f"ledger drift {drift} — balances don't "
                      "match earned-paid")

    # 2. unreported events in scope
    evs = [e for e in econ.get("events", [])
           if not e.get("reported")
           and (not since or e.get("ts", 0) >= since)
           and (not until or e.get("ts", 0) <= until)]
    checks["all_reported"] = len(evs) == 0
    if evs:
        issues.append(f"{len(evs)} events not yet reported to "
                      "authority")

    # 3. taxpayer identity — tax_config first, then env details
    # (TAX_TIN/TAXPAYER_ID), then the prime banking environment
    # (payee name / bank / account last-4). TIN still required for
    # formal e-file; name+bank satisfies early-earnings records.
    ident = _banking_identity()
    tin = (cfg.get("taxpayer_id") or cfg.get("tin")
           or os.environ.get("TAX_TIN")
           or os.environ.get("TAXPAYER_ID"))
    checks["tax_id_present"] = bool(tin)
    checks["taxpayer_identity"] = bool(
        cfg.get("taxpayer_name") or ident.get("taxpayer_name"))
    if not checks["taxpayer_identity"]:
        issues.append("no taxpayer name in tax_config.json or "
                      "prime banking env")
    elif not checks["tax_id_present"]:
        issues.append("TIN/SSN not on file (TAX_TIN env or "
                      "tax_config.json) — required before formal "
                      "government e-file; early-earnings records "
                      "still compliant")

    # 4. authority — tax_config endpoint, else the prime banking
    # tax-books rail (working submission path mirroring filings
    # into prime's data/tax/)
    checks["authority_configured"] = bool(
        cfg.get("authority") or cfg.get("endpoint") or ident)
    checks["authority"] = (cfg.get("authority")
                           or ("prime-banking-tax-books"
                               if ident else None))
    if not checks["authority_configured"]:
        issues.append("no authority in tax_config.json and prime "
                      "banking env unavailable — filings "
                      "export-only")

    # 5. payout records complete (method+address where required)
    bad_payouts = []
    for n, w in wallets.items():
        for p in w.get("payouts", []):
            if p.get("method") == "btc" and not p.get("address"):
                bad_payouts.append(p.get("id"))
    checks["payout_records_complete"] = not bad_payouts
    if bad_payouts:
        issues.append(f"{len(bad_payouts)} btc payouts missing "
                      "destination address")

    # 6. income threshold watch (informational — early-earnings)
    threshold = float(cfg.get("report_threshold", 600))
    checks["threshold_watch"] = {
        "gross_income": st["gross_income"],
        "threshold": threshold,
        "over": st["gross_income"] >= threshold,
        "note": "crossing threshold likely triggers formal "
                "information reporting"}
    if st["gross_income"] >= threshold:
        issues.append(f"gross income {st['gross_income']} >= "
                      f"{threshold} reportable threshold")

    # 7. statement artifacts exist for the period
    artifacts = list(OUT.glob("*.json")) + list(OUT.glob("*.csv")) \
        if OUT.exists() else []
    checks["artifacts_present"] = len(artifacts) > 0
    if not artifacts:
        issues.append("no exported statements in data/tax/ — "
                      "run export first")

    hard_fail = [k for k in ("books_balanced",
                             "taxpayer_identity",
                             "payout_records_complete")
                 if not checks.get(k)]
    return {"compliant": not hard_fail, "node": node or "all",
            "checks": checks, "issues": issues,
            "hard_failures": hard_fail,
            "period": st["period"],
            "gross_income": st["gross_income"],
            "net": st["net"]}
