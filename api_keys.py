"""ATLAS API key service — HuggingFace-style tokens.

signup -> login -> bearer key (`atlas_...`). Keys are persisted to
data/api_keys.json so they survive restarts (automatic recovery), and
every authenticated request is usage-tracked. Passwords are stored
only as SHA-256 hashes; keys are bearer tokens shown once at signup.
"""

import hashlib
import json
import secrets
import time
from pathlib import Path

ROOT = Path(__file__).parent
KEYS_PATH = ROOT / "data" / "api_keys.json"
KEY_PREFIX = "atlas_"


def _load() -> dict:
    try:
        return json.loads(KEYS_PATH.read_text())
    except Exception:
        return {"users": {}, "keys": {}}


def _save(db: dict):
    KEYS_PATH.parent.mkdir(exist_ok=True)
    KEYS_PATH.write_text(json.dumps(db, indent=1))


def _hash(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


def _new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(24)


def signup(username: str, password: str) -> dict:
    """Create an account and issue an API key. Returns the key once."""
    username = (username or "").strip().lower()
    if len(username) < 2 or len(password or "") < 4:
        return {"error": "username>=2 chars, password>=4 chars"}
    db = _load()
    if username in db["users"]:
        return {"error": "username taken"}
    key = _new_key()
    db["users"][username] = {"pw": _hash(password), "created": time.time()}
    db["keys"][key] = {"username": username, "created": time.time(),
                       "uses": 0, "last_used": None, "revoked": False}
    _save(db)
    return {"username": username, "key": key, "created": time.time()}


def login(username: str, password: str) -> dict:
    """Verify credentials; returns existing key or issues a new one."""
    username = (username or "").strip().lower()
    db = _load()
    user = db["users"].get(username)
    if not user or user["pw"] != _hash(password or ""):
        return {"error": "invalid credentials"}
    # reuse a live key if one exists, else mint
    for k, meta in db["keys"].items():
        if meta["username"] == username and not meta.get("revoked"):
            return {"username": username, "key": k}
    key = _new_key()
    db["keys"][key] = {"username": username, "created": time.time(),
                       "uses": 0, "last_used": None, "revoked": False}
    _save(db)
    return {"username": username, "key": key}


def check_key(key: str) -> dict | None:
    """Validate a bearer key and record usage. None if invalid/revoked."""
    if not key or not key.startswith(KEY_PREFIX):
        return None
    db = _load()
    meta = db["keys"].get(key)
    if not meta or meta.get("revoked"):
        return None
    meta["uses"] = meta.get("uses", 0) + 1
    meta["last_used"] = time.time()
    _save(db)
    return {"username": meta["username"], "uses": meta["uses"]}


def revoke(username: str, password: str) -> dict:
    username = (username or "").strip().lower()
    db = _load()
    user = db["users"].get(username)
    if not user or user["pw"] != _hash(password or ""):
        return {"error": "invalid credentials"}
    n = 0
    for meta in db["keys"].values():
        if meta["username"] == username:
            meta["revoked"] = True
            n += 1
    _save(db)
    return {"revoked": n}


def status() -> dict:
    """Key inventory for the node panel — metadata only, no secrets."""
    db = _load()
    keys = [{"username": m["username"], "uses": m.get("uses", 0),
             "last_used": m.get("last_used"), "revoked": m.get("revoked", False),
             "created": m.get("created")}
            for m in db["keys"].values()]
    return {"users": len(db["users"]), "keys": keys,
            "storage": str(KEYS_PATH)}
