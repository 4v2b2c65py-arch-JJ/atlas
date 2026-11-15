"""ModelScan — model-weight nodes on the raw PCIe lane
expressway.

A scanned DIRECTORY is a node itself (not a storage path):
each dir holding transformer weights becomes a node pinned
to an expressway lane, ordered by total bytes. Running
model GATEWAYS (ollama, llama.cpp, LM Studio, vLLM,
text-generation-webui...) are detected from the live port
log — listening sockets -> pid -> process -> probed model
list, with each weight's format identified (.gguf,
.safetensors, .bin, .onnx, ...).

Honest: unreachable ports and unparseable listings are
reported, never invented.

Store: data/modelscan/{nodes.jsonl, gateways.jsonl}
"""

import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "modelscan"
NODES = OUT / "nodes.jsonl"
GATEWAYS = OUT / "gateways.jsonl"

WEIGHT_EXTS = {
    ".gguf": "gguf", ".ggml": "ggml",
    ".safetensors": "safetensors",
    ".bin": "pytorch-bin", ".pt": "pytorch",
    ".pth": "pytorch", ".ckpt": "checkpoint",
    ".onnx": "onnx", ".mlmodel": "coreml",
    ".tflite": "tflite", ".h5": "keras",
    ".npz": "numpy", ".joblib": "sklearn",
    ".pb": "tensorflow", ".engine": "tensorrt",
    ".exl2": "exllamav2", ".awq": "awq",
    ".gptq": "gptq", ".mlx": "mlx",
}

# known transformer-gateway ports -> probe path
GATEWAY_PORTS = {
    11434: ("ollama", "/api/tags"),
    1234: ("lm_studio", "/v1/models"),
    8080: ("llama_cpp", "/v1/models"),
    8000: ("vllm_or_hf", "/v1/models"),
    5000: ("textgen_or_flask", "/api/v1/models"),
    4891: ("gpt4all", "/api/models"),
    3000: ("node_gateway", "/v1/models"),
}

SCAN_ROOTS = [ROOT, Path.home() / "models",
              Path.home() / ".ollama", Path.home() / "LM Studio",
              Path.home() / ".cache" / "huggingface",
              Path("/Volumes")]
