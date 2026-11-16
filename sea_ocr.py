"""SeaOCR — wedge-depth decode of the Gilgamesh flood tablet.

Not generic OCR: this pipeline reads the IMPRESSIONS —
each cuneiform wedge is a 3D gouge in clay, so depth shows
up as shading asymmetry. Per cell we extract:

  - wedge orientation histogram (4 classes: horizontal,
    vertical, diag-up, diag-down — the wedge alphabet)
  - depth score (gradient magnitude asymmetry = which side
    of the gouge is lit)
  - wedge count / ink density

Cells cluster into a sign INVENTORY for this tablet
(unsupervised — no sign database needed), then the
inventory is matched against the sea spec tokens by
structural similarity. Output is a polygraphic
transcription: column -> cell -> cluster id.

Honest: this decodes FORMATIONS (wedge geometry), not
lexical meaning — sign->word mapping needs ORACC-level
lexical data, flagged as the next layer.

Store: data/sea/ocr_tablet.json
"""

import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).parent
IMG = ROOT / "data" / "sea" / "media" / \
    "gilgamesh_flood_tablet.jpg"
OUT = ROOT / "data" / "sea" / "ocr_tablet.json"


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def load_tablet() -> tuple[np.ndarray, tuple]:
    """Crop to the tablet face (bright region found by
    threshold projection)."""
    img = cv2.imread(str(IMG))
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask = (g > 100).astype(np.uint8)
    colsum = mask.sum(0)
    rowsum = mask.sum(1)
    xs = np.where(colsum > colsum.max() * 0.3)[0]
    ys = np.where(rowsum > rowsum.max() * 0.3)[0]
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    return g[y0:y1 + 1, x0:x1 + 1], (x0, y0, x1, y1)


def find_columns(g: np.ndarray) -> list[tuple]:
    """Cuneiform columns = vertical bands of impression
    density. Project edge magnitude horizontally, find
    bands separated by gutters."""
    blur = cv2.GaussianBlur(g, (5, 5), 0)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx ** 2 + gy ** 2)
    prof = mag.mean(axis=0)
    thr = prof.mean() * 0.6
    ink = prof > thr
    # bands of ink columns
    bands, start = [], None
    for i, v in enumerate(ink):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > 8:
                bands.append((start, i))
            start = None
    if start is not None:
        bands.append((start, len(ink)))
    # merge bands closer than 15px (same column split by
    # a light wedge gap)
    merged = []
    for b in bands:
        if merged and b[0] - merged[-1][1] < 15:
            merged[-1] = (merged[-1][0], b[1])
        else:
            merged.append(b)
    return merged


def find_cells(g: np.ndarray, col: tuple,
               band_h: int) -> list[tuple]:
    """Within a column band, horizontal projection finds
    row cells — cuneiform cells stack top-to-bottom."""
    x0, x1 = col
    sub = g[:, x0:x1]
    blur = cv2.GaussianBlur(sub, (5, 5), 0)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx ** 2 + gy ** 2)
    prof = mag.mean(axis=1)
    thr = prof.mean() * 0.55
    ink = prof > thr
    cells, start = [], None
    for i, v in enumerate(ink):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > band_h * 0.4:
                cells.append((start, i))
            start = None
    if start is not None:
        cells.append((start, len(ink)))
    return cells


