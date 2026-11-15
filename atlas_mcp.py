"""ATLAS local MCP server — video reference, zoom, fidelity, storage tools.

stdio transport; run standalone for debugging:
    .venv/bin/python atlas_mcp.py
"""

import json
import math
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import atlas  # noqa: E402
import caster  # noqa: E402
import identity  # noqa: E402
import llm  # noqa: E402
import resonance  # noqa: E402

from mcp.server.mcpserver import MCPServer  # noqa: E402

mcp = MCPServer("atlas")

ROOT = Path(__file__).parent
MIN_FREE_RATIO = 0.15  # refuse to fill the disk past 85%


# ---------- shared helpers ----------

def probe(path: str) -> dict:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {path}")
    spec = {
        "path": path,
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
    }
    cap.release()
    spec["duration"] = spec["frames"] / spec["fps"] if spec["fps"] else 0
    return spec


def clamp_spec(spec: dict, scale: float = 1.0,
               max_seconds: float | None = None) -> dict:
    """Adjust resolution/length; keeps aspect and fps."""
    out = dict(spec)
    if scale != 1.0:
        out["width"] = int(out["width"] * scale) // 2 * 2
        out["height"] = int(out["height"] * scale) // 2 * 2
    if max_seconds is not None:
        out["duration"] = min(out["duration"], max_seconds)
        out["frames"] = int(out["duration"] * out["fps"])
    return out


def storage_report(target: str | Path) -> dict:
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(target)
    return {
        "path": str(target),
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
        "free_gb": round(usage.free / 1e9, 2),
    }


def ensure_space(target: str | Path, needed_bytes: int) -> dict:
    """Storage awareness gate: refuse if the write would overfill disk."""
    rep = storage_report(target)
    headroom = rep["free_bytes"] * (1 - MIN_FREE_RATIO)
    ok = needed_bytes <= headroom
    rep["needed_bytes"] = needed_bytes
    rep["headroom_bytes"] = int(headroom)
    rep["allowed"] = ok
    if not ok:
        raise RuntimeError(
            f"insufficient storage: need {needed_bytes / 1e9:.2f} GB, "
            f"headroom {headroom / 1e9:.2f} GB in {rep['path']}"
        )
    return rep


def estimate_bytes(spec: dict, fmt: str, seconds: float | None = None) -> int:
    frames = spec["frames"] if seconds is None else int(seconds * spec["fps"])
    px = spec["width"] * spec["height"]
    if fmt == "png_seq":
        return int(frames * px * 3 * 0.9)          # ~lossless PNG estimate
    return int(frames * px * 0.05)                  # ~5 MB/s at 1080p30 avc1


# ---------- tools ----------

@mcp.tool()
def atlas_identity() -> dict:
    """Return the ATLAS identity profile: observer identity, sort
    categories, quality gate thresholds, storage policy."""
    return identity.load_profile()


@mcp.tool()
def atlas_models() -> dict:
    """List local AI models available via Ollama for classification
    enhancement."""
    return {"endpoint": llm.OLLAMA, "models": llm.list_models()}


@mcp.tool()
def atlas_classify(video_path: str, use_llm: bool = False) -> dict:
    """Observe a video and AI-sort it: extracts probe + frame features,
    assigns categories, scores quality, and reports the gate verdict.
    Set use_llm=true to have the local Ollama model review and adjust
    the heuristic verdict."""
    profile = identity.load_profile()
    obs = identity.observe(video_path, profile)
    result = identity.classify(obs, profile)
    if use_llm:
        verdict = llm.refine_classification(obs, result, profile)
        if verdict and "categories" in verdict:
            result = identity.classify(obs, profile, overrides=verdict)
        result["llm"] = verdict
    return result | {"probe": obs["probe"]}


@mcp.tool()
def atlas_store(video_path: str, use_llm: bool = False) -> dict:
    """Classify then file a video into videos/store/<category>/.
    Rejected items land in store/rejected/. Appends to manifest.jsonl.
    Storage-aware: aborts if the copy would overfill the disk.
    Set use_llm=true for local-model classification."""
    return identity.store_file(video_path, use_llm=use_llm)


@mcp.tool()
def atlas_probe(video_path: str) -> dict:
    """Probe a video file: resolution, fps, frame count, duration."""
    return probe(video_path)


@mcp.tool()
def atlas_storage(target_dir: str = "videos") -> dict:
    """Report disk usage/free space for the output area."""
    return storage_report(ROOT / target_dir)


