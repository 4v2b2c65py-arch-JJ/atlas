"""Per-source site registry — credentials + session state for
every media site ATLAS touches (scan sites, social, anime, etc).

Secrets (passwords) live only in data/sites.json (gitignored) and
are never returned by list endpoints — callers get "***".
Session outcomes (login ts, cookie counts) are safe metadata.
"""

import json
import time
from pathlib import Path

ROOT = Path(__file__).parent
PATH = ROOT / "data" / "sites.json"


def _load() -> dict:
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return {"sites": {}}


def _save(st: dict) -> None:
    PATH.parent.mkdir(exist_ok=True)
    PATH.write_text(json.dumps(st, indent=1))


def list_sites() -> dict:
    """Public view — passwords redacted."""
    st = _load()
    out = {}
    for name, s in st.get("sites", {}).items():
        out[name] = {k: v for k, v in s.items() if k != "password"}
        if s.get("password"):
            out[name]["password"] = "***"
        out[name]["has_password"] = bool(s.get("password"))
    return {"sites": out}


def add(name: str, url: str, username: str = "",
        password: str = "", kind: str = "") -> dict:
    name = (name or "").strip().lower()
    if not name or not url:
        return {"error": "name and url required"}
    st = _load()
    st.setdefault("sites", {})[name] = {
        "url": url, "username": username, "password": password,
        "kind": kind or "", "added": time.time(),
        "last_login": None, "login_ok": None}
    _save(st)
    return {"added": name}


def remove(name: str) -> dict:
    st = _load()
    gone = st.get("sites", {}).pop(name.lower(), None)
    _save(st)
    return {"removed": name, "existed": bool(gone)}


def get(name: str) -> dict | None:
    """Full record incl. secrets — internal use only (login)."""
    return _load().get("sites", {}).get(name.lower())


def mark_login(name: str, ok: bool, detail: dict | None = None
               ) -> None:
    """Record a session outcome — safe metadata only."""
    st = _load()
    s = st.get("sites", {}).get(name.lower())
    if not s:
        return
    s["last_login"] = time.time()
    s["login_ok"] = bool(ok)
    if detail:
        s["last_detail"] = {k: v for k, v in detail.items()
                            if k not in ("password", "username")}
    _save(st)
