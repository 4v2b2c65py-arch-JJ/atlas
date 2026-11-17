"""parsecdocs — Parsec Teams API documentation ingest.

parsec.app/docs/teams-api is a Next.js page whose OpenAPI
3.0 spec is embedded as JSON.parse('...') inside the page
chunk JS. This module extracts that spec verbatim, stores
it raw under data/sea/, and derives a termlib pack +
endpoint census so the model can query Parsec's API
surface like any other world data.

Public docs only — no auth, no gated content.

  ingest()  — fetch spec, store raw + derived records
  status()  — what's banked
"""

import ast
import hashlib
import json
import re
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SEA = DATA / "sea"
TERMLIB = DATA / "termlib"
OUT = DATA / "parsecdocs"
SPEC_JSONL = OUT / "spec_records.jsonl"
DOC_PAGE = "https://parsec.app/docs/teams-api"
UA = {"User-Agent": "atlas-docs/1.0 (local research)"}


def _now() -> dict:
    n = datetime.now().astimezone()
    return {"ts": n.timestamp(),
            "iso": n.isoformat(timespec="milliseconds"),
            "timezone": n.tzname() or "local",
            "date": n.date().isoformat()}


def _fetch_spec() -> dict:
    """Pull the page, find its page-chunk JS, extract the
    embedded OpenAPI JSON."""
    req = urllib.request.Request(DOC_PAGE, headers=UA)
    html = urllib.request.urlopen(req, timeout=25) \
        .read().decode("utf-8", "replace")
    chunk = re.search(
        r'src="([^"]*pages/docs/teams-api-[^"]+\.js)"',
        html)
    if not chunk:
        raise RuntimeError("teams-api chunk not found")
    js_url = "https://parsec.app" + chunk.group(1)
    js = urllib.request.urlopen(
        urllib.request.Request(js_url, headers=UA),
        timeout=25).read().decode("utf-8", "replace")
    m = re.search(r"JSON\.parse\('((?:[^'\\]|\\.)*)'\)",
                  js)
    if not m:
        raise RuntimeError("openapi JSON.parse not found")
    return json.loads(ast.literal_eval(
        "'" + m.group(1) + "'"))


def ingest() -> dict:
    """Fetch + store spec raw, then derive records."""
    spec = _fetch_spec()
    raw = json.dumps(spec, ensure_ascii=False,
                     indent=2)
    sha = hashlib.sha256(raw.encode()).hexdigest()

    # raw preserved verbatim
    OUT.mkdir(parents=True, exist_ok=True)
    SEA.mkdir(parents=True, exist_ok=True)
    (SEA / "parsec_teams_api.json").write_text(raw)
    TERMLIB.mkdir(parents=True, exist_ok=True)

    # endpoint census
    endpoints = []
    for path, ops in sorted(spec.get("paths", {})
                            .items()):
        for method, op in ops.items():
            if method.upper() == "PARAMETERS":
                continue
            endpoints.append({
                "method": method.upper(),
                "path": path,
                "operationId": op.get("operationId",
                                      ""),
                "summary": op.get("summary", "")})
    schemas = sorted(spec.get("components", {})
                     .get("schemas", {}).keys())

    rec = {"kind": "parsec_spec_ingest",
           "device_time": _now(),
           "source": DOC_PAGE,
           "spec_sha256": sha,
           "openapi": spec.get("openapi"),
           "title": spec.get("info", {}).get("title"),
           "version": spec.get("info", {}).get("version"),
           "endpoints": len(endpoints),
           "schemas": schemas,
           "derived": True,
           "note": ("OpenAPI spec extracted verbatim "
                    "from docs page chunk — public "
                    "documentation only")}
    with SPEC_JSONL.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False)
                + "\n")
        for e in endpoints:
            f.write(json.dumps(
                {"kind": "parsec_endpoint",
                 "device_time": _now(),
                 **e, "derived": True},
                ensure_ascii=False) + "\n")

    # sea block — raw spec text preserved
    import sea
    sea.add("parsec_teams_api", raw,
            families={"parsec": ["api", "openapi",
                                 "teams", "docs"]},
            note=("raw OpenAPI 3.0 spec from "
                  "parsec.app/docs/teams-api"))

    # termlib pack — derived vocabulary
    terms = set()
    for e in endpoints:
        for seg in e["path"].strip("/").split("/"):
            seg = seg.strip("{}")
            if seg and seg not in ("v1", "v2", "teams"):
                terms.add(seg.replace("-", "_"))
        terms.update(w for w in re.findall(
            r"[a-z]{4,}", e["summary"].lower()))
    terms.update(schemas)
    terms.update(["parsec", "teams_api", "guest_access",
                  "member_invites", "app_rules", "relays",
                  "openapi", "peer_to_peer", "warp"])
    pack = {"kind": "termlib_pack",
            "name": "parsec_api",
            "seed": "parsec_teams_api",
            "device_time": _now(),
            "families": {
                "resources": sorted(schemas),
                "endpoint_paths": sorted(
                    {e["path"] for e in endpoints}),
                "terms": sorted(terms)},
            "derived": True,
            "note": ("derived from Parsec Teams API "
                     "OpenAPI spec — public docs")}
    (TERMLIB / "parsec_api.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2))

    # register api terms in terms.jsonl (same record
    # shape termlib.ingest writes)
    tindex = TERMLIB / "terms.jsonl"
    existing = set()
    if tindex.exists():
        for l in tindex.open():
            try:
                existing.add(json.loads(l)["term"])
            except Exception:
                pass
    n_reg = 0
    with tindex.open("a") as f:
        for t in sorted(terms):
            if t in existing:
                continue
            f.write(json.dumps(
                {"ts": _now()["ts"], "kind": "term",
                 "term": t, "seed": "parsec_teams_api",
                 "category": "coined_parsec",
                 "derived": True},
                ensure_ascii=False) + "\n")
            n_reg += 1

    return {"kind": "parsec_ingest",
            "device_time": _now(),
            "spec_sha256": sha[:16],
            "endpoints": len(endpoints),
            "schemas": schemas,
            "terms": len(terms),
            "terms_registered": n_reg,
            "sea_block": "parsec_teams_api",
            "termlib_pack": "parsec_api"}


def status() -> dict:
    recs = []
    if SPEC_JSONL.exists():
        for l in SPEC_JSONL.open():
            try:
                recs.append(json.loads(l))
            except Exception:
                pass
    eps = [r for r in recs
           if r.get("kind") == "parsec_endpoint"]
    return {"kind": "parsec_status",
            "device_time": _now(),
            "ingests": sum(1 for r in recs
                           if r.get("kind") ==
                           "parsec_spec_ingest"),
            "endpoints": len(eps),
            "spec_file": str(SEA /
                             "parsec_teams_api.json"),
            "spec_exists": (SEA /
                            "parsec_teams_api.json") \
                .exists()}
