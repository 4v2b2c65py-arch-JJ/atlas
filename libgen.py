"""libgen — Library Genesis (libgensis) mirror layer.

LibGen has no single URL — it lives on a rotating set of
mirrors. This module banks the known mirror URIs, probes
them live (status + latency per mirror), and searches
through whichever mirror answers first. Every probe and
result is provenance-stamped; dead mirrors stay listed
with their last-known status — the mirror web is data,
not a single endpoint.

  probe()    — check every mirror, record status/latency
  status()   — latest probe census
  search(q)  — query mirrors in order, return hits
"""

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "libgen"
MIRRORS_JSONL = OUT / "mirrors.jsonl"
PROBES = OUT / "probes.jsonl"

UA = {"User-Agent":
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36",
      "Accept": "text/html"}

# Mirror URIs — from public mirror directories and the
# libgen-cli source. Search mirrors serve search.php;
# download mirrors serve content. Kind is honest about
# which surface each URI exposes.
MIRRORS = [
    {"name": "libgen.li", "kind": "search+download",
     "url": "https://libgen.li"},
    {"name": "libgen.is", "kind": "search",
     "url": "https://libgen.is"},
    {"name": "libgen.rs", "kind": "search",
     "url": "https://libgen.rs"},
    {"name": "libgen.st", "kind": "search",
     "url": "https://libgen.st"},
    {"name": "libgen.gs", "kind": "portal",
     "url": "https://libgen.gs"},
    {"name": "libgen.la", "kind": "search",
     "url": "https://libgen.la"},
    {"name": "libgen.ee", "kind": "search",
     "url": "https://libgen.ee"},
    {"name": "libgen.gl", "kind": "search",
     "url": "https://libgen.gl"},
    {"name": "libgen.vg", "kind": "search",
     "url": "https://libgen.vg"},
    {"name": "libgen.bz", "kind": "search",
     "url": "https://libgen.bz"},
    {"name": "gen.lib.rus.ec", "kind": "search",
     "url": "http://gen.lib.rus.ec"},
    {"name": "library.lol", "kind": "download",
     "url": "https://library.lol"},
    {"name": "libgen.pm", "kind": "download",
     "url": "https://libgen.pm"},
    {"name": "annas_archive", "kind": "alternative",
     "url": "https://annas-archive.org"},
    {"name": "libgen.help", "kind": "directory",
     "url": "https://libgen.help"},
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


def _get(url: str, timeout: int = 15) -> dict:
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) \
                as r:
            raw = r.read(400_000)
            return {"ok": True, "status": r.status,
                    "final_url": r.geturl(),
                    "ms": int((time.time() - t0) * 1000),
                    "bytes": len(raw),
                    "title": _title(
                        raw.decode("utf-8", "replace")),
                    "html": raw.decode("utf-8", "replace")}
    except Exception as e:
        return {"ok": False,
                "error": str(e)[:160],
                "ms": int((time.time() - t0) * 1000)}


def seed() -> dict:
    """Write the mirror registry once (append-only —
    re-seeding adds nothing if names already exist)."""
    OUT.mkdir(parents=True, exist_ok=True)
    have = {r.get("name") for r in _jsonl(MIRRORS_JSONL)
            or []}
    n = 0
    with MIRRORS_JSONL.open("a") as f:
        for m in MIRRORS:
            if m["name"] in have:
                continue
            f.write(json.dumps({
                "kind": "libgen_mirror",
                "device_time": _now(),
                "name": m["name"], "kind_of": m["kind"],
                "url": m["url"], "derived": True,
                "note": ("Library Genesis mirror URI — "
                         "public mirror directories")},
                ensure_ascii=False) + "\n")
            n += 1
    return {"kind": "libgen_seed",
            "device_time": _now(),
            "mirrors": len(MIRRORS), "new": n}