@mcp.tool()
def atlas_resonance_render(
    scale: float = 1.0,
    max_seconds: float | None = None,
    fmt: str = "mp4",
    store: bool = False,
) -> dict:
    """Render the ALA resonance video. scale adjusts fidelity (e.g. 2.0 =
    4K from 1080p source spec), max_seconds truncates length, fmt selects
    mp4 (avc1) or png_seq (lossless frames). Storage-checked before render."""
    if fmt not in ("mp4", "png_seq"):
        raise ValueError("fmt must be 'mp4' or 'png_seq'")

    forest = atlas.parse(atlas.SRC)
    nodes = resonance.spatial_layout(forest[1])
    base = resonance.video_spec(str(resonance.VIDEO))
    spec = clamp_spec(base, scale=scale, max_seconds=max_seconds)

    out_dir = ROOT / "videos" / "resonance"
    est = estimate_bytes(spec, fmt)
    gate = ensure_space(out_dir, est)

    if fmt == "png_seq":
        out_dir.mkdir(parents=True, exist_ok=True)
        n = int(round(spec["duration"] * spec["fps"]))
        written = 0
        frames_dir = out_dir / "frames_png"
        frames_dir.mkdir(exist_ok=True)
        yy, xx = np.mgrid[0:spec["height"], 0:spec["width"]].astype(np.float32)
        for f in range(n):
            t = f / spec["fps"]
            canvas = np.zeros((spec["height"], spec["width"], 3), np.float32)
            for nd in nodes:
                wave = 0.5 + 0.5 * math.sin(
                    2 * math.pi * nd["freq"] * t + nd["phase"]
                )
                intensity = nd["glow"] * wave
                cx, cy = nd["x"] * spec["width"], nd["y"] * spec["height"]
                r = nd["radius"] * min(spec["width"], spec["height"])
                d2 = (xx - cx) ** 2 + (yy - cy) ** 2
                blob = np.exp(-d2 / (2 * (r * 0.6) ** 2)) * intensity
                dt = nd["depth"] / max(1, len(nodes) - 1)
                color = np.array([0.2 + 0.8 * dt, 0.9 - 0.5 * dt,
                                  1.0 - 0.3 * dt], np.float32)
                canvas += blob[..., None] * color * 255
            img = np.clip(canvas, 0, 255).astype(np.uint8)
            cv2.imwrite(str(frames_dir / f"frame_{f:04d}.png"), img)
            written += 1
        result = {"format": "png_seq", "frames": written,
                  "dir": str(frames_dir)}
    else:
        spec["frames"] = int(round(spec["duration"] * spec["fps"]))
        out_path, log = resonance.render(nodes, spec)
        result = {"format": "mp4", "path": str(out_path),
                  "frames": len(log)}

    result.update({"spec": spec, "storage": gate})
    if store and "path" in result:
        result["stored_record"] = identity.store_file(result["path"])
    return result


