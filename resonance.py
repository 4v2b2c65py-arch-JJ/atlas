"""Resonance emitter: renders the ALA structure as video reacting to the code.

Spatial data = each parsed node's position, depth, glow, and a resonant
frequency/phase derived from its own label — so the emission literally
reacts to the code's content. Output matches the provided video's spec
(resolution, fps, duration) frame-for-frame.
"""

import cv2
import hashlib
import json
import math
from pathlib import Path

import numpy as np

import atlas

ROOT = Path(__file__).parent
VIDEO = ROOT / "videos" / "sample-5s.mp4"
OUT = ROOT / "videos" / "resonance"
EXTRACT = ROOT / "videos" / "extracted"


def stable_hash(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def video_spec(path: Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    spec = {
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": cap.get(cv2.CAP_PROP_FPS),
        "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
    }
    cap.release()
    spec["duration"] = spec["frames"] / spec["fps"] if spec["fps"] else 0
    return spec


def spatial_layout(payload: atlas.Node) -> list[dict]:
    """Assign each spine node a spatial slot; leaves branch off."""
    nodes = []
    spine, node = [], payload
    while True:
        spine.append(node)
        if len(node.children) != 1:
            break
        node = node.children[0]

    n = len(spine)
    cx = 0.5
    for i, nd in enumerate(spine):
        h = stable_hash(nd.text)
        jitter = ((h % 1000) / 1000.0 - 0.5) * 0.24
        depth = i
        max_depth = n - 1
        nodes.append({
            "label": nd.text,
            "x": cx + jitter,
            "y": 0.12 + 0.76 * (i / max_depth if max_depth else 0),
            "depth": depth,
            "glow": atlas.tone_map((depth + 1) / n),
            "freq": 0.4 + (h % 2000) / 1000.0,          # 0.4–2.4 Hz
            "phase": (h >> 16) % 6283 / 1000.0,          # 0–2π
            "radius": 0.018 + 0.05 * ((h >> 8) % 1000) / 1000.0,
        })
    return nodes


def extract_frames(video: Path, dest: Path, every: int = 30) -> list[str]:
    dest.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    saved = []
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % every == 0:
            p = dest / f"frame_{i:04d}.png"
            cv2.imwrite(str(p), frame)
            saved.append(p.name)
        i += 1
    cap.release()
    return saved


def render(nodes: list[dict], spec: dict) -> tuple[Path, list[list[float]]]:
    OUT.mkdir(parents=True, exist_ok=True)
    W, H, fps = spec["width"], spec["height"], spec["fps"]
    n_frames = int(round(spec["duration"] * fps))

    out_path = OUT / "resonance.mp4"
    out_path.unlink(missing_ok=True)  # AVF avc1 writer cannot overwrite
    writer = cv2.VideoWriter(
        str(out_path), cv2.VideoWriter_fourcc(*"avc1"), fps, (W, H)
    )
    if not writer.isOpened():
        raise RuntimeError(f"VideoWriter failed to open {out_path}")

    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    intensity_log: list[list[float]] = []

    for f in range(n_frames):
        t = f / fps
        canvas = np.zeros((H, W, 3), np.float32)
        intensities = []

        for nd in nodes:
            wave = 0.5 + 0.5 * math.sin(
                2 * math.pi * nd["freq"] * t + nd["phase"]
            )
            intensity = nd["glow"] * wave
            intensities.append(round(intensity, 4))

            cx, cy = nd["x"] * W, nd["y"] * H
            r = nd["radius"] * min(W, H)
            d2 = (xx - cx) ** 2 + (yy - cy) ** 2
            blob = np.exp(-d2 / (2 * (r * 0.6) ** 2)) * intensity

            # hue drifts with depth: cyan core -> magenta rim
            depth_t = nd["depth"] / max(1, len(nodes) - 1)
            color = np.array([
                0.2 + 0.8 * depth_t,      # R rises with depth
                0.9 - 0.5 * depth_t,      # G falls
                1.0 - 0.3 * depth_t,      # B stays hot
            ], np.float32)
            canvas += blob[..., None] * color * 255

        canvas = np.clip(canvas, 0, 255).astype(np.uint8)
        cv2.putText(
            canvas, f"ALA resonance t={t:.2f}s", (10, H - 12),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1,
        )
        writer.write(canvas)
        intensity_log.append(intensities)

    writer.release()
    return out_path, intensity_log


def main() -> None:
    forest = atlas.parse(atlas.SRC)
    payload = forest[1]
    spec = video_spec(VIDEO)
    nodes = spatial_layout(payload)

    out_path, intensity_log = render(nodes, spec)
    extracted = extract_frames(VIDEO, EXTRACT)

    spatial = {
        "source": "atlas.SRC",
        "video_spec": spec,
        "nodes": nodes,
        "frames_rendered": len(intensity_log),
        "intensity_log": intensity_log,
        "extracted_destination_frames": extracted,
        "output": str(out_path),
    }
    (OUT / "spatial_data.json").write_text(json.dumps(spatial, indent=2))

    print(f"spec      {spec['width']}x{spec['height']} @ {spec['fps']:.0f}fps "
          f"{spec['frames']}f {spec['duration']:.2f}s")
    print(f"nodes     {len(nodes)} spatial slots")
    print(f"rendered  {out_path}")
    print(f"extracted {len(extracted)} frames -> {EXTRACT}")
    print(f"spatial   {OUT / 'spatial_data.json'}")


if __name__ == "__main__":
    main()
