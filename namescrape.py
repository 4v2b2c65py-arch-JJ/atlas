"""Global name-identity scraper -> GGUF dataset store.

Sources (all public, no auth):
- US SSA baby names national + state files (download.ssa.gov zip)
- Wikidata SPARQL: given-name entities ranked by sitelinks
- Forebears.io: per-country first-name + surname frequency tables

Raw records land in data/names/*.jsonl; the packed dataset is
written as a real GGUF container (magic GGUF v3, metadata KV
strings) at data/names/identities.gguf — readable by any gguf
parser, loadable as model-side dataset.
"""

import io
import json
import re
import struct
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "names"
GGUF_PATH = OUT / "identities.gguf"
UA = {"User-Agent": "atlas-namescrape/1.0 (identity research)"}
_last = [0.0]
MIN_INTERVAL = 1.1


def _get(url: str, timeout: int = 60, binary: bool = False):
    wait = MIN_INTERVAL - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    _last[0] = time.time()
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    return data if binary else data.decode("utf-8", "replace")


# ---------- GGUF writer (v3, metadata-only container) ----------

def _gstr(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<Q", len(b)) + b


GGUF_TYPES = {"uint32": 4, "int32": 5, "float32": 6, "bool": 7,
              "string": 8, "array": 9, "uint64": 10, "int64": 11,
              "float64": 12}


def _kv(key: str, vtype: str, value) -> bytes:
    out = _gstr(key) + struct.pack("<I", GGUF_TYPES[vtype])
    if vtype == "string":
        return out + _gstr(str(value))
    if vtype == "uint64":
        return out + struct.pack("<Q", int(value))
    if vtype == "uint32":
        return out + struct.pack("<I", int(value))
    raise ValueError(vtype)


def write_gguf(records: list[dict], path: Path = GGUF_PATH,
               chunk: int = 400) -> dict:
    """Pack identity records into a GGUF container: each chunk is a
    metadata string value (JSON array). tensor_count = 0."""
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = [
        _kv("general.name", "string", "atlas-identities"),
        _kv("general.type", "string", "dataset"),
        _kv("atlas.record_count", "uint64", len(records)),
        _kv("atlas.sources", "string",
            ",".join(sorted({r.get("src", "?") for r in records}))),
    ]
    chunks = [records[i:i + chunk] for i in range(0, len(records),
                                                   chunk)]
    meta += [_kv(f"atlas.chunk.{i:05d}", "string",
                 json.dumps(c, ensure_ascii=False))
             for i, c in enumerate(chunks)]
    meta += [_kv("atlas.chunk_count", "uint64", len(chunks))]
    buf = io.BytesIO()
    buf.write(b"GGUF")
    buf.write(struct.pack("<I", 3))            # version
    buf.write(struct.pack("<Q", 0))            # tensor_count
    buf.write(struct.pack("<Q", len(meta)))    # metadata_kv_count
    for m in meta:
        buf.write(m)
    path.write_bytes(buf.getvalue())
    return {"path": str(path), "bytes": path.stat().st_size,
            "records": len(records), "chunks": len(chunks)}


def read_gguf_count(path: Path = GGUF_PATH) -> int:
    "Verify: read header, count metadata kvs, return record count."""
    b = path.read_bytes()
    assert b[:4] == b"GGUF", "bad magic"
    ver, tcount, kcount = struct.unpack_from("<IQQ", b, 4)
    return {"version": ver, "tensors": tcount, "metadata_kvs": kcount}


# ---------- sources ----------

def scrape_ssa() -> list[dict]:
    """US SSA baby-name files via Wayback (ssa.gov 403s bots).
    yobYYYY.txt = national counts per year; aggregated to one
    record per (name, sex) with peak count + years seen."""
    url = ("https://web.archive.org/web/20261102233045id_/"
           "https://www.ssa.gov/oact/babynames/names.zip")
    z = zipfile.ZipFile(io.BytesIO(_get(url, timeout=300,
                                       binary=True)))
    agg = {}
    for name in z.namelist():
        m = re.match(r"(?:yob)?(\d{4})\.txt$|National\.txt$", name)
        if not m:
            continue
        year = m.group(1) or "all"
        for line in z.read(name).decode("utf-8", "replace").splitlines():
            p = line.split(",")
            if len(p) == 3 and p[0].isalpha():
                nm, sx, ct = (p[0].strip().lower(), p[1],
                              int(p[2]))
                k = (nm, sx)
                cur = agg.get(k)
                if cur is None or ct > cur["count"]:
                    agg[k] = {"name": nm, "sex": sx, "count": ct,
                              "peak_year": year, "years": 1,
                              "scope": "US", "src": "ssa"}
                elif cur:
                    cur["years"] += 1
    return list(agg.values())


def scrape_wikidata_kind(class_qid: str, prop: str, kind: str,
                         limit: int = 80000) -> list[dict]:
    """Name entities of a class (given Q202444 / family Q101352)
    with enwiki articles — the WDQS-stable pattern."""
    q = urllib.parse.quote(f"""
    SELECT ?item ?itemLabel (COUNT(DISTINCT ?article) AS ?sitelinks)
    WHERE {{
      ?item wdt:P31 wd:{class_qid} .
      ?article schema:about ?item ;
               schema:isPartOf <https://en.wikipedia.org/> .
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }} GROUP BY ?item ?itemLabel
    ORDER BY DESC(?sitelinks) LIMIT {limit}
    """)
    url = ("https://query.wikidata.org/sparql?query=" + q +
           "&format=json")
    data = None
    for attempt in range(4):
        req = urllib.request.Request(url, headers={
            **UA, "Accept": "application/sparql-results+json"})
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                data = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(65 if e.code == 429 else 20)
                continue
            raise
    if data is None:
        raise RuntimeError("wikidata sparql failed")
    recs = []
    for b in data["results"]["bindings"]:
        label = b["itemLabel"]["value"]
        if re.fullmatch(r"Q\d+", label):
            continue
        recs.append({
            "name": label.lower(),
            "sitelinks": int(b["sitelinks"]["value"]),
            "qid": b["item"]["value"].rsplit("/", 1)[-1],
            "kind": kind, "src": "wikidata"})
    return recs


def scrape_wikidata(limit: int = 60000) -> list[dict]:
    return scrape_wikidata_kind("Q202444", "P735", "given", limit)


def scrape_wikidata_family(limit: int = 60000) -> list[dict]:
    return scrape_wikidata_kind("Q101352", "P734", "family", limit)


def _xlsx_rows(data: bytes) -> list[list[str]]:
    """Minimal xlsx reader: shared strings + first sheet rows."""
    z = zipfile.ZipFile(io.BytesIO(data))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        xml = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
        shared = re.findall(r"<t[^>]*>([^<]*)</t>", xml)
    sheet = z.read("xl/worksheets/sheet1.xml").decode(
        "utf-8", "replace")
    rows = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S):
        cells = []
        for c in re.findall(
                r'<c[^>]*?(?:\s+t="(\w+)")?[^>]*>(?:<v>([^<]*)</v>)?',
                row):
            t, v = c
            cells.append(shared[int(v)] if t == "s" and v else v)
        rows.append(cells)
    return rows


