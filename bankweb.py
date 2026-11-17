"""bankweb — world banking + emerging-fintech web banking.

Harvests public pages from banking systems and emerging
payment/banking platforms around the globe into the
ATLAS stores. Each fetch is provenance-stamped (url,
status, sha256, title, text excerpt). Honest: records
failures and gates, never fabricates content.

  ingest()  — fetch all BANK_SOURCES, store records
  status()  — census of banked pages
"""

import hashlib
import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "bankweb"
PAGES = OUT / "pages.jsonl"

UA = {"User-Agent":
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml"}

# World banking systems + emerging fintech, grouped by
# region/system. Public homepages only — no auth, no
# gated endpoints.
BANK_SOURCES = [
    # Brazil — Pix / open banking
    {"name": "bcb_pix", "region": "south_america",
     "system": "central_bank",
     "url": "https://www.bcb.gov.br"},
    {"name": "nubank", "region": "south_america",
     "system": "neobank", "url": "https://nubank.com.br"},
    {"name": "inter", "region": "south_america",
     "system": "digital_bank", "url": "https://www.bancointer.com.br"},
    # India — UPI / account aggregator
    {"name": "npci_upi", "region": "south_asia",
     "system": "payment_rail",
     "url": "https://www.npci.org.in"},
    {"name": "paytm", "region": "south_asia",
     "system": "super_app_wallet",
     "url": "https://paytm.com"},
    {"name": "phonepe", "region": "south_asia",
     "system": "upi_app",
     "url": "https://www.phonepe.com"},
    # China — super-app wallets
    {"name": "alipay", "region": "east_asia",
     "system": "super_app_wallet",
     "url": "https://www.alipay.com"},
    {"name": "wechat_pay", "region": "east_asia",
     "system": "super_app_wallet",
     "url": "https://pay.weixin.qq.com"},
    # Southeast Asia — e-wallets
    {"name": "gcash", "region": "se_asia",
     "system": "e_wallet",
     "url": "https://www.gcash.com"},
    {"name": "maya", "region": "se_asia",
     "system": "e_wallet",
     "url": "https://www.maya.ph"},
    {"name": "grab_financial", "region": "se_asia",
     "system": "super_app_wallet",
     "url": "https://www.grab.com"},
    # Africa — mobile money
    {"name": "mpesa_safaricom", "region": "east_africa",
     "system": "mobile_money",
     "url": "https://www.safaricom.co.ke"},
    {"name": "mtn_momo", "region": "west_africa",
     "system": "mobile_money",
     "url": "https://www.mtn.com"},
    {"name": "wave", "region": "west_africa",
     "system": "mobile_money",
     "url": "https://www.wave.com"},
    {"name": "orange_money", "region": "west_africa",
     "system": "mobile_money",
     "url": "https://www.orange.com"},
    {"name": "ecocash", "region": "southern_africa",
     "system": "mobile_money",
     "url": "https://www.ecocash.co.zw"},
    {"name": "tymebank", "region": "southern_africa",
     "system": "digital_bank",
     "url": "https://www.tymebank.co.za"},
    # South Asia — mobile banking
    {"name": "bkash", "region": "south_asia",
     "system": "mobile_money",
     "url": "https://www.bkash.com"},
    # Central Asia
    {"name": "kaspi", "region": "central_asia",
     "system": "super_app",
     "url": "https://kaspi.kz"},
    # Europe / CIS
    {"name": "tbank", "region": "europe",
     "system": "digital_bank",
     "url": "https://www.tbank.ru"},
    {"name": "revolut", "region": "europe",
     "system": "neobank",
     "url": "https://www.revolut.com"},
    # North America — challenger + stablecoin rails
    {"name": "chime", "region": "north_america",
     "system": "neobank", "url": "https://www.chime.com"},
    {"name": "sofi", "region": "north_america",
     "system": "digital_bank",
     "url": "https://www.sofi.com"},
    # Nigeria / pan-African rails
    {"name": "flutterwave", "region": "west_africa",
     "system": "payment_aggregator",
     "url": "https://flutterwave.com"},
    {"name": "paystack", "region": "west_africa",
     "system": "payment_aggregator",
     "url": "https://paystack.com"},
    {"name": "interswitch", "region": "west_africa",
     "system": "payment_processor",
     "url": "https://interswitchgroup.com"},
    {"name": "yellowcard", "region": "africa",
     "system": "crypto_onramp",
     "url": "https://yellowcard.io"},
    # Global standards bodies
    {"name": "bis", "region": "global",
     "system": "central_bank_coop",
     "url": "https://www.bis.org"},
    {"name": "swift", "region": "global",
     "system": "messaging_network",
     "url": "https://www.swift.com"},
    {"name": "federal_reserve", "region": "north_america",
     "system": "central_bank",
     "url": "https://www.federalreserve.gov"},
    {"name": "ecb", "region": "europe",
     "system": "central_bank",
     "url": "https://www.ecb.europa.eu"},
]


def _now() -> dict:
    n = datetime.now().astimezone()
    return {"ts": n.timestamp(),
            "iso": n.isoformat(timespec="milliseconds"),
            "timezone": n.tzname() or "local",
            "date": n.date().isoformat()}


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>",
                  html, re.S | re.I)
    if not m:
        return ""
    return re.sub(r"\s+", " ", re.sub(
        r"<[^>]+>", "", m.group(1))).strip()[:200]


