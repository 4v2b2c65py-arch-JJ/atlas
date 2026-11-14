"""Vision layer: reverse-image search + AI identification for ATLAS.

dHash perceptual matching runs with zero model deps. If Ollama has a
vision-capable model (llava, moondream, minicpm-v, qwen-vl, gemma3),
it captions the query image for semantic tags on top.
"""

import base64
import io
import json
import urllib.request
from pathlib import Path

import cv2
import numpy as np

import llm

ROOT = Path(__file__).parent
THUMB_CACHE = ROOT / "data" / "thumb_cache.json"
VISION_HINTS = ("llava", "moondream", "minicpm", "bakllava", "qwen",
                "gemma3", "vision", "llama3.2-vision")


def _load_cache() -> dict:
    try:
        return json.loads(THUMB_CACHE.read_text())
    except Exception:
        return {}


def _save_cache(c: dict):
    THUMB_CACHE.parent.mkdir(exist_ok=True)
    THUMB_CACHE.write_text(json.dumps(c))


def dhash_from_gray(gray: np.ndarray) -> int:
    """64-bit difference hash: resize 9x8, compare adjacent pixels."""
    img = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | (1 if img[r, c] > img[r, c + 1] else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def video_dhash(path: str, frac: float = 0.5) -> int | None:
    """dHash of a video frame (default: middle). Cached by mtime+size."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    cache = _load_cache()
    key = str(p)
    ent = cache.get(key)
    if ent and ent.get("mtime") == st.st_mtime and ent.get("size") == st.st_size:
        return ent.get("hash")
    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        return None
    cap.set(cv2.CAP_PROP_POS_FRAMES,
            int(cap.get(cv2.CAP_PROP_FRAME_COUNT) * frac))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h = dhash_from_gray(gray)
    cache[key] = {"mtime": st.st_mtime, "size": st.st_size, "hash": h}
    _save_cache(cache)
    return h


def image_dhash(image_bytes: bytes) -> int | None:
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    return dhash_from_gray(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))


def color_tags(image_bytes: bytes) -> list[str]:
    """Fallback 'identification': dominant color/brightness statistics."""
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return []
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mean_v = float(hsv[..., 2].mean())
    mean_s = float(hsv[..., 1].mean())
    mean_h = float(hsv[..., 0].mean())
    tags = []
    if mean_v < 60:
        tags.append("dark")
    elif mean_v > 170:
        tags.append("bright")
    if mean_s < 50:
        tags.append("muted" if mean_v < 120 else "pale")
    else:
        if mean_h < 15 or mean_h > 170:
            tags.append("warm")
        elif mean_h < 40:
            tags.append("orange")
        elif mean_h < 80:
            tags.append("green")
        elif mean_h < 130:
            tags.append("cyan")
        else:
            tags.append("blue" if mean_h < 150 else "purple")
    return tags


def vision_model() -> str | None:
    """First Ollama model that looks vision-capable, else None."""
    for name in llm.list_models():
        if any(h in name.lower() for h in VISION_HINTS):
            return name
    return None


def caption_image(image_bytes: bytes, model: str | None = None) -> dict | None:
    """Ask a local vision model to identify the image. Returns
    {description, tags} or None when no vision model exists."""
    model = model or vision_model()
    if not model:
        return None
    b64 = base64.b64encode(image_bytes).decode()
    prompt = (
        "Identify this image for a video search engine. Reply ONLY with JSON: "
        '{"description": "<=20 words", "tags": ["tag1","tag2","tag3"]}'
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt,
                      "images": [b64]}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2, "num_predict": 200},
    }
    try:
        req = urllib.request.Request(
            f"{llm.OLLAMA}/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = json.loads(r.read())["message"]["content"]
        data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        return {
            "description": str(data.get("description", ""))[:200],
            "tags": [str(t).lower() for t in data.get("tags", [])][:10],
            "model": model,
        }
    except Exception:
        return None


def visual_search(image_bytes: bytes, candidates: list[dict],
                  top_k: int = 8) -> dict:
    """Reverse-image lookup: hash match against outlet frames + optional
    vision caption, expanded into search terms."""
    qhash = image_dhash(image_bytes)
    scored = []
    if qhash is not None:
        for c in candidates:
            src = c.get("src") or c.get("path")
            if not src:
                continue
            full = ROOT / src if not Path(src).is_absolute() else Path(src)
            vh = video_dhash(str(full))
            if vh is None:
                continue
            dist = hamming(qhash, vh)
            scored.append({**{k: c[k] for k in
                              ("src", "name", "category", "width", "height",
                               "duration", "size") if k in c},
                           "distance": dist,
                           "similarity": round(max(0.0, 1 - dist / 64), 3)})
    scored.sort(key=lambda x: x["distance"])

    caption = caption_image(image_bytes)
    tags = list((caption or {}).get("tags", [])) + color_tags(image_bytes)
    return {
        "hash": format(qhash, "016x") if qhash is not None else None,
        "vision": caption,
        "tags": sorted(set(tags)),
        "matches": scored[:top_k],
    }
