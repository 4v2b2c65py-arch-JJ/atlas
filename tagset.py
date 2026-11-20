"""tagset — subset datasets keyed by tag.

Wire-in for dumps-style datasets: every record is a
subset member carrying a TAG that doubles as its
description (tag as description). Records append to
data/tagset/subsets.jsonl; queries return the subset
for a tag — 7k dumps, 10K sevs, Q time 5B, 1000K V
style buckets stay separable, never flattened.

  add(tag, desc=None, source="")  — tag IS the
      description when desc omitted
  subset(tag)  — all records under a tag
  tags()       — census of tag -> count
  status()
"""

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "tagset"
STORE = OUT / "subsets.jsonl"


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


def add(tag: str, desc: str | None = None,
        source: str = "", **extra) -> dict:
    """Append a subset record. tag as description:
    when desc is omitted the tag itself describes the
    member — dumps datasets stay self-describing."""
    t = (tag or "").strip()
    if not t:
        return {"error": "empty_tag"}
    rec = {"kind": "tagset_member",
           "device_time": _now(),
           "tag": t,
           "description": desc if desc is not None else t,
           "tag_is_description": desc is None,
           "source": source,
           "derived": True,
           **extra}
    OUT.mkdir(parents=True, exist_ok=True)
    with STORE.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False)
                + "\n")
    return {**rec, "added": True}


def subset(tag: str) -> dict:
    t = (tag or "").strip()
    recs = [r for r in _jsonl(STORE) or []
            if r.get("tag") == t]
    return {"kind": "tagset_subset",
            "device_time": _now(),
            "tag": t,
            "count": len(recs),
            "members": recs}


def tags() -> dict:
    census = {}
    for r in _jsonl(STORE) or []:
        t = r.get("tag", "?")
        census[t] = census.get(t, 0) + 1
    return {"kind": "tagset_tags",
            "device_time": _now(),
            "tags": dict(sorted(
                census.items(),
                key=lambda kv: -kv[1]))}


def status() -> dict:
    recs = list(_jsonl(STORE) or [])
    return {"kind": "tagset_state",
            "device_time": _now(),
            "space": "subset_datasets",
            "rule": "tag as description",
            "members": len(recs),
            "tags": len({r.get("tag") for r in recs})}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "add":
        print(json.dumps(add(
            sys.argv[2],
            sys.argv[3] if len(sys.argv) > 3
            else None), indent=1))
    elif len(sys.argv) > 2 and sys.argv[1] == "subset":
        print(json.dumps(subset(sys.argv[2]),
                         indent=1)[:3000])
    elif len(sys.argv) > 1 and sys.argv[1] == "tags":
        print(json.dumps(tags(), indent=1))
    else:
        print(json.dumps(status(), indent=1))
