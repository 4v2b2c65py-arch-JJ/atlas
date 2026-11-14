"""Upscale pass: bring sub-720p outlets up to stream quality.

Lanczos4 resize + unsharp mask, re-encoded via OpenCV avc1. Used when
the identity gate would otherwise reject usable content.
"""

import cv2
import numpy as np
from pathlib import Path

ROOT = Path(__file__).parent
TARGET_H = 720
MAX_FRAMES = 3600  # safety cap for very long sources


def needs_upscale(path: str, target_h: int = TARGET_H) -> bool:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return False
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return 0 < h < target_h


def upscale_video(src: str, dst_dir: str | Path | None = None) -> Path | None:
    """Upscale src to TARGET_H (aspect kept). Returns new file or None."""
    src = Path(src)
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return None
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if H >= TARGET_H:
        cap.release()
        return None
    scale = TARGET_H / H
    nw = int(W * scale) // 2 * 2
    nh = TARGET_H

    out_dir = Path(dst_dir) if dst_dir else ROOT / "videos" / "upscaled"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / src.name
    dest.unlink(missing_ok=True)  # avc1 can't overwrite

    writer = cv2.VideoWriter(str(dest), cv2.VideoWriter_fourcc(*"avc1"),
                             fps, (nw, nh))
    if not writer.isOpened():
        cap.release()
        return None

    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], np.float32)
    written = 0
    while written < MAX_FRAMES:
        ok, frame = cap.read()
        if not ok:
            break
        up = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
        up = cv2.filter2D(up, -1, kernel)
        writer.write(up)
        written += 1
    cap.release()
    writer.release()
    return dest if written else None