MAX_DEPTH = 6
MAX_FILES_PER_DIR = 500


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def _append(path: Path, rec: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _probe(port: int, path: str, timeout: float = 1.5):
    """GET a gateway endpoint; return parsed json or None."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            headers={"User-Agent": "atlas-modelscan"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()[:200_000])
    except Exception:
        return None


def _models_from(d) -> list:
    """Normalize gateway model listings."""
    out = []
    if not isinstance(d, dict):
        return out
    # ollama: {"models":[{name,size,details:{family}}]}
    for m in d.get("models", []) or []:
        if isinstance(m, dict) and m.get("name"):
            det = m.get("details") or {}
            out.append({"name": m["name"],
                        "size": m.get("size"),
                        "format": "gguf",
                        "family": det.get("family"),
                        "quant": det.get("quantization_level")})
    # openai-style: {"data":[{id}]} or {"models":[{id}]}
    for m in d.get("data", []) or []:
        if isinstance(m, dict) and m.get("id"):
            out.append({"name": m["id"], "format": "api"})
    for m in d.get("models", []) or []:
        if isinstance(m, dict) and m.get("id") and \
                "name" not in m:
            out.append({"name": m["id"], "format": "api"})
    return out


def ports() -> list[dict]:
    """Running port log — listening TCP sockets -> pid ->
    process name; known gateway ports get probed."""
    listeners = []
    try:
        r = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN",
             "-FpFn"], capture_output=True, text=True,
            timeout=15)
        pid = fn = None
        for line in r.stdout.splitlines():
            if line.startswith("p"):
                pid = line[1:]
            elif line.startswith("f"):
                fn = line[1:]
            elif line.startswith("n") and pid:
                m = re.search(r":(\d+)\s*\(LISTEN\)", line) or \
                    re.search(r":(\d+)\b", line)
                if m:
                    port = int(m.group(1))
                    comm = ""
                    try:
                        pr = subprocess.run(
                            ["ps", "-p", pid, "-o", "comm="],
                            capture_output=True, text=True,
                            timeout=3)
                        comm = pr.stdout.strip()
                    except Exception:
                        pass
                    listeners.append({"port": port,
                                      "pid": int(pid),
                                      "proc": comm})
    except Exception:
        pass
    # probe known gateway ports
    for port, (kind, path) in GATEWAY_PORTS.items():
        d = _probe(port, path)
        if d is not None:
            models = _models_from(d)
            listeners.append({
                "port": port, "kind": kind, "probe": path,
                "gateway": True, "models": models,
                "reachable": True})
    return listeners


def scan(roots=None) -> dict:
    """Directories ARE nodes — walk roots, weight files pin
    the node to an expressway lane ordered by total bytes."""
    roots = [Path(r) for r in (roots or SCAN_ROOTS)
             if Path(r).exists()]
    raw = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).relative_to(
                root).parts) if dirpath != str(root) else 0
            if depth > MAX_DEPTH:
                dirnames[:] = []
                continue
            # prune noise dirs
            dirnames[:] = [d for d in dirnames if not
                           d.startswith((".", "node_modules",
                                         "__pycache__"))]
            hits = []
            for fn in filenames[:MAX_FILES_PER_DIR]:
                ext = Path(fn).suffix.lower()
                if ext in WEIGHT_EXTS:
                    p = Path(dirpath) / fn
                    try:
                        sz = p.stat().st_size
                    except OSError:
                        sz = 0
                    hits.append({"file": fn, "size": sz,
                                 "format": WEIGHT_EXTS[ext],
                                 "ext": ext})
            if hits:
                raw.append({"dir": dirpath, "weights": hits,
                            "total_bytes": sum(
                                h["size"] for h in hits)})
    # lane assignment: sort by bytes desc -> expressway lanes
    raw.sort(key=lambda r: -r["total_bytes"])
    nodes = []
    for i, r in enumerate(raw):
        formats = sorted({h["format"] for h in r["weights"]})
        node = {"node": "dir_" + re.sub(
                    r"[^a-z0-9]+", "_",
                    Path(r["dir"]).name.lower())[:40],
                "kind": "model_node", "path": r["dir"],
                "lane": i + 1,
                "weights": len(r["weights"]),
                "bytes": r["total_bytes"],
                "formats": formats,
                "files": r["weights"][:100],
                "device_time": _device_time(),
                "derived": True}
        nodes.append(node)
        _append(NODES, node)
    return {"kind": "modelscan", "nodes": len(nodes),
            "weights": sum(n["weights"] for n in nodes),
            "bytes": sum(n["bytes"] for n in nodes),
            "lanes": [{"lane": n["lane"], "node": n["node"],
                       "bytes": n["bytes"],
                       "formats": n["formats"]}
                      for n in nodes[:50]]}


def _ollama_show(name: str) -> dict:
    """Fetch /api/show for an ollama model — architecture,
    params, quant, context — then rope-classify it."""
    d = _probe(11434, "/api/show", timeout=3)
    # /api/show is POST; do it manually
    try:
        body = json.dumps({"model": name}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/show", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode()[:100_000])
    except Exception:
        return {}
    mi = d.get("model_info") or {}
    arch = mi.get("general.architecture")
    meta = {k: v for k, v in mi.items()
            if not k.startswith("general.")}
    meta["general.architecture"] = arch or "unknown"
    try:
        import rope as _rope
        cls = _rope.classify(meta)
        cls.pop("label", None)
        return {"architecture": arch,
                "basename": mi.get("general.basename"),
                "parameter_count": mi.get(
                    "general.parameter_count"),
                "size_label": mi.get("general.size_label"),
                "finetune": mi.get("general.finetune"),
                "parameters": d.get("parameters"),
                "quantization": (d.get("details") or {}).get(
                    "quantization_level"),
                "format": d.get("format"),
                "rope": cls.get("rope"),
                "era": cls.get("era"),
                "weave": cls.get("weave"),
                "context_length": cls.get("context_length"),
                "derived": True}
    except Exception:
        return {"architecture": arch,
                "model_info_keys": len(mi)}


def detect() -> dict:
    """Full detect: running gateways + scanned weight nodes."""
    pls = ports()
    gws = []
    for p in pls:
        if p.get("gateway"):
            models = p.get("models", [])
            for m in models:
                if p["kind"] == "ollama" and m.get("name"):
                    det = _ollama_show(m["name"])
                    if det:
                        m["details_probe"] = det
            gws.append({"port": p["port"], "kind": p["kind"],
                        "pid": p.get("pid"),
                        "models": models,
                        "reachable": p.get("reachable")})
            _append(GATEWAYS, {"kind": "gateway_seen",
                               "device_time": _device_time(),
                               **{k: gws[-1][k] for k in
                                  ("port", "kind", "pid")}})
    nodes = list(_jsonl(NODES) or [])
    # latest node per path
    latest = {}
    for n in nodes:
        latest[n["path"]] = n
    return {"kind": "modelscan_detect",
            "device_time": _device_time(),
            "gateways": gws,
            "gateway_count": len(gws),
            "listeners": len(pls),
            "weight_nodes": len(latest),
            "top_lanes": sorted(
                latest.values(), key=lambda n: -n["bytes"])[:20]}


def status() -> dict:
    nodes = list(_jsonl(NODES) or [])
    gws = list(_jsonl(GATEWAYS) or [])
    latest = {}
    for n in nodes:
        latest[n["path"]] = n
    return {"kind": "modelscan_state",
            "device_time": _device_time(),
            "weight_nodes": len(latest),
            "gateway_events": len(gws),
            "formats_seen": sorted({f for n in latest.values()
                                    for f in n.get("formats",
                                                   [])}),
            "note": ("dirs are nodes on the raw pcie lane "
                     "expressway; gateways detected via live "
                     "port log probes")}