def scrape_census_surnames() -> list[dict]:
    """US Census 2010 surnames (~162k) with ethnicity breakdown."""
    url = ("https://www2.census.gov/topics/genealogy/"
           "2010surnames/names.zip")
    z = zipfile.ZipFile(io.BytesIO(_get(url, timeout=120,
                                       binary=True)))
    csv_name = next(n for n in z.namelist() if n.endswith(".csv"))
    text = z.read(csv_name).decode("utf-8", "replace")
    recs = []
    for r in [l.split(",") for l in text.splitlines()[1:]]:
        # name,rank,count,prop100k,cum,pctwhite,pctblack,pctapi,
        # pctaian,pct2prace,pcthispanic
        if len(r) >= 4 and r[0].strip().isalpha():
            recs.append({"name": r[0].strip().lower(), "kind":
                         "family", "count": int(r[2]),
                         "rank": int(r[1]), "prop100k": r[3],
                         "pct_hispanic": r[10] if len(r) > 10 else "",
                         "scope": "US-2010", "src": "census"})
    return recs


FOREBEARS_COUNTRIES = [
    "united-states", "united-kingdom", "canada", "australia",
    "germany", "france", "spain", "italy", "netherlands", "sweden",
    "norway", "denmark", "finland", "poland", "india", "china",
    "japan", "brazil", "mexico", "argentina", "philippines",
    "indonesia", "turkey", "russia", "ukraine", "south-africa",
    "nigeria", "egypt", "saudi-arabia", "united-arab-emirates",
    "south-korea", "vietnam", "thailand", "malaysia", "singapore",
    "hong-kong", "new-zealand", "ireland", "belgium", "switzerland",
    "austria", "portugal", "greece", "czech-republic", "hungary",
]


def scrape_forebears(per_country: int = 300) -> list[dict]:
    """Forebears first-name + surname frequency tables for top
    countries — global-scale coverage."""
    recs = []
    row_re = re.compile(
        r'<a href="/([a-z-]+)/first-names/([^"]+)">(.*?)</a>.*?'
        r'(\d[\d,]*)', re.S)
    for cc in FOREBEARS_COUNTRIES:
        for kind in ("first-names", "surnames"):
            try:
                html = _get(f"https://forebears.io/{cc}/{kind}",
                            timeout=30)
            except Exception:
                continue
            # forebears rows: rank, name link, incidence/100k
            for m in re.finditer(
                    r'<td[^>]*>\s*(\d+)\s*</td>\s*<td[^>]*>\s*'
                    r'<a[^>]*>([^<]+)</a>\s*</td>\s*<td[^>]*>\s*'
                    r'([\d.,]+)', html):
                rank, name, freq = m.groups()
                if int(rank) > per_country:
                    continue
                recs.append({
                    "name": name.strip().lower(), "kind":
                    "given" if kind == "first-names" else "family",
                    "rank": int(rank), "freq_per_100k": freq,
                    "scope": cc, "src": "forebears"})
                if len([r for r in recs
                        if r["scope"] == cc
                        and r["kind"] == ("given" if kind ==
                                          "first-names" else
                                          "family")]) >= per_country:
                    break
    return recs


