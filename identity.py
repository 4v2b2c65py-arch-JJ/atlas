"""ATLAS identity profile: classification, quality gate, sorted storage."""

import json
import shutil
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).parent
PROFILE_PATH = ROOT / "identity.json"
STORE_ROOT = ROOT / "videos" / "store"
MANIFEST = STORE_ROOT / "manifest.jsonl"

DEFAULT_PROFILE = {
    "identity": "ATLAS",
    "version": "1.0.0",
    "observer": "local-ai-mcp",
    "categories": [
        "resonance-synthetic", "high-motion", "low-motion",
        "dark-scene", "bright-scene", "portrait", "landscape",
        "short-clip", "long-form", "rejected",
    ],
    "quality_gate": {
        "probe_required": True,
        "min_bytes": 20000,
        "min_width": 640,
        "min_height": 360,
        "min_score": 50,
        "min_free_ratio": 0.15,
    },
    "storage": {"root": "videos/store", "auto_store": False},
    "classifier": {
        "sample_frames": 24,
        "motion_high": 6.0,
        "motion_low": 1.5,
        "dark_luma": 70,
        "bright_luma": 170,
        "short_clip_s": 3.0,
        "long_form_s": 60.0,
        "portrait_aspect": 0.9,
    },
}


def load_profile() -> dict:
    if PROFILE_PATH.exists():
        return json.loads(PROFILE_PATH.read_text())
    PROFILE_PATH.write_text(json.dumps(DEFAULT_PROFILE, indent=2))
    return dict(DEFAULT_PROFILE)


def probe_video(path: str) -> dict:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {path}")
    spec = {
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
    }
    cap.release()
    spec["duration"] = spec["frames"] / spec["fps"] if spec["fps"] else 0
    return spec


def observe(path: str, profile: dict) -> dict:
    """Sample frames and extract the features the classifier sorts on."""
    spec = probe_video(path)
    cfg = profile["classifier"]
    n_samples = min(cfg["sample_frames"], max(1, spec["frames"]))
    idxs = np.linspace(0, spec["frames"] - 1, n_samples).astype(int)

    cap = cv2.VideoCapture(path)
    lumas, diffs = [], []
    prev = None
    for i in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        lumas.append(float(gray.mean()))
        if prev is not None:
            diffs.append(float(np.abs(gray - prev).mean()))
        prev = gray
    cap.release()

    size = Path(path).stat().st_size
    return {
        "probe": spec,
        "bytes": size,
        "mean_luma": float(np.mean(lumas)) if lumas else 0.0,
        "luma_std": float(np.std(lumas)) if lumas else 0.0,
        "motion": float(np.mean(diffs)) if diffs else 0.0,
        "aspect": spec["width"] / spec["height"] if spec["height"] else 0,
    }


def quality_score(obs: dict) -> int:
    """0-100 composite: resolution, duration, bitrate-ish size, motion."""
    p = obs["probe"]
    res = min(1.0, (p["width"] * p["height"]) / (1920 * 1080)) * 30
    dur = min(1.0, p["duration"] / 30.0) * 20
    size = min(1.0, obs["bytes"] / 50e6) * 20
    motion = min(1.0, obs["motion"] / 20.0) * 15
    stable = 15 if obs["luma_std"] < 60 else 10
    return int(res + dur + size + motion + stable)


def classify(obs: dict, profile: dict,
             overrides: dict | None = None,
             skip_score_gate: bool = False) -> dict:
    """Rule-sorted categories of what was observed + gate verdict."""
    cfg = profile["classifier"]
    gate = profile["quality_gate"]
    p, cats = obs["probe"], []

    # synthetic signature: near-black field, faint content, minimal motion
    synthetic = (
        obs["mean_luma"] < cfg.get("synthetic_luma_max", 15)
        and obs["luma_std"] < cfg.get("synthetic_std_max", 8)
        and obs["motion"] < cfg.get("synthetic_motion_max", 4)
    )
    if synthetic:
        cats.append("resonance-synthetic")

    aspect = obs["aspect"]
    if aspect < cfg["portrait_aspect"]:
        cats.append("portrait")
    else:
        cats.append("landscape")

    if obs["motion"] >= cfg["motion_high"]:
        cats.append("high-motion")
    elif obs["motion"] <= cfg["motion_low"]:
        cats.append("low-motion")

    if obs["mean_luma"] < cfg["dark_luma"]:
        cats.append("dark-scene")
    elif obs["mean_luma"] > cfg["bright_luma"]:
        cats.append("bright-scene")

    if p["duration"] < cfg["short_clip_s"]:
        cats.append("short-clip")
    elif p["duration"] > cfg["long_form_s"]:
        cats.append("long-form")

    score = quality_score(obs)
    if overrides:
        if overrides.get("categories"):
            allowed = set(profile["categories"])
            cats = [c for c in overrides["categories"] if c in allowed] or cats
        try:
            score = max(0, min(100, int(overrides.get("quality_score", score))))
        except (TypeError, ValueError):
            pass
    checks = {
        "probe_ok": True,
        "min_bytes": obs["bytes"] >= gate["min_bytes"],
        "min_width": p["width"] >= gate["min_width"],
        "min_height": p["height"] >= gate["min_height"],
        "min_score": score >= gate["min_score"] or synthetic or skip_score_gate,
    }
    passed = all(checks.values())
    if not passed:
        cats = ["rejected"]
    return {
        "categories": cats,
        "quality_score": score,
        "gate": {"passed": passed, "checks": checks},
        "features": {k: obs[k] for k in
                     ("mean_luma", "luma_std", "motion", "aspect", "bytes")},
    }


def store_file(path: str, profile: dict | None = None,
               use_llm: bool = False, skip_score_gate: bool = False) -> dict:
    """Classify an observed video and file it into the sorted store."""
    profile = profile or load_profile()
    path = str(Path(path).resolve())
    obs = observe(path, profile)
    result = classify(obs, profile, skip_score_gate=skip_score_gate)
    if use_llm:
        import llm
        verdict = llm.refine_classification(obs, result, profile)
        if verdict and "categories" in verdict:
            result = classify(obs, profile, overrides=verdict,
                              skip_score_gate=skip_score_gate)
            result["llm"] = verdict
        elif verdict:
            result["llm"] = verdict
    primary = result["categories"][0]

    store_root = ROOT / profile["storage"]["root"]
    dest_dir = store_root / primary
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(path).name
    shutil.copy2(path, dest)

    # storage awareness: refuse to let the store overfill the disk
    import shutil as _sh
    free = _sh.disk_usage(store_root).free
    if dest.stat().st_size > free * (1 - profile["quality_gate"]["min_free_ratio"]):
        dest.unlink(missing_ok=True)
        raise RuntimeError("store write would overfill disk; aborted")

    record = {
        "ts": time.time(),
        "src": path,
        "dest": str(dest),
        "identity": profile["identity"],
        "categories": result["categories"],
        "quality_score": result["quality_score"],
        "gate_passed": result["gate"]["passed"],
        "probe": obs["probe"],
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a") as fh:
        fh.write(json.dumps(record) + "\n")

    result.update({"stored": str(dest), "manifest": str(MANIFEST)})
    return result
