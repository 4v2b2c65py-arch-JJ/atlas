"""Spot and download data stream videos.

Reads candidate URLs (one per line) from a file or argv, spots the ones that
are actually video streams, and downloads them into ./videos/.

Usage:
    python3 spot_download.py urls.txt
    python3 spot_download.py https://example.com/feed.mp4 ...
"""

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

OUT_DIR = Path(__file__).parent / "videos"
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
TIMEOUT = 30


def spot(url: str) -> dict:
    """Check whether a URL serves a video stream."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            ctype = r.headers.get("Content-Type", "")
            clength = r.headers.get("Content-Length")
            final = r.geturl()
    except Exception:
        # some servers reject HEAD; fall back to a ranged GET
        try:
            req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                ctype = r.headers.get("Content-Type", "")
                clength = r.headers.get("Content-Length")
                final = r.geturl()
        except Exception as e:
            return {"url": url, "is_video": False, "error": str(e)}

    is_video = ctype.startswith("video/") or any(
        final.lower().split("?")[0].endswith(e) for e in VIDEO_EXTS
    )
    return {
        "url": url,
        "final_url": final,
        "content_type": ctype,
        "size": int(clength) if clength and clength.isdigit() else None,
        "is_video": is_video,
    }


def download(info: dict) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    name = info["final_url"].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    if not Path(name).suffix:
        name += ".mp4"
    dest = OUT_DIR / name
    subprocess.run(
        ["curl", "-fL", "--retry", "2", "-o", str(dest), info["final_url"]],
        check=True,
    )
    return dest


def probe(path: Path) -> dict:
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(out.stdout)
        vstream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            {},
        )
        return {
            "duration": float(data.get("format", {}).get("duration", 0)),
            "codec": vstream.get("codec_name"),
            "width": vstream.get("width"),
            "height": vstream.get("height"),
        }
    except Exception:
        head = path.read_bytes()[:32]
        container = "mp4" if head[4:8] == b"ftyp" else "unknown"
        return {
            "container": container,
            "size_bytes": path.stat().st_size,
            "ffprobe": "unavailable",
        }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    urls = (
        [u.strip() for u in Path(argv[0]).read_text().splitlines() if u.strip()]
        if len(argv) == 1 and Path(argv[0]).exists()
        else argv
    )

    results = []
    for url in urls:
        info = spot(url)
        results.append(info)
        if not info["is_video"]:
            print(f"skip  {url}  ({info.get('error') or info.get('content_type')})")
            continue
        dest = download(info)
        meta = probe(dest)
        detail = (
            f"{meta.get('width')}x{meta.get('height')} {meta.get('codec')} "
            f"{meta.get('duration', 0):.1f}s"
            if "codec" in meta
            else f"{meta.get('container')} {meta.get('size_bytes')}B "
                 f"(ffprobe unavailable)"
        )
        print(f"got   {dest.name}  {detail}")
        results[-1]["downloaded"] = str(dest)
        results[-1]["probe"] = meta

    (OUT_DIR / "report.json").write_text(json.dumps(results, indent=2))
    print(f"\nreport -> {OUT_DIR / 'report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