def run(forebears: bool = False) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    all_recs = []
    ssa_p = OUT / "ssa.jsonl"
    if not ssa_p.exists():
        ssa = scrape_ssa()
        ssa_p.write_text("\n".join(json.dumps(r) for r in ssa))
        print(f"ssa: {len(ssa)} records")
    else:
        ssa = [json.loads(l) for l in ssa_p.read_text().splitlines()]
        print(f"ssa (cached): {len(ssa)}")
    all_recs += ssa

    wd_p = OUT / "wikidata.jsonl"
    wd_fam_p = OUT / "wikidata_family.jsonl"
    cen_p = OUT / "census.jsonl"
    wd, wdf, cen = [], [], []
    if not wd_p.exists():
        try:
            wd = scrape_wikidata_kind("Q202444", "P735", "given")
            wd_p.write_text("\n".join(json.dumps(r) for r in wd))
        except Exception as e:
            print(f"wikidata given failed: {e}")
    else:
        wd = [json.loads(l) for l in wd_p.read_text().splitlines()]
    print(f"wikidata given: {len(wd)}")
    all_recs += wd

    if not wd_fam_p.exists():
        try:
            wdf = scrape_wikidata_kind("Q101352", "P734", "family",
                                       limit=30000)
            wd_fam_p.write_text("\n".join(json.dumps(r) for r in wdf))
        except Exception as e:
            print(f"wikidata family failed: {e}")
    else:
        wdf = [json.loads(l) for l in wd_fam_p.read_text().splitlines()]
    print(f"wikidata family: {len(wdf)}")
    all_recs += wdf

    if not cen_p.exists():
        try:
            cen = scrape_census_surnames()
            cen_p.write_text("\n".join(json.dumps(r) for r in cen))
        except Exception as e:
            print(f"census failed: {e}")
    else:
        cen = [json.loads(l) for l in cen_p.read_text().splitlines()]
    print(f"census surnames: {len(cen)}")
    all_recs += cen

    fb = []
    fb_p = OUT / "forebears.jsonl"
    if forebears and not fb_p.exists():
        fb = scrape_forebears()
        fb_p.write_text("\n".join(json.dumps(r) for r in fb))
        print(f"forebears: {len(fb)}")
    elif fb_p.exists():
        fb = [json.loads(l) for l in fb_p.read_text().splitlines()]
        print(f"forebears (cached): {len(fb)}")
        all_recs += fb
    else:
        all_recs += fb

    # dedupe: keep richest record per (name,kind,scope)
    seen, dedup = set(), []
    for r in all_recs:
        k = (r.get("name"), r.get("kind", "?"), r.get("scope", "?"),
             r.get("src"))
        if k not in seen:
            seen.add(k)
            dedup.append(r)

    res = write_gguf(dedup)
    res["verify"] = read_gguf_count(GGUF_PATH)
    res["sources"] = {"ssa": len(ssa), "wikidata_given": len(wd),
                      "wikidata_family": len(wdf), "census": len(cen),
                      "forebears": len(fb),
                      "total_raw": len(all_recs),
                      "deduped": len(dedup)}
    (OUT / "report.json").write_text(json.dumps(res, indent=1))
    return res


_cache: dict = {}


def lookup(query: str, limit: int = 25) -> dict:
    """Search the scraped identity datasets by name."""
    q = query.strip().lower()
    if not q:
        return {"query": q, "hits": []}
    if "all" not in _cache:
        recs = []
        for f in OUT.glob("*.jsonl"):
            for line in f.read_text().splitlines():
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
        _cache["all"] = recs
    hits = [r for r in _cache["all"]
            if q in r.get("name", "")]
    hits.sort(key=lambda r: -(r.get("count") or 0)
              - (r.get("sitelinks") or 0))
    exact = [r for r in hits if r.get("name") == q]
    return {"query": q, "total": len(hits),
            "hits": (exact or hits)[:limit]}


def status() -> dict:
    rep = {}
    try:
        rep = json.loads((OUT / "report.json").read_text())
    except Exception:
        pass
    return {"gguf": str(GGUF_PATH),
            "gguf_bytes": GGUF_PATH.stat().st_size
            if GGUF_PATH.exists() else 0,
            "report": rep}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "lookup":
        print(json.dumps(lookup(" ".join(sys.argv[2:])), indent=1))
    else:
        print(json.dumps(run(), indent=1))
