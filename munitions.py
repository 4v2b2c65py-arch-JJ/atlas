"""munitions — holder + cartridge + refill.

Add munitions to the holder: cartridges are loaded under
the `refill` command from sea seed blocks — typed token
content with nodded (indexed) sea seeds. Each cartridge
= one sea block's verbatim tokens, stamped with its
seed slug; the holder is the magazine they feed.

  refill   — load cartridges from all sea blocks
  status   — holder contents
  fire <n> — pop cartridge n (returns its tokens)

Store: data/munitions/holder.json + cartridges.jsonl
"""

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data" / "munitions"
HOLDER = DATA / "holder.json"
CARTS = DATA / "cartridges.jsonl"


def _now() -> dict:
    n = datetime.now().astimezone()
    return {"ts": n.timestamp(),
            "iso": n.isoformat(timespec="milliseconds"),
            "timezone": n.tzname() or "local",
            "date": n.date().isoformat()}


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _load_holder() -> dict:
    if HOLDER.exists():
        return json.loads(HOLDER.read_text())
    return {"kind": "munitions_holder", "capacity": 64,
            "loaded": [], "device_time": _now()}


def _save_holder(h: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    HOLDER.write_text(json.dumps(h, ensure_ascii=False,
                                 indent=2))


def refill() -> dict:
    """Load cartridges: every sea block -> one cartridge
    of typed token content, seed-slug stamped."""
    import sea
    blocks = sea.listing()
    DATA.mkdir(parents=True, exist_ok=True)
    h = _load_holder()
    loaded = 0
    with CARTS.open("a") as f:
        for b in blocks:
            name = b.get("name") or b.get("slug")
            if not name:
                continue
            blk = sea.get(name)
            if blk.get("error"):
                continue
            cart = {"kind": "cartridge",
                    "device_time": _now(),
                    "seed_slug": blk.get("slug", name),
                    "seed_name": name,
                    "tokens": blk.get("tokens", []),
                    "token_count": blk.get("token_count", 0),
                    "typed": True,
                    "nodded": True,
                    "note": ("sea seed cartridge — typed "
                             "token content, verbatim")}
            f.write(json.dumps(cart, ensure_ascii=False)
                    + "\n")
            slug = cart["seed_slug"]
            if slug not in h["loaded"]:
                h["loaded"].append(slug)
            loaded += 1
    h["device_time"] = _now()
    _save_holder(h)
    try:
        import eventbus
        eventbus.emit("munitions_refill",
                      {"cartridges": loaded})
    except Exception:
        pass
    return {"kind": "munitions_refill",
            "device_time": _now(),
            "cartridges": loaded,
            "holder_loaded": len(h["loaded"]),
            "capacity": h["capacity"]}


def fire(n: int | None = None) -> dict:
    """Pop a cartridge — by index or seed slug."""
    carts = list(_jsonl(CARTS) or [])
    if not carts:
        return {"error": "empty_magazine",
                "hint": "refill first"}
    if isinstance(n, str):
        hit = [c for c in carts
               if c.get("seed_slug") == n or
               c.get("seed_name") == n]
        cart = hit[-1] if hit else None
    else:
        cart = carts[n] if n is not None and \
            0 <= n < len(carts) else carts[-1]
    if not cart:
        return {"error": "no_cartridge", "n": n}
    return {"kind": "cartridge_fire",
            "device_time": _now(),
            "seed_slug": cart["seed_slug"],
            "seed_name": cart["seed_name"],
            "tokens": cart["tokens"],
            "token_count": cart["token_count"]}


def status() -> dict:
    h = _load_holder()
    carts = list(_jsonl(CARTS) or [])
    return {"kind": "munitions_state",
            "device_time": _now(),
            "holder": {"loaded": len(h["loaded"]),
                       "capacity": h["capacity"],
                       "seeds": h["loaded"][:20]},
            "cartridges": len(carts),
            "note": ("munitions holder — sea seeds as "
                     "typed token cartridges")}
