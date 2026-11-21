"""libweb — multi-region library web banking.

Harvests public catalog/index pages from libraries and
text archives around the globe — old texts, transcriptions,
public-domain collections. Same provenance discipline as
bankweb: url/status/sha256/title/text excerpt per record,
failures recorded not hidden, jina_reader fallback for
bot-walls.

  ingest()  — fetch all LIB_SOURCES
  status()  — census by region/system
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
OUT = DATA / "libweb"
PAGES = OUT / "pages.jsonl"

UA = {"User-Agent":
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml"}

# Multi-region libraries + public-domain text archives.
LIB_SOURCES = [
    # global / multi-region
    {"name": "gutenberg", "region": "global",
     "system": "ebook_archive",
     "url": "https://www.gutenberg.org/browse/scores/top"},
    {"name": "internet_archive",
     "region": "global", "system": "digital_library",
     "url": "https://archive.org/details/texts"},
    {"name": "openlibrary", "region": "global",
     "system": "book_catalog",
     "url": "https://openlibrary.org"},
    {"name": "wikisource", "region": "global",
     "system": "transcription_wiki",
     "url": "https://en.wikisource.org"},
    {"name": "standard_ebooks", "region": "global",
     "system": "ebook_archive",
     "url": "https://standardebooks.org"},
    {"name": "hathitrust", "region": "global",
     "system": "digital_library",
     "url": "https://www.hathitrust.org"},
    {"name": "loc", "region": "north_america",
     "system": "national_library",
     "url": "https://www.loc.gov"},
    # europe
    {"name": "gallica_bnf", "region": "europe",
     "system": "national_library",
     "url": "https://gallica.bnf.fr"},
    {"name": "europeana", "region": "europe",
     "system": "cultural_aggregator",
     "url": "https://www.europeana.eu"},
    {"name": "ddb_germany", "region": "europe",
     "system": "national_library",
     "url": "https://www.deutsche-digitale-bibliothek.de"},
    {"name": "bibliotheque_nationale",
     "region": "europe", "system": "national_library",
     "url": "https://www.bnf.fr"},
    {"name": "rusneb_russia", "region": "europe",
     "system": "national_library",
     "url": "https://www.rsl.ru"},
    {"name": "bl_uk", "region": "europe",
     "system": "national_library",
     "url": "https://www.bl.uk"},
    # asia
    {"name": "ndl_japan", "region": "east_asia",
     "system": "national_library",
     "url": "https://www.ndl.go.jp"},
    {"name": "ncl_china", "region": "east_asia",
     "system": "national_library",
     "url": "http://www.nlc.cn"},
    {"name": "korea_nl", "region": "east_asia",
     "system": "national_library",
     "url": "https://www.nl.go.kr"},
    {"name": "sarit_india", "region": "south_asia",
     "system": "digital_library",
     "url": "https://sarit.india.gov.in"},
    {"name": "ignca", "region": "south_asia",
     "system": "cultural_archive",
     "url": "https://ignca.gov.in"},
    # africa / oceania / americas
    {"name": "nla_australia", "region": "oceania",
     "system": "national_library",
     "url": "https://www.nla.gov.au"},
    {"name": "natlib_sa", "region": "africa",
     "system": "national_library",
     "url": "https://www.nlsa.ac.za"},
    {"name": "bnf_portugal", "region": "europe",
     "system": "national_library",
     "url": "https://bnportugal.gov.pt"},
    {"name": "bibsys_chile", "region": "south_america",
     "system": "national_library",
     "url": "https://www.bibliotecanacionaldigital.gob.cl"},
    {"name": "bn_mexico", "region": "north_america",
     "system": "national_library",
     "url": "https://www.bibliotecanacionaldemexico.gob.mx"},
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


def _fetch(src: dict) -> dict:
    try:
        req = urllib.request.Request(src["url"], headers=UA)
        with urllib.request.urlopen(req, timeout=20) \
                as r:
            raw = r.read()
            html = raw.decode("utf-8", "replace")
            return {"status": r.status, "ok": True,
                    "final_url": r.geturl(),
                    "sha256": hashlib.sha256(raw)
                    .hexdigest()[:16],
                    "bytes": len(raw),
                    "title": _title(html),
                    "text": _text(html)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


def _fetch_jina(src: dict) -> dict:
    u = "https://r.jina.ai/" + src["url"]
    try:
        req = urllib.request.Request(u, headers={
            "User-Agent": UA["User-Agent"],
            "Accept": "text/plain"})
        with urllib.request.urlopen(req, timeout=45) \
                as r:
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


def ingest(refresh: bool = False) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    done = set()
    if not refresh and PAGES.exists():
        for r in _jsonl(PAGES):
            if r.get("ok"):
                done.add(r["name"])
    n_new = n_fail = 0
    with PAGES.open("a") as f:
        for src in LIB_SOURCES:
            if src["name"] in done:
                continue
            res = _fetch(src)
            if not res.get("ok"):
                fb = _fetch_jina(src)
                if fb.get("ok"):
                    fb["direct_error"] = res.get("error")
                    res = fb
            rec = {"kind": "libweb_page",
                   "device_time": _now(),
                   "name": src["name"],
                   "region": src["region"],
                   "system": src["system"],
                   "url": src["url"],
                   "derived": True,
                   **res,
                   "note": ("public library catalog "
                            "harvest — provenance "
                            "stamped, no auth")}
            f.write(json.dumps(rec, ensure_ascii=False)
                    + "\n")
            n_new += 1
            n_fail += 0 if res.get("ok") else 1
            time.sleep(0.5)
    return {"kind": "libweb_ingest",
            "device_time": _now(),
            "sources": len(LIB_SOURCES),
            "new": n_new, "failed": n_fail,
            "out": str(PAGES.relative_to(ROOT))}


def status() -> dict:
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
    return {"kind": "libweb_state",
            "device_time": _now(),
            "space": "world_libraries_web",
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