@mcp.tool()
def atlas_auto_zoom(
    video_path: str,
    max_seconds: float | None = None,
    zoom: float = 1.6,
    sample_every: int = 10,
    store: bool = False,
) -> dict:
    """Automatic reference zoom: samples the video, finds the region of
    highest motion energy, and renders a smoothly-tracked zoom crop.
    Storage-checked; respects max_seconds for long sources."""
    spec = probe(video_path)
    spec = clamp_spec(spec, max_seconds=max_seconds)
    n_frames = spec["frames"]
    W, H, fps = spec["width"], spec["height"], spec["fps"]

    cap = cv2.VideoCapture(video_path)
    prev = None
    keyframes = []  # (frame_idx, cx, cy, half_w, half_h) in px
    idx = 0
    while idx < n_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if prev is not None:
            energy = cv2.absdiff(gray, prev[1])
            energy = cv2.GaussianBlur(energy, (21, 21), 0)
            thresh = np.percentile(energy, 92)
            ys, xs = np.nonzero(energy > thresh)
            if len(xs) > 50:
                cx, cy = xs.mean(), ys.mean()
                spread = max(xs.std(), ys.std()) * 3
                half = min(max(spread, 0.18 * min(W, H)),
                           0.5 * min(W, H) / zoom)
                keyframes.append((idx, cx, cy, half))
        prev = (idx, gray)
        idx += sample_every
    cap.release()

    if not keyframes:
        keyframes = [(0, W / 2, H / 2, min(W, H) / (2 * zoom))]

    # smooth the ROI path with a moving average
    k = len(keyframes)
    smooth = []
    for i, kf in enumerate(keyframes):
        lo, hi = max(0, i - 2), min(k, i + 3)
        win = keyframes[lo:hi]
        smooth.append((
            kf[0],
            float(np.mean([w[1] for w in win])),
            float(np.mean([w[2] for w in win])),
            float(np.mean([w[3] for w in win])),
        ))

    out_dir = ROOT / "videos" / "zoom"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "auto_zoom.mp4"
    out_path.unlink(missing_ok=True)  # AVF avc1 writer cannot overwrite

    est = estimate_bytes({**spec, "width": int(W / zoom) // 2 * 2,
                          "height": int(H / zoom) // 2 * 2}, "mp4")
    gate = ensure_space(out_dir, est)

    zw = int(W / zoom) // 2 * 2
    zh = int(H / zoom) // 2 * 2
    writer = cv2.VideoWriter(
        str(out_path), cv2.VideoWriter_fourcc(*"avc1"), fps, (zw, zh))
    if not writer.isOpened():
        raise RuntimeError(f"VideoWriter failed to open {out_path}")

    def roi_at(frame_idx: int):
        # linear interpolate between keyframes
        if frame_idx <= smooth[0][0]:
            return smooth[0][1:]
        if frame_idx >= smooth[-1][0]:
            return smooth[-1][1:]
        for a, b in zip(smooth, smooth[1:]):
            if a[0] <= frame_idx <= b[0]:
                t = (frame_idx - a[0]) / max(1, b[0] - a[0])
                return tuple(a[i] + (b[i] - a[i]) * t for i in (1, 2, 3))
        return smooth[-1][1:]

    cap = cv2.VideoCapture(video_path)
    written = 0
    for f in range(n_frames):
        ok, frame = cap.read()
        if not ok:
            break
        cx, cy, half = roi_at(f)
        x0 = int(np.clip(cx - half, 0, W - zw))
        y0 = int(np.clip(cy - half, 0, H - zh))
        # square-ish crop centered on ROI, resized to zoom target
        side = int(min(half * 2, W, H))
        sx0 = int(np.clip(cx - side / 2, 0, W - side))
        sy0 = int(np.clip(cy - side / 2, 0, H - side))
        crop = frame[sy0:sy0 + side, sx0:sx0 + side]
        writer.write(cv2.resize(crop, (zw, zh), interpolation=cv2.INTER_LANCZOS4))
        written += 1
    cap.release()
    writer.release()

    result = {
        "path": str(out_path),
        "frames": written,
        "zoom": zoom,
        "keyframes": len(smooth),
        "spec": {**spec, "width": zw, "height": zh},
        "storage": gate,
    }
    if store:
        result["stored_record"] = identity.store_file(result["path"])
    return result


@mcp.tool()
def atlas_upscale(video_path: str) -> dict:
    """Upscale a sub-720p video to 720p (Lanczos4 + unsharp) and file it
    into the sorted store, bypassing the score gate. Never drops content
    for being 'poor' — it improves it instead."""
    import upscale
    if not upscale.needs_upscale(video_path):
        return {"upscaled": False, "reason": "already >=720p or unreadable"}
    up = upscale.upscale_video(video_path)
    if not up:
        return {"upscaled": False, "reason": "encode failed"}
    record = identity.store_file(str(up), skip_score_gate=True)
    up.unlink(missing_ok=True)
    return {"upscaled": True, "stored": record.get("stored"),
            "categories": record.get("categories")}


@mcp.tool()
def atlas_enhance_fidelity(
    video_path: str,
    max_seconds: float | None = None,
    out_subdir: str = "enhanced",
) -> dict:
    """Highest-fidelity enhancement: per-frame CLAHE contrast + unsharp
    mask, written as lossless PNG sequence (storage-gated). Use for
    reference frames / frame extraction destinations."""
    spec = probe(video_path)
    spec = clamp_spec(spec, max_seconds=max_seconds)

    out_dir = ROOT / "videos" / out_subdir
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    est = estimate_bytes(spec, "png_seq")
    gate = ensure_space(out_dir, est)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    cap = cv2.VideoCapture(video_path)
    written = 0
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], np.float32)
    for f in range(spec["frames"]):
        ok, frame = cap.read()
        if not ok:
            break
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        lab[..., 0] = clahe.apply(lab[..., 0])
        enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
        enhanced = cv2.filter2D(enhanced, -1, kernel)
        cv2.imwrite(str(frames_dir / f"frame_{f:04d}.png"), enhanced)
        written += 1
    cap.release()

    return {
        "dir": str(frames_dir),
        "frames": written,
        "format": "png_seq_lossless",
        "enhancements": ["CLAHE(luma, clip=2.0, 8x8)", "unsharp 3x3"],
        "spec": spec,
        "storage": gate,
    }


@mcp.tool()
def atlas_tier_list() -> dict:
    """ATLAS caster tier list — names ranked S/A/B/C/D by
    consensus strength (mirror votes + est. global bearers)."""
    return caster.tier_list()


@mcp.tool()
def atlas_cast_mirror(trigger: dict) -> dict:
    """Triggerable caster mirror — feed it a websearch/discovery
    result {name, src, at, repeats, seconds, instruction} and it
    casts a character-lens clip with repeater view, engraving the
    instruction into the original video layers. Metrics (incl.
    downtime) are logged; results persist to casts.gguf."""
    return caster.mirror(trigger)


@mcp.tool()
def atlas_caster_status() -> dict:
    """Caster status: compute caps, downtime metrics, tier list,
    cast count, GGUF store."""
    return caster.status()


if __name__ == "__main__":
    mcp.run()
