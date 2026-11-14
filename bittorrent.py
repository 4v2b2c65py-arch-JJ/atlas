"""BitTorrent plugin — resolves magnet links via a local webtorrent
node runtime (bt/bt_download.mjs). Pluggable: returns None when the
runtime isn't installed so callers can fall back to peer resolution.
"""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent
BT_DIR = ROOT / "bt"
SCRIPT = BT_DIR / "bt_download.mjs"


def available() -> bool:
    return (SCRIPT.exists()
            and shutil.which("node") is not None
            and (BT_DIR / "node_modules" / "webtorrent").exists())


def resolve(uri: str, workdir: Path, timeout: int = 600) -> Path | None:
    """Download the largest video file from a magnet/.torrent URI.
    Returns the produced file path, or None on failure/timeout."""
    if not available():
        return None
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            ["node", str(SCRIPT), uri, str(workdir), str(timeout)],
            capture_output=True, text=True, timeout=timeout + 30,
            cwd=str(BT_DIR))
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    vids = [p for p in workdir.glob("video.*")
            if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi")
            and p.stat().st_size > 50000]
    return vids[0] if vids else None
