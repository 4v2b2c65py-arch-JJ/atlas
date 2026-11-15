"""Scheduler — periodic git-history + maintenance ticks.

Tasks run on a tick() call (cron/launchd/CLI driven):
  - boardctl_scan: kernel module diff + board state sync
  - nodereg_forward: unified node registry -> git bundle
  - git_sync: commit any pending history changes

Every run is recorded to data/scheduler/runs.jsonl with
device-local timestamps. install() emits a launchd plist so
the schedule survives reboots — 'store history via git
schedules' made literal.

Store: data/scheduler/{runs.jsonl, schedule.json}
"""

import json
import plistlib
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "scheduler"
RUNS = OUT / "runs.jsonl"
SCHED = OUT / "schedule.json"

DEFAULT_INTERVAL_S = 3600  # hourly


def _device_time() -> dict:
    now = datetime.now().astimezone()
    return {"ts": now.timestamp(),
            "iso": now.isoformat(timespec="milliseconds"),
            "timezone": now.tzname() or "local",
            "date": now.date().isoformat()}


def _append(rec: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with RUNS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _cfg() -> dict:
    if SCHED.exists():
        try:
            return json.loads(SCHED.read_text())
        except Exception:
            pass
    return {"interval_s": DEFAULT_INTERVAL_S,
            "tasks": ["boardctl_scan", "nodereg_forward",
                      "git_sync"],
            "enabled": True}


def set_schedule(interval_s: int | None = None,
                 tasks: list | None = None,
                 enabled: bool | None = None) -> dict:
    cfg = _cfg()
    if interval_s is not None:
        cfg["interval_s"] = int(interval_s)
    if tasks is not None:
        cfg["tasks"] = tasks
    if enabled is not None:
        cfg["enabled"] = bool(enabled)
    OUT.mkdir(parents=True, exist_ok=True)
    SCHED.write_text(json.dumps(cfg, indent=2))
    return {"kind": "schedule_set", **cfg}


def _run_task(name: str) -> dict:
    try:
        if name == "boardctl_scan":
            import boardctl
            boardctl.scan_kernel()
            return boardctl.git_sync()
        if name == "nodereg_forward":
            import nodereg
            return nodereg.forward()
        if name == "git_sync":
            import boardctl
            return boardctl.git_sync()
        return {"task": name, "error": "unknown_task"}
    except Exception as e:
        return {"task": name, "error": str(e)}


def tick() -> dict:
    """Run all scheduled tasks once; record the run."""
    cfg = _cfg()
    results = []
    for t in (cfg.get("tasks") or []):
        r = _run_task(t)
        results.append({"task": t,
                        "ok": "error" not in r,
                        "result": r})
    rec = {"ts": _device_time()["ts"],
           "device_time": _device_time(),
           "kind": "schedule_tick",
           "enabled": cfg.get("enabled", True),
           "tasks": results, "derived": True}
    _append(rec)
    try:
        import eventbus
        eventbus.emit("schedule_tick", rec)
    except Exception:
        pass
    return rec


def status() -> dict:
    runs = []
    if RUNS.exists():
        for line in RUNS.open():
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    cfg = _cfg()
    last = runs[-1] if runs else None
    return {"kind": "schedule_state",
            "device_time": _device_time(),
            "config": cfg, "runs": len(runs),
            "last_run": last,
            "next_due_in_s": (cfg.get("interval_s", 0)
                              if last is None else max(
                0, cfg.get("interval_s", 0) -
                (time.time() - last["ts"])))}


def install() -> dict:
    """Write a launchd plist that ticks every interval —
    git history on a real OS schedule."""
    cfg = _cfg()
    interval = cfg.get("interval_s", DEFAULT_INTERVAL_S)
    label = "ai.atlas.scheduler"
    plist = {
        "Label": label,
        "ProgramArguments": [
            str(ROOT / ".venv" / "bin" / "python"),
            str(ROOT / "atlas_cli.py"), "schedule", "tick"],
        "StartInterval": interval,
        "WorkingDirectory": str(ROOT),
        "StandardOutPath": str(OUT / "launchd.out.log"),
        "StandardErrorPath": str(OUT / "launchd.err.log"),
    }
    dest = Path.home() / "Library" / "LaunchAgents" / \
        (label + ".plist")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(plistlib.dumps(plist))
    return {"kind": "schedule_install",
            "plist": str(dest), "interval_s": interval,
            "load": ("launchctl load %s" % dest),
            "note": ("run the load command, or call tick() "
                     "from cron: */%d * * * * cd %s && "
                     ".venv/bin/python atlas_cli.py "
                     "schedule tick" %
                     (max(1, interval // 60), ROOT))}
