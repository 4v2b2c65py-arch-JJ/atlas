"""Sea — preserved text/vocab section.

Important data blocks land here VERBATIM: raw text stored
byte-for-byte, tokenized alongside, never normalized or
rewritten. Each block gets a name, families are suggested
but the raw always wins.

Store: data/sea/<name>.json + data/sea/index.jsonl
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SEA = DATA / "sea"
INDEX = SEA / "index.jsonl"


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _load(name: str) -> dict | None:
    p = SEA / (_slug(name) + ".json")
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def _index(rec: dict) -> None:
    SEA.mkdir(parents=True, exist_ok=True)
    with INDEX.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def add(name: str, raw: str,
        families: dict | None = None,
        note: str = "") -> dict:
    """Store a raw text block verbatim + token view."""
    slug = _slug(name)
    tokens = raw.split()
    block = {"kind": "sea_block", "name": name, "slug": slug,
             "raw": raw, "tokens": tokens,
             "token_count": len(tokens),
             "unique_count": len(set(t.lower()
                                      for t in tokens)),
             "families": families or {},
             "note": note,
             "added": _device_time(),
             "preserved": True}
    SEA.mkdir(parents=True, exist_ok=True)
    (SEA / (slug + ".json")).write_text(
        json.dumps(block, ensure_ascii=False, indent=2))
    _index({"kind": "sea_index", "name": name,
            "slug": slug, "tokens": len(tokens),
            "device_time": _device_time()})
    try:
        import eventbus
        eventbus.emit("sea_block", {"name": name,
                                    "tokens": len(tokens)})
    except Exception:
        pass
    return block


def get(name: str) -> dict:
    b = _load(name)
    if not b:
        return {"error": "not_found", "name": name}
    return b


def listing() -> list[dict]:
    out = []
    if SEA.exists():
        for p in sorted(SEA.glob("*.json")):
            if p.name == "index.jsonl":
                continue
            try:
                b = json.loads(p.read_text())
                out.append({"name": b.get("name"),
                            "slug": b.get("slug"),
                            "tokens": b.get("token_count"),
                            "added": b.get("added")})
            except Exception:
                continue
    return out


def status() -> dict:
    blocks = listing()
    return {"kind": "sea_state",
            "device_time": _device_time(),
            "blocks": len(blocks), "listing": blocks,
            "note": ("raw text blocks preserved verbatim; "
                     "tokens are a view, never a rewrite")}
