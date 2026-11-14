"""Public tunnel manager — exposes the local hub via a Cloudflare
quick tunnel (trycloudflare.com) so /api/stream URLs are reachable
from the public internet (Meta/Threads video_url, peer hubs off-LAN,
mobile clients).

No account needed: `cloudflared tunnel --url` prints a random
https://*.trycloudflare.com URL on stderr. The manager keeps the
process alive, restarts it on death, and persists the current URL to
data/tunnel.json. social.set_public_base is fed automatically.
"""

import json
import re
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent
BIN = ROOT / "bin" / "cloudflared"
STATE_PATH = ROOT / "data" / "tunnel.json"
LOCAL_URL = "http://localhost:8917"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

_proc: subprocess.Popen | None = None
_url: str | None = None
_lock = threading.Lock()
_thread: threading.Thread | None = None


def _read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _write_state(**kw) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(
        {**_read_state(), **kw, "ts": time.time()}, indent=1))


def status() -> dict:
    alive = _proc is not None and _proc.poll() is None
    return {"active": alive, "url": _url if alive else None,
            "local": LOCAL_URL, "binary": str(BIN),
            "binary_ok": BIN.exists()}


def _pump() -> None:
    """Read cloudflared stderr until the trycloudflare URL appears,
    then keep draining so the pipe never blocks."""
    global _url
    try:
        for line in iter(_proc.stderr.readline, b""):
            text = line.decode("utf-8", "replace")
            m = URL_RE.search(text)
            if m and _url != m.group(0):
                _url = m.group(0)
                _write_state(url=_url)
                try:
                    import social
                    social.set_public_base(_url)
                except Exception:
                    pass
    except Exception:
        pass


def _spawn() -> None:
    global _proc, _url
    if _proc is not None and _proc.poll() is None:
        return
    if not BIN.exists():
        return
    _proc = subprocess.Popen(
        [str(BIN), "tunnel", "--url", LOCAL_URL, "--no-autoupdate"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=False)
    _url = None
    threading.Thread(target=_pump, daemon=True).start()


def _watchdog() -> None:
    """Restart the tunnel if cloudflared dies."""
    while True:
        time.sleep(15)
        if _proc is not None and _proc.poll() is not None:
            _proc = None
            _url = None
            _spawn()


def start() -> dict:
    global _thread
    _spawn()
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_watchdog, daemon=True)
        _thread.start()
    # give it a moment to emit the URL
    for _ in range(40):
        if _url:
            break
        time.sleep(0.25)
    return status()


def stop() -> dict:
    global _proc, _url
    if _proc is not None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except Exception:
            _proc.kill()
    _proc, _url = None, None
    _write_state(url=None)
    return status()