def _text(html: str, limit: int = 3000) -> str:
    body = re.sub(r"(?is)<(script|style|noscript).*?</\1>",
                  " ", html)
    txt = re.sub(r"<[^>]+>", " ", body)
    txt = re.sub(r"&[a-z#0-9]+;", " ", txt)
    return re.sub(r"\s+", " ", txt).strip()[:limit]


def _fetch_jina(src: dict) -> dict:
    """Fallback backend: r.jina.ai reader proxy fetches
    the page server-side and returns markdown. Used when
    the direct fetch hits a bot-wall (403). Provenance
    records backend=jina_reader."""
    u = ("https://r.jina.ai/" + src["url"])
    try:
        req = urllib.request.Request(u, headers={
            "User-Agent": UA["User-Agent"],
            "Accept": "text/plain"})
        with urllib.request.urlopen(req, timeout=45)                 as r:
            raw = r.read()
            text = raw.decode("utf-8", "replace")
            if len(text) < 200:
                return {"ok": False,
                        "error": "jina_empty",
                        "backend": "jina_reader"}
            return {"status": 200, "ok": True,
                    "final_url": src["url"],
                    "sha256": hashlib.sha256(raw)
                    .hexdigest()[:16],
                    "bytes": len(raw),
                    "title": text.split("\n")[0]
                    .replace("Title:", "").strip()[:200],
                    "text": text[:6000],
                    "backend": "jina_reader"}
    except Exception as e:
        return {"ok": False,
                "error": "jina:" + str(e)[:150],
                "backend": "jina_reader"}


def _fetch(src: dict) -> dict:
    try:
        req = urllib.request.Request(src["url"], headers=UA)
        with urllib.request.urlopen(req, timeout=20) \
                as r:
            raw = r.read()
            html = raw.decode("utf-8", "replace")
            return {"status": r.status,
                    "final_url": r.geturl(),
                    "sha256": hashlib.sha256(raw)
                    .hexdigest()[:16],
                    "bytes": len(raw),
                    "title": _title(html),
                    "text": _text(html),
                    "ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def ingest(refresh: bool = False) -> dict:
    """Fetch every BANK_SOURCES page once (or re-fetch
    all when refresh=True). Records land append-only
    with full provenance."""
    OUT.mkdir(parents=True, exist_ok=True)
    done = set()
    if not refresh and PAGES.exists():
        for r in _jsonl(PAGES):
            if r.get("ok"):
                done.add(r["name"])
    n_new = n_fail = 0
    with PAGES.open("a") as f:
        for src in BANK_SOURCES:
            if src["name"] in done:
                continue
            res = _fetch(src)
            if not res.get("ok"):
                fb = _fetch_jina(src)
                if fb.get("ok"):
                    fb["direct_error"] = res.get("error")
                    res = fb
            rec = {"kind": "bankweb_page",
                   "device_time": _now(),
                   "name": src["name"],
                   "region": src["region"],
                   "system": src["system"],
                   "url": src["url"],
                   "derived": True,
                   **res,
                   "note": ("public homepage harvest — "
                            "provenance-stamped, no auth")}
            f.write(json.dumps(rec, ensure_ascii=False)
                    + "\n")
            n_new += 1
            n_fail += 0 if res.get("ok") else 1
            time.sleep(0.5)
    return {"kind": "bankweb_ingest",
            "device_time": _now(),
            "sources": len(BANK_SOURCES),
            "new": n_new, "failed": n_fail,
            "out": str(PAGES.relative_to(ROOT))}


def status() -> dict:
    # latest record per source name wins (retries
    # supersede earlier failures)
    latest = {}
    for r in _jsonl(PAGES) or []:
        latest[r.get("name")] = r
    recs = list(latest.values())
    ok = [r for r in recs if r.get("ok")]
    regions = {}
    systems = {}
    for r in ok:
        regions[r["region"]] = regions.get(
            r["region"], 0) + 1
        systems[r["system"]] = systems.get(
            r["system"], 0) + 1
    fails = [r["name"] for r in recs if not r.get("ok")]
    return {"kind": "bankweb_state",
            "device_time": _now(),
            "space": "world_banking_web",
            "pages": len(recs), "ok": len(ok),
            "failed": fails,
            "regions": regions, "systems": systems}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "ingest":
        print(json.dumps(ingest(
            refresh="--refresh" in sys.argv),
            indent=1))
    else:
        print(json.dumps(status(), indent=1))
