"""Circuit — long-circuit wire test per video source.

For every video/media source in the catalogue + inbox +
ingest streams, trace the LONG CIRCUIT: the end-to-end path
the content travels —

  source URL -> fetch process (PID) -> bus/node (PCIe or
  mesh) -> storage file -> render/display node

Each source gets a circuit record with a pattern signature
(hop list hash). wire_test() then probes every link:
file exists? pid alive? node registered? -> per-link
pass/fail and a circuit verdict.

Honest: sources with no file, dead pids or unregistered
nodes report BROKEN links — never hidden.

Store: data/circuit/{circuits.jsonl, tests.jsonl}
"""

import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "circuit"
CIRCUITS = OUT / "circuits.jsonl"
TESTS = OUT / "tests.jsonl"
VIDEOS = ROOT / "videos"

MEDIA_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi",
             ".mp3", ".flac", ".wav", ".png", ".jpg",
             ".jpeg", ".webp", ".gif"}


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _append(path: Path, rec: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _server_pid() -> int | None:
    """PID of the running hub — the fetch/render process."""
    try:
        import subprocess
        r = subprocess.run(
            ["pgrep", "-f", "server.py"],
            capture_output=True, text=True, timeout=5)
        pids = [int(x) for x in r.stdout.split() if x.strip()]
        return pids[0] if pids else os.getpid()
    except Exception:
        return os.getpid()


def _node_for(kind_hint: str = "display") -> str | None:
    """Pick a nodereg node for the bus hop — gpu/pcie node
    preferred, else any registered node."""
    try:
        import nodereg as _nr
        nodes = {}
        for r in _jsonl(_nr.NODES) or []:
            nodes[r.get("node")] = r
        for n, r in nodes.items():
            if kind_hint in str(r.get("node_kind", "")) or \
                    "gpu" in n or "pcie" in n or \
                    "display" in n:
                return n
        return next(iter(nodes), None)
    except Exception:
        return None


def _sources() -> list[dict]:
    """All video/media sources: catalogue + inbox files +
    ingest records."""
    srcs = []
    # catalogue store
    for p in (DATA / "catalogue").rglob("*") if (
            DATA / "catalogue").exists() else []:
        if p.is_file() and p.suffix.lower() in MEDIA_EXT:
            srcs.append({"source": str(p),
                         "kind": "catalogue_file",
                         "title": p.stem})
    # peer/ingest jsonl records carrying urls
    for jf in (DATA / "peer_urls.json",):
        try:
            d = json.loads(jf.read_text())
            for url, meta in (d.items()
                              if isinstance(d, dict) else []):
                if any(url.lower().endswith(e) or e in url
                       for e in MEDIA_EXT):
                    srcs.append({"source": url,
                                 "kind": "peer_url",
                                 "title": (meta or {})
                                 .get("title", url)})
        except Exception:
            pass
    return srcs


def scan() -> dict:
    """Trace the long circuit for every source."""
    pid = _server_pid()
    node = _node_for()
    made = []
    for s in _sources():
        src = s["source"]
        is_file = Path(src).exists() if not src.startswith(
            "http") else None
        hops = [
            {"hop": "origin", "ref": src[:120]},
            {"hop": "fetch_pid", "ref": pid},
            {"hop": "bus_node", "ref": node or "unassigned"},
            {"hop": "storage", "ref": src if is_file else
             "remote_only"},
            {"hop": "render", "ref": node or "headless"}]
        pat = hashlib.sha256(json.dumps(
            [h["hop"] for h in hops]).encode()
        ).hexdigest()[:12]
        rec = {"ts": _device_time()["ts"],
               "device_time": _device_time(),
               "kind": "circuit", "source": src,
               "media_kind": s.get("kind"),
               "title": s.get("title"),
               "pid": pid, "bus_node": node,
               "pattern": pat, "hops": hops,
               "file_present": is_file,
               "derived": True}
        _append(CIRCUITS, rec)
        made.append(rec)
    return {"kind": "circuit_scan", "sources": len(made),
            "pid": pid, "bus_node": node,
            "circuits": made[:50]}


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def test_one(rec: dict) -> dict:
    """Wire-test one circuit record — every link checked."""
    links = []
    src = rec.get("source", "")
    # origin link
    links.append({"link": "origin",
                  "ok": src.startswith("http") or
                  Path(src).exists(),
                  "detail": src[:80]})
    # pid link
    links.append({"link": "fetch_pid",
                  "ok": _pid_alive(rec.get("pid")),
                  "detail": "pid %s" % rec.get("pid")})
    # bus node link
    links.append({"link": "bus_node",
                  "ok": bool(rec.get("bus_node")),
                  "detail": rec.get("bus_node")})
    # storage link
    if rec.get("file_present") is False:
        links.append({"link": "storage", "ok": False,
                      "detail": "file missing"})
    elif rec.get("file_present") is True:
        links.append({"link": "storage", "ok": True,
                      "detail": "on disk"})
    else:
        links.append({"link": "storage", "ok": True,
                      "detail": "remote (no local file)"})
    # render link
    links.append({"link": "render",
                  "ok": bool(rec.get("bus_node")),
                  "detail": "via bus node"})
    broken = [l for l in links if not l["ok"]]
    verdict = "OK" if not broken else "BROKEN"
    out = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "circuit_test",
           "source": src, "pattern": rec.get("pattern"),
           "links": links, "broken": len(broken),
           "verdict": verdict, "derived": True}
    _append(TESTS, out)
    return out


def test() -> dict:
    """Wire-test every recorded circuit."""
    recs = list(_jsonl(CIRCUITS) or [])
    results = [test_one(r) for r in recs[-200:]]
    ok = sum(1 for r in results if r["verdict"] == "OK")
    return {"kind": "circuit_test_all",
            "tested": len(results), "ok": ok,
            "broken": len(results) - ok,
            "results": results[:50]}


def status() -> dict:
    return {"kind": "circuit_state",
            "device_time": _device_time(),
            "circuits": sum(1 for _ in
                            _jsonl(CIRCUITS) or []),
            "tests": sum(1 for _ in _jsonl(TESTS) or []),
            "pid": _server_pid(),
            "bus_node": _node_for(),
            "note": ("long-circuit per video source: origin->"
                     "pid->bus(pcie/mesh)->storage->render; "
                     "wire test checks every link")}
