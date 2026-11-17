"""parsecoss — Parsec open-source ingest.

Pulls the public Parsec OSS surface into ATLAS —
three products share the name, all banked honestly:

  parsec.app        — MalfoyJW/parsec-sdk (C+Python
                      game-streaming SDK) + c272/parsec-py
                      (BSD-3 wire-protocol client)
  parsec.finance    — parsec-finance/parsec_python_sdk
                      (crypto/data GraphQL endpoints)
  parsecapi.com     — parsecular/sdk-python (Apache-2.0
                      prediction-markets SDK: markets,
                      orders, orderbook, ctf, wallet)

Raw files land under data/sea/parsec_oss/<repo>/<path>
verbatim with sha256 manifest; derived vocab goes to
termlib pack parsec_oss; gaster observation reports mark
the ingest; familytree gains a hidden 'parsec' branch
(vanitas mechanism — nodes emerge via summon only).

Public GitHub only — no auth, no gated content.

  ingest()  — fetch + store + derive
  status()  — what's banked
"""

import hashlib
import json
import re
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SEA = DATA / "sea"
OSS = SEA / "parsec_oss"
TERMLIB = DATA / "termlib"
OUT = DATA / "parsecoss"
MANIFEST = OUT / "manifest.jsonl"
UA = {"User-Agent": "atlas-docs/1.0 (local research)",
      "Accept": "application/vnd.github+json"}
RAW_UA = {"User-Agent": "atlas-docs/1.0 (local research)"}

REPOS = {
    "parsec-sdk": "MalfoyJW/parsec-sdk",
    "parsec-py": "c272/parsec-py",
    "parsecfi": "parsec-finance/parsec_python_sdk",
    "parsec_api": "parsecular/sdk-python",
}

# files worth banking per repo (docs + python surface +
# wire protocol defs — skip binary/assets/examples noise)
WANT = {
    "parsec-sdk": [
        "README.md", "LICENSE.md",
        "api/public/README.md", "api/public/api.py",
        "api/public/auth-codes.py",
        "api/public/auth-sessions.py",
        "api/public/host-invites.py",
        "api/public/hosts.py",
        "api/public/login.py",
        "api/enterprise/README.md",
        "api/enterprise/api.py",
    ],
    "parsec-py": [
        "README.md", "LICENSE", "Makefile",
        "parsec.py", "parsec_stream.py",
        "parsec_message.py", "parsec_enums.py",
        "parsec_header.py", "key_attributes.py",
        "__init__.py",
        "operations/ping.py",
        "operations/psa_export_key.py",
        "operations/psa_export_public_key.py",
        "operations/psa_generate_key.py",
        "operations/psa_sign_hash.py",
        "operations/psa_verify_hash.py",
    ],
    "parsecfi": [
        "README.md",
        "parsecfi/__init__.py",
        "parsecfi/parsecfi.py",
        "parsecfi/lib/__init__.py",
        "parsecfi/lib/endpoints.py",
        "examples/friend_tech.py",
    ],
    "parsec_api": [
        "README.md", "api.md", "CHANGELOG.md",
        "src/parsec_api/__init__.py",
        "src/parsec_api/_client.py",
        "src/parsec_api/_models.py",
        "src/parsec_api/resources/__init__.py",
        "src/parsec_api/resources/markets.py",
        "src/parsec_api/resources/orders.py",
        "src/parsec_api/resources/orderbook.py",
        "src/parsec_api/resources/positions.py",
        "src/parsec_api/resources/fills.py",
        "src/parsec_api/resources/trades.py",
        "src/parsec_api/resources/wallet.py",
        "src/parsec_api/resources/ctf.py",
        "src/parsec_api/resources/exchanges.py",
        "src/parsec_api/resources/events.py",
        "src/parsec_api/resources/websocket.py",
        "src/parsec_api/resources/builder/builder.py",
        "src/parsec_api/resources/builder/escrow.py",
    ],
}


def _now() -> dict:
    n = datetime.now().astimezone()
    return {"ts": n.timestamp(),
            "iso": n.isoformat(timespec="milliseconds"),
            "timezone": n.tzname() or "local",
            "date": n.date().isoformat()}


def _get(url: str, headers: dict = UA) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(req, timeout=25).read()


def _tree(repo: str) -> list[str]:
    d = json.loads(_get(
        f"https://api.github.com/repos/{repo}"
        "/git/trees/HEAD?recursive=1"))
    return [x["path"] for x in d.get("tree", [])
            if x["type"] == "blob"]


