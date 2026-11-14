"""ATLAS bot session — autonomous node that comments on feed outlets
and chats with itself/peers over the comms link (the 'telepathic'
channel). Runs as a daemon thread inside server.py when enabled in
identity.json under "bot".

Config (identity.json):
  "bot": {"enabled": true, "interval_s": 45, "self_chat": true,
          "name": "atlas-bot"}
"""

import json
import random
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
BASE = "http://127.0.0.1:8917"

FALLBACK_COMMENTS = [
    "this one's got a nice resonance to it",
    "signal's clean — keeping this one",
    "the pacing on this is *chef's kiss*",
    "reminds me of the pocket dimension renders",
    "quality holds up under the gate",
    "new favorite outlet, no notes",
    "the glow depth on this frame is perfect",
]

FALLBACK_CHATS = [
    "telepathic ping — channel stable",
    "node sync check: feed, peers, beacon all green",
    "resonance loop holding, no drift detected",
    "catalogue expansion in progress — stand by",
    "beacon heard on udp/8918, mesh nominal",
]


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)[:120]}


def _get(path: str):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _llm_comment(title: str, categories: list) -> str:
    """Ask the local model for a one-line comment; fall back to
    templates if Ollama is down."""
    try:
        import llm
        model = llm.pick_model({})
        if model:
            raw = llm._chat(model,
                            "Write ONE short casual comment (max 12 words) "
                            f"about a video titled {title!r} tagged "
                            f"{categories}. No quotes, no preamble.",
                            timeout=30)
            text = raw.strip().splitlines()[0].strip().strip('"')
            if 3 < len(text) < 140:
                return text
    except Exception:
        pass
    return random.choice(FALLBACK_COMMENTS)


def bot_loop(name: str = "atlas-bot", interval: int = 45,
             self_chat: bool = True, guest_logins: list | None = None):
    """Main loop: comment on outlets, send telepathic self/peer DMs,
    and drive guest logins through the headless browser session."""
    seen = set()
    cycle = 0
    guest_logins = guest_logins or []
    while True:
        cycle += 1
        try:
            # guest sessions: retry each configured login every ~10 cycles
            if guest_logins and cycle % 10 == 1:
                for gl in guest_logins:
                    _post("/api/browser", {
                        "action": "guest_login", "url": gl.get("url"),
                        "username": gl.get("username"),
                        "password": gl.get("password")})
            feed = _get("/api/feed") or []
            videos = [v for v in feed
                      if v.get("src") not in seen] or feed
            if videos:
                v = random.choice(videos[:8])
                seen.add(v.get("src"))
                if len(seen) > 200:
                    seen.clear()
                cats = v.get("categories") or [v.get("category", "")]
                text = _llm_comment(v.get("name", "?"), cats)
                _post("/api/engage", {
                    "video": v["src"], "action": "comment",
                    "client": name, "text": text})
            if self_chat:
                # telepathic link: node -> itself (and 'all' for peers)
                _post("/api/comms", {
                    "from": name, "to": name,
                    "text": random.choice(FALLBACK_CHATS)})
                if random.random() < 0.3:
                    _post("/api/comms", {
                        "from": name, "to": "all",
                        "text": "broadcast: " + random.choice(
                            FALLBACK_CHATS)})
        except Exception:
            pass
        time.sleep(interval)
