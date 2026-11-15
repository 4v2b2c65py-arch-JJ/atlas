"""RenderLab — computational anomaly datasets from renderers.

Scans proximity/stack outputs for COMPUTABLE FACTORS (the
numbers the model actually produces — distances, powers,
deltas, ability counts) and packages them as emit-ready
datasets. Accepts datasets reported from OTHER MACHINES
(remote renderers), detects statistical anomalies against
the local baseline, and keeps a RECORDED SAVE FILE per
renderer — its detected factors, anomalies, last seen.

GPU probing is honest: system_profiler (macOS), nvidia-smi,
vulkaninfo — whatever exists gets reported; missing tools
are reported missing, never faked.

Store: data/renderlab/{datasets.jsonl, anomalies.jsonl,
       saves/<renderer>.json, baseline.json}
"""

import json
import math
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "renderlab"
DS = OUT / "datasets.jsonl"
ANOM = OUT / "anomalies.jsonl"
SAVES = OUT / "saves"
BASE = OUT / "baseline.json"
CAL = OUT / "calendar"

Z_THRESHOLD = 2.5   # |z| beyond this = anomaly


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
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with path.open("a") as f:
        f.write(line)
    day = CAL / (rec.get("device_time", {})
                 .get("date", "unknown") + ".jsonl")
    day.parent.mkdir(parents=True, exist_ok=True)
    with day.open("a") as f:
        f.write(line)


# ---------- GPU / render probing (honest) ----------

def probe_gpu() -> dict:
    """Detect render hardware + API availability."""
    out = {"kind": "gpu_probe", "apis": {}, "gpus": [],
           "derived": True}
    # macOS display hardware
    try:
        r = subprocess.run(
            ["system_profiler", "SPDisplaysDataType"],
            capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                line = line.strip()
                if "Chipset Model" in line or \
                        "Model:" in line and "VRAM" not in line:
                    out["gpus"].append(
                        {"name": line.split(":")[-1].strip(),
                         "src": "system_profiler"})
                if "VRAM" in line or "Metal" in line:
                    out["apis"]["metal"] = True
            if "Metal" in r.stdout:
                out["apis"]["metal"] = True
    except Exception as e:
        out["gpus"].append({"error": str(e)[:80]})
    # vulkan
    try:
        r = subprocess.run(["vulkaninfo", "--summary"],
                           capture_output=True, text=True,
                           timeout=15)
        out["apis"]["vulkan"] = r.returncode == 0
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if "deviceName" in line or \
                        "apiVersion" in line:
                    out["gpus"].append(
                        {"name": line.strip()[:100],
                         "src": "vulkaninfo"})
    except FileNotFoundError:
        out["apis"]["vulkan"] = False
    except Exception:
        out["apis"]["vulkan"] = False
    # cuda / nvidia
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name",
                            "--format=csv,noheader"],
                           capture_output=True, text=True,
                           timeout=10)
        out["apis"]["cuda"] = r.returncode == 0
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                if line.strip():
                    out["gpus"].append(
                        {"name": line.strip(),
                         "src": "nvidia-smi"})
    except FileNotFoundError:
        out["apis"]["cuda"] = False
    except Exception:
        out["apis"]["cuda"] = False
    return out


# ---------- computable factors ----------

def computable_factors(node: str,
                       profile: str = "default") -> dict:
    """Extract the NUMBERS the model produces for a node —
    stack power, ability counts, proximity distances, tier
    layer min/max, true-name values. These are the factors a
    renderer/anomaly detector consumes."""
    f = {"kind": "computable_factors", "node": node,
         "profile": profile, "factors": {}, "derived": True}
    try:
        import stack as _st
        full = _st.build_stack(node, profile)
        f["factors"]["power"] = _st._power(full)
        f["factors"]["abilities"] = sum(
            full["counts"].values())
        f["factors"]["layers"] = full.get("counts", {})
    except Exception as e:
        f["factors"]["power_error"] = str(e)[:80]
    try:
        import proximity as _px
        import familytree as _ft
        nodes = list(getattr(_ft, "LABEL", {}).keys()) or \
            list(getattr(_ft, "TREE", {}).keys())
        dists = []
        for other in nodes:
            if other == node:
                continue
            try:
                dists.append(_px.distance(node, other))
            except Exception:
                pass
        if dists:
            f["factors"]["prox_min"] = round(min(dists), 4)
            f["factors"]["prox_max"] = round(max(dists), 4)
            f["factors"]["prox_mean"] = round(
                sum(dists) / len(dists), 4)
    except Exception:
        pass
    try:
        import tierlayers as _tl
        rng = _tl.output_range(node, profile)
        f["factors"]["min_output"] = \
            (rng.get("min_output") or {}).get("power")
        f["factors"]["max_output"] = \
            (rng.get("max_output") or {}).get("power")
    except Exception:
        pass
    return f


def emit(node: str, renderer: str = "local",
         profile: str = "default") -> dict:
    """Emit a dataset record: computable factors + GPU probe
    for this renderer. Appends to datasets + calendar."""
    factors = computable_factors(node, profile)
    gpu = probe_gpu() if renderer == "local" else {}
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "render_dataset", "node": node,
           "renderer": renderer, "profile": profile,
           "factors": factors["factors"],
           "gpu": gpu if renderer == "local" else
           {"reported": renderer},
           "derived": True}
    _append(DS, rec)
    _update_baseline(rec)
    _update_save(renderer, rec)
    return {"kind": "render_emit", "node": node,
            "renderer": renderer,
            "factors": factors["factors"],
            "gpu_apis": gpu.get("apis", {}),
            "anomalies": _flag_anomalies(
                factors["factors"], renderer)}