def probe() -> dict:
    """Live-probe every mirror: HTTP status + latency.
    Records land append-only; dead mirrors keep their
    last status — the web is data."""
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    with PROBES.open("a") as f:
        for m in MIRRORS:
            r = _get(m["url"] + "/", timeout=15)
            rec = {"kind": "libgen_probe",
                   "device_time": _now(),
                   "name": m["name"],
                   "kind_of": m["kind"],
                   "url": m["url"],
                   "ok": r.get("ok"),
                   "status": r.get("status"),
                   "ms": r.get("ms"),
                   "title": r.get("title", ""),
                   "error": r.get("error"),
                   "derived": True}
            f.write(json.dumps(rec, ensure_ascii=False)
                    + "\n")
            results.append(rec)
            time.sleep(0.4)
    up = sum(1 for r in results if r.get("ok"))
    return {"kind": "libgen_probe_run",
            "device_time": _now(),
            "mirrors": len(MIRRORS),
            "up": up, "down": len(MIRRORS) - up,
            "results": results}


def status() -> dict:
    """Latest probe per mirror + registry census."""
    latest = {}
    for r in _jsonl(PROBES) or []:
        latest[r.get("name")] = r
    regs = list(_jsonl(MIRRORS_JSONL) or [])
    mirrors = []
    for m in MIRRORS:
        p = latest.get(m["name"], {})
        mirrors.append({
            "name": m["name"], "kind": m["kind"],
            "url": m["url"],
            "up": bool(p.get("ok")),
            "status": p.get("status"),
            "ms": p.get("ms"),
            "last_probe": p.get("device_time", {})
            .get("iso"),
            "error": p.get("error")})
    return {"kind": "libgen_state",
            "device_time": _now(),
            "space": "library_genesis",
            "alias": "libgensis",
            "mirrors": len(mirrors),
            "up": sum(1 for m in mirrors if m["up"]),
            "registry": len(regs),
            "list": mirrors}


def _parse_results(html: str, mirror: str) -> list:
    """LibGen search.php returns a table; book links look
    like book/index.php?md5=... — extract title+url pairs
    honestly (whatever the mirror actually returned)."""
    out = []
    for m in re.finditer(
            r'<a[^>]+href="([^"]*(?:book/index\.php|md5=)'
            r'[^"]*)"[^>]*>(.*?)</a>', html, re.S | re.I):
        url = m.group(1)
        if url.startswith("/"):
            url = "https://" + mirror + url
        title = re.sub(r"\s+", " ", re.sub(
            r"<[^>]+>", "", m.group(2))).strip()
        if title and len(title) > 3 and "md5" in url.lower() \
                or (title and "book/index" in url):
            out.append({"title": title[:200],
                        "url": url[:400],
                        "mirror": mirror})
        if len(out) >= 25:
            break
    return out


def search(query: str, limit: int = 25) -> dict:
    """Query mirrors in registry order; first mirror that
    returns parseable hits wins. All attempts recorded —
    honest about which mirror answered."""
    q = (query or "").strip()
    if not q:
        return {"error": "empty_query"}
    attempts = []
    for m in MIRRORS:
        if m["kind"] not in ("search", "search+download",
                             "portal"):
            continue
        url = (m["url"].rstrip("/") +
               "/search.php?req=" +
               urllib.parse.quote(q) +
               "&lg_topic=libgen&open=0&view=simple&"
               "res=25&phrase=1&column=def")
        r = _get(url, timeout=20)
        hits = _parse_results(r.get("html", ""),
                              m["name"]) \
            if r.get("ok") else []
        attempts.append({
            "mirror": m["name"], "ok": r.get("ok"),
            "status": r.get("status"),
            "ms": r.get("ms"),
            "hits": len(hits),
            "error": r.get("error")})
        if hits:
            return {"kind": "libgen_search",
                    "device_time": _now(),
                    "query": q,
                    "mirror": m["name"],
                    "hits": hits[:limit],
                    "attempts": attempts,
                    "note": ("first answering mirror wins — "
                             "all attempts recorded")}
        time.sleep(0.4)
    return {"kind": "libgen_search",
            "device_time": _now(),
            "query": q,
            "hits": [],
            "attempts": attempts,
            "note": ("no mirror returned parseable "
                     "results — recorded honestly")}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "seed":
        print(json.dumps(seed(), indent=1))
    elif len(sys.argv) > 1 and sys.argv[1] == "probe":
        print(json.dumps(probe(), indent=1)[:4000])
    elif len(sys.argv) > 2 and sys.argv[1] == "search":
        print(json.dumps(search(" ".join(sys.argv[2:])),
                         indent=1)[:4000])
    else:
        print(json.dumps(status(), indent=1)[:4000])