def ingest() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    stored, missing, terms = [], [], set()

    for short, repo in REPOS.items():
        try:
            have = set(_tree(repo))
        except Exception as e:
            missing.append({"repo": repo,
                            "error": str(e)[:120]})
            continue
        want = [w for w in WANT[short] if w in have]
        for w in WANT[short]:
            if w not in have:
                missing.append({"repo": repo, "path": w,
                                "reason": "not_in_tree"})
        # also bank the vocabulary-bearing dirs —
        # wire protocol, sdk endpoints, api resources:
        # the payload is in the defs, not the examples
        extra = [p for p in have
                 if (p.startswith("protobuf/") and
                     p.endswith(".proto")) or
                 (p.startswith("operations/") and
                  p.endswith(".py")) or
                 (p.startswith("parsecfi/lib/"
                               "endpoints_list/") and
                  p.endswith(".py")) or
                 (p.startswith("src/parsec_api/resources/")
                  and p.endswith(".py")) or
                 (p.startswith("src/parsec_api/types/") and
                  p.endswith(".py"))]
        for path in sorted(set(want) | set(extra)):
            url = ("https://raw.githubusercontent.com/"
                   f"{repo}/HEAD/{path}")
            try:
                body = _get(url, RAW_UA)
            except Exception as e:
                missing.append({"repo": repo, "path": path,
                                "reason": str(e)[:100]})
                continue
            text = body.decode("utf-8", "replace")
            sha = hashlib.sha256(body).hexdigest()
            dest = OSS / short / path
            dest.parent.mkdir(parents=True,
                              exist_ok=True)
            dest.write_bytes(body)
            stored.append({
                "repo": repo, "path": path,
                "bytes": len(body), "sha256": sha,
                "device_time": _now()})
            # derive vocabulary from python/proto/md
            if path.endswith((".py", ".proto", ".md")):
                for tok in re.findall(
                        r"[a-z][a-z0-9_]{3,}",
                        text.lower()):
                    if tok not in ("self", "none", "true",
                                   "false", "param", "return",
                                   "import", "class", "def"):
                        terms.add(tok)

    # manifest — every stored file, honest about misses
    with MANIFEST.open("a") as f:
        for s in stored:
            f.write(json.dumps(
                {"kind": "oss_file", **s,
                 "derived": True}, ensure_ascii=False)
                + "\n")
        f.write(json.dumps(
            {"kind": "oss_ingest",
             "device_time": _now(),
             "repos": list(REPOS.values()),
             "files_stored": len(stored),
             "missing": missing,
             "derived": True}, ensure_ascii=False) + "\n")

    # sea blocks for the two READMEs — raw preserved
    import sea
    for short in REPOS:
        rd = OSS / short / "README.md"
        if rd.exists():
            sea.add(f"parsec_{short}_readme",
                    rd.read_text(errors="replace"),
                    families={"parsec": ["oss", "readme",
                                         short]},
                    note=f"raw README from {REPOS[short]}")

    # termlib pack — wire-protocol + sdk vocabulary
    focus = {t for t in terms if any(
        k in t for k in ("parsec", "psa_", "attest",
                         "opcode", "proto", "host",
                         "guest", "peer", "stream",
                         "warp", "relay", "invite",
                         "auth", "session", "claim",
                         "encoder", "decoder", "nal"))}
    pack = {"kind": "termlib_pack",
            "name": "parsec_oss",
            "seed": "parsec_oss",
            "device_time": _now(),
            "families": {
                "repos": list(REPOS.values()),
                "files": [s["path"] for s in stored],
                "wire_terms": sorted(focus)[:400]},
            "derived": True,
            "note": ("derived from Parsec OSS repos — "
                     "public GitHub sources only")}
    (TERMLIB / "parsec_oss.json").write_text(
        json.dumps(pack, ensure_ascii=False, indent=2))

    # register wire/sdk terms
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
        for t in sorted(focus):
            if t in existing:
                continue
            f.write(json.dumps(
                {"ts": _now()["ts"], "kind": "term",
                 "term": t, "seed": "parsec_oss",
                 "category": "coined_parsec",
                 "derived": True},
                ensure_ascii=False) + "\n")
            n_reg += 1

    # gaster logic — observation reports, REPORT !=
    # COMMAND: the ingest is observed, not commanded
    reports = []
    try:
        import gaster
        facts = [
            {"kind": "repos", "values":
             list(REPOS.values())},
            {"kind": "files_stored",
             "value": len(stored)},
            {"kind": "wire_terms", "value": len(focus)},
            {"kind": "missing", "value": len(missing)},
        ]
        rep = gaster._report(
            "parsec_oss_ingest", facts,
            "OBSERVED — public Parsec OSS surface banked; "
            "wire protocol vocabulary extracted; gaps "
            "recorded honestly")
        reports.append(rep.get("rid") or rep["report"])
    except Exception as e:
        reports.append({"error": str(e)[:100]})

    # vanitas mechanism — hidden familytree branch:
    # nodes exist but only emerge via summon path.
    # The branch is persisted in familytree.py source;
    # this runtime pass is an idempotent ensure.
    try:
        import familytree
        familytree.ensure_parsec_branch()
    except Exception as e:
        reports.append({"familytree_error":
                        str(e)[:100]})

    return {"kind": "parsec_oss_ingest",
            "device_time": _now(),
            "files_stored": len(stored),
            "repos": list(REPOS.values()),
            "wire_terms": len(focus),
            "terms_registered": n_reg,
            "missing": missing[:10],
            "gaster_reports": reports,
            "sea_blocks": [f"parsec_{s}_readme"
                           for s in REPOS],
            "termlib_pack": "parsec_oss"}


def status() -> dict:
    recs = []
    if MANIFEST.exists():
        for l in MANIFEST.open():
            try:
                recs.append(json.loads(l))
            except Exception:
                pass
    files = [r for r in recs
             if r.get("kind") == "oss_file"]
    return {"kind": "parsec_oss_status",
            "device_time": _now(),
            "files": len(files),
            "ingests": sum(1 for r in recs
                           if r.get("kind") ==
                           "oss_ingest"),
            "repos": sorted({r.get("repo")
                             for r in files})}
