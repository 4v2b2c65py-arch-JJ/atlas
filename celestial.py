"""sweep — combination-space web sweep for celestial body.

Every candidate entity/term from the key notes gets a
web query; results land in data/celestial/sweep.jsonl
with provenance (query, url, title, snippet, fetched
ts). Honest: records what resolves, what doesn't —
no fabricated hits.

  run()      — sweep all candidate terms
  status()   — sweep census
"""

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "celestial"
SWEEP = OUT / "sweep.jsonl"
UA = {"User-Agent":
      "atlas-sweep/1.0 (local research)"}


def _now() -> dict:
    n = datetime.now().astimezone()
    return {"ts": n.timestamp(),
            "iso": n.isoformat(timespec="milliseconds"),
            "timezone": n.tzname() or "local",
            "date": n.date().isoformat()}


def candidates() -> list[str]:
    """The combination space: every coined/entity term
    from celestial_key_notes families."""
    pack = json.loads(
        (ROOT / "data" / "termlib" /
         "celestial_key_notes.json").read_text())
    terms = []
    for fam in ("entities", "routing", "celestial",
                "cipher", "lore", "interface"):
        for t in pack["families"].get(fam, []):
            tl = t.lower()
            if tl.isalpha() and len(tl) >= 3 \
                    and tl not in terms:
                terms.append(tl)
    # multiword phrases worth querying as units
    phrases = ["celestial routing", "trak zone",
               "li snyder", "isla_m", "dus=dos=dous="
               "doz=dooz", "skill juggling jester",
               "monk sword vs assassin",
               "projection stars", "terrain port",
               "hydrogen routing", "serve dots",
               "comas dot coms", "wordpress "
               "continuity", "pink earth america"]
    return terms + phrases


def _bing_url(raw: str) -> str:
    """Unwrap Bing redirect: u=a1<base64url> -> real URL.
    href arrives HTML-escaped (&amp;) — unescape first."""
    import html as _h
    raw = _h.unescape(raw)
    q = urllib.parse.parse_qs(
        urllib.parse.urlparse(raw).query)
    u = (q.get("u") or [""])[0]
    if u.startswith("a1"):
        import base64
        try:
            pad = "=" * (-len(u[2:]) % 4)
            return base64.urlsafe_b64decode(
                u[2:] + pad).decode("utf-8", "replace")
        except Exception:
            pass
    return raw


def _ddg(query: str, limit: int = 5) -> list[dict]:
    """Bing scrape — b_algo results (DDG endpoints are
    walled; Brave 429s). NOTE: mkt/cc params trigger
    Bing's bot-shell page (b_no, zero results) — base
    URL is the only reliable form; locale drift is
    recorded honestly in records."""
    u = ("https://www.bing.com/search?q=" +
         urllib.parse.quote(query) + "&count=10")
    try:
        req = urllib.request.Request(
            u, headers={"User-Agent":
                        "Mozilla/5.0 (Macintosh; Intel "
                        "Mac OS X 10_15_7) "
                        "AppleWebKit/537.36"})
        with urllib.request.urlopen(req, timeout=20) \
                as r:
            html = r.read().decode("utf-8", "replace")
    except Exception as e:
        return [{"error": str(e)[:120]}]
    import html as _html
    out = []
    for m in re.finditer(
            r'<li class="b_algo".*?<h2[^>]*><a[^>]+href="'
            r'([^"]+)"[^>]*>(.*?)</a>.*?<p[^>]*>'
            r'(.*?)</p>', html, re.S):
        url = _bing_url(m.group(1))
        title = _html.unescape(re.sub(
            r"<[^>]+>", "", m.group(2))).strip()
        snip = _html.unescape(re.sub(
            r"<[^>]+>", "", m.group(3))).strip()[:300]
        if url.startswith("http") and \
                "bing.com" not in url:
            out.append({"title": title, "url": url,
                        "snippet": snip})
        if len(out) >= limit:
            break
    return out


def run(max_terms: int | None = None) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    terms = candidates()
    if max_terms:
        terms = terms[:max_terms]
    # dedupe against prior sweep
    done = set()
    if SWEEP.exists():
        for l in SWEEP.open():
            try:
                done.add(json.loads(l)["query"])
            except Exception:
                pass
    n_new = 0
    with SWEEP.open("a") as f:
        for q in terms:
            if q in done:
                continue
            res = _ddg(q)
            rec = {"kind": "sweep_result",
                   "device_time": _now(),
                   "query": q,
                   "backend": "bing",
                   "hits": len([r for r in res
                                if "url" in r]),
                   "results": res,
                   "derived": True,
                   "note": ("combination-space sweep — "
                            "public web only")}
            f.write(json.dumps(rec,
                               ensure_ascii=False) + "\n")
            n_new += 1
            time.sleep(0.4)  # be polite
    return {"kind": "sweep_run",
            "device_time": _now(),
            "terms": len(terms), "new": n_new,
            "out": str(SWEEP.relative_to(ROOT))}


def feed() -> dict:
    """Feed sweep results into model-facing stores:
    derived sea block (summary, provenance kept in
    sweep.jsonl) + namehall rebuild so queries, hit
    titles and domains enter the name query space.
    Raw notes untouched — everything here is derived."""
    recs = [json.loads(l) for l in SWEEP.open()] \
        if SWEEP.exists() else []
    domains = {}
    for r in recs:
        for h in r.get("results", []):
            m = re.match(r"https?://([^/]+)",
                         h.get("url") or "")
            if m:
                d = m.group(1)
                domains[d] = domains.get(d, 0) + 1
    top = sorted(domains.items(),
                 key=lambda kv: -kv[1])[:25]
    summary = {
        "kind": "celestial_sweep_summary",
        "device_time": _now(),
        "queries": len(recs),
        "with_hits": sum(1 for r in recs
                         if r.get("hits")),
        "total_hits": sum(r.get("hits", 0)
                          for r in recs),
        "top_domains": dict(top),
        "source": "data/celestial/sweep.jsonl",
        "derived": True,
        "note": ("derived summary of public-web sweep — "
                 "raw key notes preserved separately "
                 "in celestial body/key_notes_001.txt")}
    import sea
    sea.add("celestial_sweep", json.dumps(
        summary, ensure_ascii=False),
        families={"celestial": ["sweep", "web",
                                "derived"]},
        note="derived — not a raw block")
    nh = {"kind": "namehall_rebuild"}
    try:
        import namehall
        b = namehall.build()
        nh.update({"names": b.get("names",
                                  b.get("count", "?"))})
    except Exception as e:
        nh["error"] = str(e)[:120]
    return {"kind": "celestial_feed",
            "device_time": _now(),
            "sea_block": "celestial_sweep",
            "namehall": nh}


def status() -> dict:
    recs = [json.loads(l) for l in SWEEP.open()] \
        if SWEEP.exists() else []
    hits = sum(1 for r in recs if r.get("hits"))
    return {"kind": "sweep_state",
            "device_time": _now(),
            "queries": len(recs),
            "with_hits": hits,
            "no_hits": len(recs) - hits}
