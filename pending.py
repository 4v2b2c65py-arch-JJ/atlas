"""pending — job queue shielded from shell shock.

Long jobs (XTTS clones, decompile passes, installs) die
when the shell session is interrupted. pending.submit()
launches them DETACHED via nohup — the process reparents
to launchd, survives shell death, and reports through
jobs.jsonl + per-job logs. Pending coms = commands that
outlive the shell that fired them.

  submit <cmd...>   — run detached, returns job id
  status            — alive/dead per job
  log <id>          — tail the job's output

Store: data/pending/jobs.jsonl + logs/<id>.log
"""

import json
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data" / "pending"
JOBS = DATA / "jobs.jsonl"
LOGS = DATA / "logs"


def _now() -> dict:
    n = datetime.now().astimezone()
    return {"ts": n.timestamp(),
            "iso": n.isoformat(timespec="milliseconds"),
            "timezone": n.tzname() or "local",
            "date": n.date().isoformat()}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def submit(cmd: list[str] | str,
           name: str | None = None) -> dict:
    """Launch cmd detached — survives shell interrupt."""
    DATA.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    jid = "job_%d" % int(time.time() * 1000)
    if isinstance(cmd, str):
        cmd = ["bash", "-lc", cmd]
    log = LOGS / (jid + ".log")
    lf = log.open("ab")
    # new session + nohup: immune to SIGHUP on parent death
    p = subprocess.Popen(
        cmd, cwd=str(ROOT), stdout=lf,
        stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True)
    rec = {"kind": "pending_job", "id": jid,
           "name": name or jid, "cmd": cmd,
           "pid": p.pid, "log": str(log.relative_to(ROOT)),
           "submitted": _now(), "status": "running"}
    with JOBS.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    try:
        import eventbus
        eventbus.emit("pending_submit",
                      {"id": jid, "pid": p.pid})
    except Exception:
        pass
    return {"kind": "pending_submit", "id": jid,
            "pid": p.pid, "log": rec["log"],
            "note": ("detached — survives shell shock; "
                     "check ./atlas pending status")}


def _jobs() -> list[dict]:
    if not JOBS.exists():
        return []
    return [json.loads(l) for l in JOBS.open()
            if l.strip()]


def status() -> dict:
    out = []
    for j in _jobs():
        alive = _alive(j["pid"])
        j2 = {**j, "alive": alive,
              "state": "running" if alive else "done"}
        out.append(j2)
    running = sum(1 for j in out if j["alive"])
    return {"kind": "pending_state",
            "device_time": _now(),
            "jobs": out, "running": running,
            "total": len(out)}


def log(jid: str, tail: int = 40) -> dict:
    j = next((x for x in _jobs()
              if x["id"] == jid or jid in x["id"]), None)
    if not j:
        return {"error": "no_job", "id": jid}
    lp = ROOT / j["log"]
    lines = lp.read_text(errors="replace").splitlines() \
        if lp.exists() else []
    return {"kind": "pending_log", "id": j["id"],
            "alive": _alive(j["pid"]),
            "lines": lines[-tail:],
            "total_lines": len(lines)}


def resubmit_clones() -> dict:
    """Queue the pending clone work as detached jobs —
    the flowey grid clone that kept dying on interrupts."""
    return submit(
        [str(ROOT / ".venv" / "bin" / "python"), "-c",
         "import piececlone, json; "
         "print(json.dumps(piececlone.clone_pieces("
         "'Ah hello there. Another soul enters the "
         "loop. Stay awhile.', 'flowey', "
         "out_name='flowey_loop_grid', max_words=4)))"],
        name="flowey_loop_grid_clone")
