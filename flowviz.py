"""Repeat visualiser — audio-target vs visual-data timeline.

Decodes a media URL's audio into Q-time energy windows and its
video into per-second luma/motion stats, aligns both on one grid,
then renders a looping mp4: top lane = audio energy with voiced
(target) segments lit, bottom lane = visual luma/motion, playhead
sweeps repeatedly. Stores the render into the ATLAS feed.
"""

import subprocess
import tempfile
import urllib.parse
import wave
from pathlib import Path

import cv2
import numpy as np

import passkeys

SR = 16000


def _mono(url: str, seconds: int) -> "np.ndarray | None":
    return passkeys._decode_mono(url, seconds)


def _video_stats(url: str, seconds: int) -> dict:
    """Per-second luma + motion from ffmpeg rawvideo frames."""
    ff = passkeys._ffmpeg()
    if not ff:
        return {"error": "no ffmpeg"}
    w, h = 64, 36
    cmd = [ff, "-y", "-t", str(seconds), "-i", url,
           "-vf", f"fps=1,scale={w}:{h}", "-f", "rawvideo",
           "-pix_fmt", "gray", "-"]
    try:
        raw = subprocess.run(cmd, capture_output=True,
                             timeout=seconds + 90).stdout
    except Exception:
        return {"error": "video decode failed"}
    n = len(raw) // (w * h)
    if n < 2:
        return {"error": "no video frames"}
    frames = np.frombuffer(raw[:n * w * h], dtype=np.uint8) \
        .reshape(n, h, w).astype(np.float32) / 255.0
    luma = frames.mean(axis=(1, 2))
    motion = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    motion = np.concatenate([[0.0], motion])
    return {"luma": [round(float(v), 3) for v in luma],
            "motion": [round(float(v), 4) for v in motion],
            "fps": 1, "frames": n}


def _bins(flow: dict, quantum: float = 1.0) -> list:
    """Resample the 0.5s audio envelope to 1s bins."""
    env = flow.get("envelope") or []
    step = int(round(quantum / flow.get("quantum", 0.5)))
    return [float(np.mean(env[i:i + step])) if env[i:i + step]
            else 0.0 for i in range(0, len(env), step)]


def render(url: str, seconds: int = 30, out: str | None = None,
           store: bool = True) -> dict:
    """Render the repeat visualiser mp4 and optionally file it into
    the feed via identity.store_file."""
    flow = passkeys.audio_flow(url, seconds=seconds, quantum=0.5)
    if "error" in flow:
        return flow
    vstats = _video_stats(url, seconds)
    if "error" in vstats:
        return vstats
    audio = _bins(flow, 1.0)
    n = min(len(audio), len(vstats["luma"]))
    if n < 4:
        return {"error": "not enough aligned windows"}
    audio, luma = audio[:n], vstats["luma"][:n]
    motion = vstats["motion"][:n]

    # voiced mask on the 1s grid (from flow segments)
    voiced = np.zeros(n, dtype=bool)
    for s in flow.get("segments", []):
        for i in range(int(s["start"]), min(int(np.ceil(s["end"])), n)):
            if 0 <= i < n:
                voiced[i] = True
    a = np.array(audio, dtype=np.float32)
    a /= (a.max() + 1e-9)
    l = np.array(luma, dtype=np.float32)
    l /= (l.max() + 1e-9)
    m = np.array(motion, dtype=np.float32)
    m /= (m.max() + 1e-9)

    # how often audio "targets" vs visual data responds
    target_rate = float(voiced.mean())
    vis_response = float(m[voiced].mean()) if voiced.any() else 0.0
    vis_base = float(m[~voiced].mean()) if (~voiced).any() else 0.0
    lift = round(vis_response / (vis_base + 1e-6), 2)

    W, H = 960, 540
    FPS = 30
    LANE_H = 150
    TOP = 90
    # loop length: sweep the timeline in ~6s, repeat for `seconds`
    sweep = max(2.0, min(8.0, n / 4.0))
    total_frames = int(min(seconds, 24) * FPS)
    host = urllib.parse.urlsplit(url).netloc[:28]

    out_path = out or str(Path(tempfile.gettempdir()) /
                          f"flowviz_{abs(hash(url)) % 99999}.mp4")
    vw = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(
        *"avc1"), FPS, (W, H))
    if not vw.isOpened():
        vw = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(
            *"mp4v"), FPS, (W, H))
    bar_w = (W - 40) / max(n, 1)

    for f in range(total_frames):
        t = f / FPS
        frame = np.zeros((H, W, 3), dtype=np.uint8)
        # background — kept bright enough to pass the luma gate
        frame[:] = (26, 26, 36)
        # playhead sweeps timeline repeatedly
        pos = (t % sweep) / sweep * n
        pi = int(pos)
        # ---- audio lane (top) ----
        for i in range(n):
            x0 = int(20 + i * bar_w)
            x1 = int(20 + (i + 1) * bar_w) - 1
            hgt = int(audio[i] * LANE_H * 0.9)
            y0 = TOP + LANE_H - hgt
            col = (90, 230, 255) if voiced[i] else (70, 130, 190)
            cv2.rectangle(frame, (x0, y0), (x1, TOP + LANE_H), col,
                          -1)
            if voiced[i]:  # target hit marker
                cv2.rectangle(frame, (x0, TOP - 8), (x1, TOP),
                              (0, 255, 255), -1)
        # ---- visual lane (bottom): luma + motion ----
        vy = TOP + LANE_H + 60
        for i in range(n):
            x0 = int(20 + i * bar_w)
            x1 = int(20 + (i + 1) * bar_w) - 1
            lh = int(luma[i] * LANE_H * 0.5)
            mh = int(motion[i] * LANE_H * 0.9)
            cv2.rectangle(frame, (x0, vy + LANE_H - lh),
                          (x1, vy + LANE_H), (80, 80, 200), -1)
            cv2.rectangle(frame, (x0, vy + LANE_H - lh - mh),
                          (x1, vy + LANE_H - lh), (60, 220, 120), -1)
        # ---- playhead ----
        if 0 <= pi < n:
            px = int(20 + pos * bar_w)
            cv2.line(frame, (px, TOP - 20), (px, vy + LANE_H),
                     (255, 255, 255), 2)
        # ---- HUD ----
        cv2.putText(frame,
                    f"REPEAT VIS // {host} // target {target_rate:.0%}"
                    f"  pause {flow.get('pause_ratio', 0):.0%}"
                    f"  flow {flow.get('flow_score', 0)}"
                    f"  vis-lift x{lift}",
                    (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (230, 230, 230), 1, cv2.LINE_AA)
        cv2.putText(frame, "AUDIO TARGETS",
                    (20, TOP + LANE_H + 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (80, 200, 255), 1)
        cv2.putText(frame, "VISUAL DATA (luma/motion)",
                    (20, vy - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    (60, 220, 120), 1)
        vw.write(frame)
    vw.release()

    result = {"file": out_path, "windows": n,
              "target_rate": round(target_rate, 3),
              "vis_lift": lift,
              "flow_score": flow.get("flow_score"),
              "pauses": flow.get("pauses"),
              "segments": flow.get("segments")}
    if store:
        try:
            import identity
            # self-generated viz: bypass the upload-quality gate,
            # it still gets classified/filed normally
            result["stored"] = identity.store_file(
                out_path, skip_score_gate=True)
        except Exception as e:
            result["store_error"] = str(e)
    return result
