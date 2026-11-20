"""shortcut — small automation shortcut module.

vue = vuelve = return: trigger tokens dispatch to
registered automations and RETURN the result. Shortcuts
can be built-in (status pulls across the halls) or
user-registered in data/shortcuts.jsonl as
{trigger, module, func} — resolved lazily, honest about
missing modules/functions.

  run(name)    — dispatch a shortcut, return result
  register()   — add a user shortcut record
  status()     — census of registered shortcuts
"""

import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
STORE = DATA / "shortcuts.jsonl"


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


# built-ins: the return-family. vue/vuelve/return all
# come back — they pull live status from the halls.
def _status_tagset():
    import tagset
    return tagset.status()


def _status_halls():
    out = {}
    for mod in ("namehall", "memehall", "modelchars",
                "bankweb", "celestial"):
        try:
            m = __import__(mod)
            out[mod] = m.status()
        except Exception as e:
            out[mod] = {"error": str(e)[:120]}
    return out


BUILTINS = {
    "vue": {"kind": "shortcut_builtin",
            "action": "status_halls",
            "note": "vue — return the halls' status"},
    "vuelve": {"kind": "shortcut_builtin",
               "action": "status_halls",
               "note": "vuelve — return (spanish: comes "
                       "back) the halls' status"},
    "return": {"kind": "shortcut_builtin",
               "action": "status_halls",
               "note": "return — come back with status"},
    "tagset": {"kind": "shortcut_builtin",
               "action": "status_tagset",
               "note": "subset dataset census"},
    "halls": {"kind": "shortcut_builtin",
              "action": "status_halls",
              "note": "all hall statuses at once"},
}

_ACTIONS = {"status_halls": _status_halls,
            "status_tagset": _status_tagset}


def register(trigger: str, module: str, func: str,
             note: str = "") -> dict:
    """User shortcut: trigger -> module.func() result."""
    t = (trigger or "").strip().lower()
    if not t:
        return {"error": "empty_trigger"}
    rec = {"kind": "shortcut",
           "device_time": _now(),
           "trigger": t, "module": module,
           "func": func, "note": note}
    DATA.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False)
                + "\n")
    return {**rec, "registered": True}


def _find(trigger: str) -> dict | None:
    t = (trigger or "").strip().lower()
    if t in BUILTINS:
        return BUILTINS[t]
    latest = None
    for r in _jsonl(STORE) or []:
        if r.get("trigger") == t:
            latest = r
    return latest


def run(trigger: str) -> dict:
    """Dispatch: builtins run their action; user
    shortcuts import module.func and return its result.
    Missing pieces return honest errors, not crashes."""
    rec = _find(trigger)
    if not rec:
        return {"kind": "shortcut_result",
                "device_time": _now(),
                "trigger": trigger,
                "error": "unknown_shortcut",
                "hint": "register() or use a builtin"}
    if rec.get("kind") == "shortcut_builtin":
        fn = _ACTIONS.get(rec.get("action"))
        if not fn:
            return {"kind": "shortcut_result",
                    "device_time": _now(),
                    "trigger": trigger,
                    "error": "missing_action",
                    "action": rec.get("action")}
        try:
            result = fn()
        except Exception as e:
            result = {"error": str(e)[:200]}
        return {"kind": "shortcut_result",
                "device_time": _now(),
                "trigger": trigger,
                "builtin": True,
                "result": result}
    # user shortcut — module.func()
    try:
        mod = __import__(rec["module"])
        fn = getattr(mod, rec["func"])
        result = fn()
    except Exception as e:
        return {"kind": "shortcut_result",
                "device_time": _now(),
                "trigger": trigger,
                "error": "dispatch_failed",
                "detail": str(e)[:200]}
    return {"kind": "shortcut_result",
            "device_time": _now(),
            "trigger": trigger,
            "module": rec["module"],
            "func": rec["func"],
            "result": result}


def status() -> dict:
    user = [r for r in _jsonl(STORE) or []
            if r.get("kind") == "shortcut"]
    return {"kind": "shortcut_state",
            "device_time": _now(),
            "space": "automation_shortcuts",
            "syntax": "vue = vuelve = return",
            "builtins": sorted(BUILTINS),
            "user_shortcuts": len(user),
            "triggers": sorted(
                {r["trigger"] for r in user})}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "register":
        print(json.dumps(register(
            sys.argv[2], sys.argv[3], sys.argv[4]
            if len(sys.argv) > 4 else "status",
            " ".join(sys.argv[5:])), indent=1))
    elif len(sys.argv) > 2:
        print(json.dumps(run(sys.argv[2]), indent=1)
              [:3000])
    else:
        print(json.dumps(status(), indent=1))
