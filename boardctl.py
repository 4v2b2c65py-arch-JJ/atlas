"""BoardCtl — internal kernel updates + modem/board register
tracker with git-scheduled history.

KERNEL UPDATES: scan_kernel() hashes every top-level module
and diffs against last scan — changed/new/removed modules
become 'krnl_update' records. This tracks the internal
kernel (our .py surface) evolving.

BOARD RECONFIGURATION: modem-style positional layout —
registers addressed by position hold {num, val} pairs.
eeprom_write(bank, addr, val) models EPROM/EEPROM writes;
set_reg(addr, num, val) reconfigures the live board.
Every write is a history record.

GIT SCHEDULED HISTORY: history lives in jsonl AND gets
committed to git on a schedule — git_sync() stages the
tracker files and commits with a boardctl trailer.
AUTO_GIT config flag controls per-write sync; manual
git_sync always available.

Store: data/boardctl/{kernel.jsonl, writes.jsonl,
       board_state.json, .git_synced}
"""

import hashlib
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "boardctl"
KERNEL = OUT / "kernel.jsonl"
WRITES = OUT / "writes.jsonl"
STATE = OUT / "board_state.json"
SYNCED = OUT / ".git_synced"
GIT_PATHS = ["data/boardctl"]

AUTO_GIT = False   # per-write commits are opt-in; manual
                   # git_sync always works


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _append(path: Path, rec: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except json.JSONDecodeError:
            pass
    return {"kind": "board_state", "layout": {},
            "eeprom": {}, "kernel_hashes": {}}


def _save_state(st: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1))


def _module_hashes() -> dict:
    """sha256[:16] per top-level .py module — the kernel
    surface."""
    out = {}
    for p in sorted(ROOT.glob("*.py")):
        try:
            out[p.name] = hashlib.sha256(
                p.read_bytes()).hexdigest()[:16]
        except Exception:
            pass
    return out


def scan_kernel() -> dict:
    """Diff module hashes vs last scan — updates, additions,
    removals become krnl_update records."""
    st = _load_state()
    old = st.get("kernel_hashes", {})
    new = _module_hashes()
    changes = []
    for name, h in new.items():
        if name not in old:
            changes.append({"module": name, "change": "added",
                            "hash": h})
        elif old[name] != h:
            changes.append({"module": name,
                            "change": "updated",
                            "old": old[name], "new": h})
    for name in old:
        if name not in new:
            changes.append({"module": name,
                            "change": "removed"})
    for c in changes:
        _append(KERNEL, {"ts": _device_time()["ts"],
                         "device_time": _device_time(),
                         "kind": "krnl_update", **c})
    st["kernel_hashes"] = new
    _save_state(st)
    return {"kind": "krnl_scan", "modules": len(new),
            "changes": len(changes), "detail": changes[:50]}


def set_reg(addr: int, num, val, note: str = "") -> dict:
    """Reconfigure a board register — positional num/val.
    History record + live board state."""
    st = _load_state()
    pos = str(addr)
    prev = st["layout"].get(pos)
    st["layout"][pos] = {"num": num, "val": val,
                         "ts": _device_time()["ts"],
                         "note": note}
    _save_state(st)
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "board_write", "addr": addr,
           "num": num, "val": val, "note": note,
           "prev": prev, "derived": True}
    _append(WRITES, rec)
    try:
        import eventbus
        eventbus.emit("board_write", rec)
    except Exception:
        pass
    if AUTO_GIT:
        git_sync()
    return rec


def eeprom_write(bank: str, addr: int, val,
                 size: int = 1) -> dict:
    """EPROM/EEPROM write — modem-like nonvolatile store.
    Tracked per bank+addr with prev value."""
    st = _load_state()
    key = "%s:%s" % (bank, addr)
    prev = (st["eeprom"].get(bank) or {}).get(str(addr))
    st.setdefault("eeprom", {}).setdefault(
        bank, {})[str(addr)] = {"val": val, "size": size,
                               "ts": _device_time()["ts"]}
    _save_state(st)
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "eeprom_write", "bank": bank,
           "addr": addr, "val": val, "size": size,
           "prev": prev, "derived": True}
    _append(WRITES, rec)
    try:
        import eventbus
        eventbus.emit("eeprom_write", rec)
    except Exception:
        pass
    if AUTO_GIT:
        git_sync()
    return rec


def board() -> dict:
    """Current positional layout — all num/val registers."""
    st = _load_state()
    return {"kind": "board_state",
            "layout": st.get("layout", {}),
            "eeprom_banks": {
                b: len(v) for b, v in
                st.get("eeprom", {}).items()},
            "registers": len(st.get("layout", {}))}


def history(limit: int = 100) -> dict:
    recs = list(_jsonl(WRITES) or []) + \
        list(_jsonl(KERNEL) or [])
    recs.sort(key=lambda r: r.get("ts") or 0)
    return {"kind": "board_history",
            "count": len(recs),
            "records": recs[-limit:]}


def git_sync(msg: str | None = None) -> dict:
    """Commit tracker history to git — the scheduled store.
    Stages only data/boardctl paths; no-op when clean."""
    try:
        # -f: data/ is gitignored but boardctl history is
        # MEANT to be committed — force-track these paths
        subprocess.run(["git", "add", "-f"] + GIT_PATHS,
                       cwd=str(ROOT), capture_output=True,
                       timeout=30)
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet",
             "--"] + GIT_PATHS, cwd=str(ROOT))
        if diff.returncode == 0:
            return {"kind": "git_sync", "committed": False,
                    "reason": "clean"}
        msg = msg or ("boardctl: %s kernel=%d writes=%d" % (
            _device_time()["date"],
            sum(1 for _ in _jsonl(KERNEL) or []),
            sum(1 for _ in _jsonl(WRITES) or [])))
        r = subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=str(ROOT), capture_output=True, text=True,
            timeout=30)
        SYNCED.write_text(json.dumps(
            {"ts": time.time(), "msg": msg,
             "ok": r.returncode == 0}))
        return {"kind": "git_sync", "committed":
                r.returncode == 0, "msg": msg,
                "output": r.stdout.strip()[-200:]}
    except Exception as e:
        return {"kind": "git_sync", "committed": False,
                "error": str(e)[:200]}


def status() -> dict:
    st = _load_state()
    return {"kind": "boardctl_state",
            "device_time": _device_time(),
            "kernel_modules": len(st.get("kernel_hashes",
                                         {})),
            "kernel_updates": sum(1 for _ in
                                  _jsonl(KERNEL) or []),
            "writes": sum(1 for _ in _jsonl(WRITES) or []),
            "registers": len(st.get("layout", {})),
            "eeprom_banks": len(st.get("eeprom", {})),
            "auto_git": AUTO_GIT,
            "note": ("positional num/val board + eeprom banks; "
                     "kernel diffs tracked; git_sync commits "
                     "history")}