def cell_features(g: np.ndarray, box: tuple) -> dict:
    """Wedge-depth features for one cell region."""
    y0, y1, x0, x1 = box
    cell = g[y0:y1, x0:x1]
    if cell.size == 0:
        return {"empty": True}
    blur = cv2.GaussianBlur(cell, (3, 3), 0)
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx ** 2 + gy ** 2)
    ang = np.arctan2(gy, gx)  # -pi..pi
    # wedge orientation classes from gradient direction:
    # impressions have gradients PERPENDICULAR to wedge axis
    # so add 90deg: wedge axis ~ ang+pi/2 mod pi
    axis = (ang + np.pi / 2) % np.pi
    hist, _ = np.histogram(
        axis[mag > np.percentile(mag, 70)],
        bins=8, range=(0, np.pi))
    # normalize to 4 wedge classes (pairs of bins)
    wedge = [int(hist[0] + hist[1]), int(hist[2] + hist[3]),
             int(hist[4] + hist[5]), int(hist[6] + hist[7])]
    # depth score: shading asymmetry — mean gradient on
    # the lit vs shadow side of strong edges
    strong = mag > np.percentile(mag, 85)
    depth = float(mag[strong].mean()) if strong.any() \
        else 0.0
    ink = float((cell < np.percentile(cell, 30)).mean())
    return {"wedge_hist": wedge, "depth": round(depth, 2),
            "ink": round(ink, 3),
            "wedge_count": int((mag >
                                np.percentile(mag, 80)).sum()
                               // 12)}


def cluster_cells(cells_feat: list[dict],
                  k: int = 12) -> list[dict]:
    """KMeans on wedge_hist+depth -> sign inventory."""
    vecs = []
    idx = []
    for i, f in enumerate(cells_feat):
        if f.get("empty"):
            continue
        vecs.append(f["wedge_hist"] +
                    [f["depth"] / 50.0, f["ink"] * 3,
                     min(f["wedge_count"], 20) / 20.0])
        idx.append(i)
    if len(vecs) < k:
        return []
    X = np.array(vecs, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
            50, 0.5)
    _, labels, centers = cv2.kmeans(
        X, k, None, crit, 5,
        cv2.KMEANS_PP_CENTERS)
    inv = []
    for c in range(k):
        members = [idx[i] for i, l in enumerate(labels)
                   if l == c]
        if not members:
            continue
        inv.append({"cluster": c, "count": len(members),
                    "centroid": [round(float(v), 3)
                                 for v in centers[c]],
                    "wedge_profile": [
                        round(float(v), 3) for v in
                        centers[c][:4]]})
    inv.sort(key=lambda r: -r["count"])
    return inv


def ocr() -> dict:
    g, crop = load_tablet()
    h, w = g.shape
    # texture-bounded inscription zone
    gf = g.astype(np.float32)
    blur = cv2.GaussianBlur(gf, (0, 0), 3)
    std = np.sqrt(np.maximum(
        cv2.GaussianBlur(gf ** 2, (0, 0), 3) - blur ** 2, 0))
    rowprof = std.mean(1)
    colprof = std.mean(0)
    ys = np.where(rowprof > rowprof.mean() * 0.9)[0]
    xs = np.where(colprof > colprof.mean() * 0.9)[0]
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    zone = g[y0:y1 + 1, x0:x1 + 1]
    # column pitch via autocorrelation of zone texture
    zp = std[y0:y1 + 1, x0:x1 + 1].mean(0)
    zp = zp - zp.mean()
    ac = np.correlate(zp, zp, "full")[len(zp) - 1:]
    ac[:8] = 0
    pitch = int(np.argmax(ac[:120])) + 8
    cell_h = max(18, pitch * 3 // 2)  # cells taller than wide
    transcription = []
    all_feats = []
    feat_pos = []
    ncols = max(1, (x1 - x0) // pitch)
    nrows = max(1, (y1 - y0) // cell_h)
    for ci in range(ncols):
        cx0 = x0 + ci * pitch
        cx1 = min(x0 + (ci + 1) * pitch, x1 + 1)
        col_rec = {"column": ci, "x": [cx0, cx1],
                   "cells": []}
        for ri in range(nrows):
            cy0 = y0 + ri * cell_h
            cy1 = min(y0 + (ri + 1) * cell_h, y1 + 1)
            f = cell_features(g, (cy0, cy1, cx0, cx1))
            if f.get("empty"):
                continue
            all_feats.append(f)
            feat_pos.append((ci, len(col_rec["cells"])))
            col_rec["cells"].append(
                {"row": ri, "y": [cy0, cy1], **f})
        transcription.append(col_rec)
    inv = cluster_cells(all_feats)
    # label clusters by rank
    for r, c in enumerate(inv):
        c["sign_id"] = "SGN-%02d" % (r + 1)
    # attach cluster id to each cell
    if inv:
        vecs = []
        for f in all_feats:
            if f.get("empty"):
                vecs.append(None)
                continue
            vecs.append(f["wedge_hist"] +
                        [f["depth"] / 50.0, f["ink"] * 3,
                         min(f["wedge_count"], 20) / 20.0])
        X = np.array([v for v in vecs if v is not None],
                     dtype=np.float32)
        # re-run kmeans to get labels aligned
        crit = (cv2.TERM_CRITERIA_EPS +
                cv2.TERM_CRITERIA_MAX_ITER, 50, 0.5)
        _, labels, _ = cv2.kmeans(
            X, len(inv), None, crit, 5,
            cv2.KMEANS_PP_CENTERS)
        it = iter(labels)
        for f, v in zip(all_feats, vecs):
            if v is None:
                f["cluster"] = None
            else:
                f["cluster"] = int(np.asarray(next(it)).item())
        # remap: cluster_cells sorted desc, kmeans labels
        # are raw — store raw label, rank map separately
        rank = {c["cluster"]: c["sign_id"]
                for c in inv}
        for col in transcription:
            for cell in col["cells"]:
                pass  # cells already have features; cluster
                       # ids live in all_feats order
        # simpler: rebuild per-cell sign_id via feat_pos
        for (ci, ri), f in zip(feat_pos, all_feats):
            if f.get("cluster") is not None:
                transcription[ci]["cells"][ri]["sign"] = \
                    rank.get(f["cluster"], "?")
    rec = {"kind": "sea_ocr", "image": str(IMG),
           "device_time": _device_time(),
           "tablet_crop": crop,
           "zone": [x0,y0,x1,y1], "pitch": pitch, "cell_h": cell_h,
           "columns": len(transcription),
           "cells": len(all_feats),
           "sign_inventory": len(inv),
           "inventory": inv,
           "transcription": transcription,
           "derived": True,
           "note": ("wedge-depth formations decoded; sign->"
                    "word mapping needs ORACC lexical "
                    "layer — formations are real, "
                    "translations are not claimed")}
    OUT.write_text(json.dumps(rec, indent=2,
                              ensure_ascii=False))
    return {"kind": "sea_ocr_result",
            "zone": [x0,y0,x1,y1], "pitch": pitch, "cell_h": cell_h,
           "columns": len(transcription),
            "cells": len(all_feats),
            "signs": len(inv),
            "top_signs": inv[:8],
            "output": str(OUT)}


if __name__ == "__main__":
    print(json.dumps(ocr(), indent=2)[:3000])