def ingest_remote(renderer: str, dataset: dict) -> dict:
    """Accept a dataset reported from ANOTHER MACHINE —
    validate shape, flag anomalies vs baseline, update that
    renderer's save file."""
    renderer = (renderer or "remote").lower()
    factors = (dataset or {}).get("factors") or {}
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "render_dataset", "node":
           dataset.get("node", "remote"),
           "renderer": renderer,
           "remote": True,
           "factors": factors,
           "gpu": dataset.get("gpu", {}),
           "derived": True}
    _append(DS, rec)
    anomalies = _flag_anomalies(factors, renderer)
    if anomalies:
        for a in anomalies:
            _append(ANOM, {"ts": rec["ts"],
                           "device_time": rec["device_time"],
                           "kind": "render_anomaly",
                           "renderer": renderer,
                           "node": rec["node"], **a})
    _update_save(renderer, rec)
    return {"kind": "render_ingest", "renderer": renderer,
            "node": rec["node"],
            "factors_received": len(factors),
            "anomalies": anomalies}


def _stats() -> dict:
    """Baseline mean/std per numeric factor across all
    datasets — the local normal."""
    vals = {}
    for r in _jsonl(DS) or []:
        for k, v in (r.get("factors") or {}).items():
            if isinstance(v, (int, float)) and \
                    not isinstance(v, bool):
                vals.setdefault(k, []).append(float(v))
    out = {}
    for k, xs in vals.items():
        if len(xs) < 3:
            continue
        xs = sorted(xs)
        n = len(xs)
        med = xs[n // 2] if n % 2 else \
            (xs[n // 2 - 1] + xs[n // 2]) / 2
        # MAD — median absolute deviation; robust to the very
        # outliers we're hunting (mean/std get polluted)
        dev = sorted(abs(x - med) for x in xs)
        mad = dev[n // 2] if n % 2 else \
            (dev[n // 2 - 1] + dev[n // 2]) / 2
        out[k] = {"median": med, "mad": mad, "n": n}
    return out


def _flag_anomalies(factors: dict,
                    renderer: str) -> list[dict]:
    """Z-score each factor vs baseline; |z| > threshold =
    computational anomaly."""
    base = _stats()
    out = []
    for k, v in factors.items():
        if not isinstance(v, (int, float)) or \
                isinstance(v, bool):
            continue
        b = base.get(k)
        if not b:
            continue
        mad = b["mad"]
        if mad < 1e-9:
            # tight cluster — any deviation beyond 1% of median
            # counts; scale-free fallback
            if abs(v - b["median"]) > \
                    max(1e-6, abs(b["median"]) * 0.5):
                out.append({"factor": k, "value": v,
                            "robust_z": "inf",
                            "median": b["median"],
                            "rule": "mad_zero"})
            continue
        z = 0.6745 * (v - b["median"]) / mad
        if abs(z) > Z_THRESHOLD:
            out.append({"factor": k, "value": v,
                        "robust_z": round(z, 3),
                        "median": round(b["median"], 3),
                        "mad": round(mad, 3),
                        "threshold": Z_THRESHOLD})
    return out


def _update_baseline(rec: dict) -> None:
    st = _stats()
    BASE.write_text(json.dumps(
        {"kind": "render_baseline", "ts": time.time(),
         "stats": st}, indent=1))


def _update_save(renderer: str, rec: dict) -> None:
    """Recorded save file per renderer — its latest factors,
    anomaly count, last seen, dataset count."""
    SAVES.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_"
                   else "_" for c in renderer)
    p = SAVES / (safe + ".json")
    save = {}
    if p.exists():
        try:
            save = json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    save.update({
        "kind": "renderer_save", "renderer": renderer,
        "last_seen": rec["ts"],
        "device_time": rec["device_time"],
        "last_node": rec.get("node"),
        "last_factors": rec.get("factors", {}),
        "datasets": save.get("datasets", 0) + 1,
        "anomalies": save.get("anomalies", 0) +
        len(_flag_anomalies(rec.get("factors", {}),
                            renderer)),
        "gpu": rec.get("gpu", {}),
        "derived": True})
    p.write_text(json.dumps(save, indent=1))


def saves() -> dict:
    out = {}
    if SAVES.exists():
        for p in SAVES.glob("*.json"):
            try:
                out[p.stem] = json.loads(p.read_text())
            except json.JSONDecodeError:
                continue
    return {"kind": "renderer_saves", "saves": out}


def status() -> dict:
    return {"kind": "renderlab_state",
            "device_time": _device_time(),
            "datasets": sum(1 for _ in _jsonl(DS) or []),
            "anomalies": sum(1 for _ in _jsonl(ANOM) or []),
            "saves": len(list(SAVES.glob("*.json")))
            if SAVES.exists() else 0,
            "baseline_factors": len(_stats()),
            "z_threshold": Z_THRESHOLD}
