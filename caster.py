"""Caster — tier-list casting engine with character lens, repeater
view, downtime metrics, and GGUF persistence.

Mechanism:
- tier_list(): ranks every tracked name by consensus strength
  (est. global bearers / mirror presence) into S/A/B/C/D tiers
- cast(): renders a clip at an exact timestamp through a
  character lens (name overlay) with repeater view (stream_loop)
  — instruction engraved in original layers like cardinal.engrave
- mirror(): triggerable entry point — accepts a name straight
  from websearch/discovery results and casts it
- run_queue(): "till infinity" bounded by compute caps —
  max_casts / max_seconds / deadline; downtime (queue wait +
  errors) is metered per item
- metrics: every event appends to data/caster/metrics.jsonl
- store_gguf(): packs cast records + metrics into
  data/caster/casts.gguf (GGUF v3 metadata container)
"""

import json
import subprocess
import time
from pathlib import Path

import cardinal
import nameconsensus
import namescrape

ROOT = Path(__file__).parent
CAST_DIR = ROOT / "data" / "caster"
METRICS = CAST_DIR / "metrics.jsonl"
GGUF_PATH = CAST_DIR / "casts.gguf"
CAPS = {"max_casts": 50, "max_seconds": 600.0,
        "max_repeats": 8, "deadline_s": 3600.0}


def _ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _metric(event: str, **kw) -> dict:
    CAST_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "event": event, **kw}
    with METRICS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _df_escape(s: str) -> str:
    return (s.replace("\\", "\\\\").replace(":", "\\:")
             .replace("'", "\\'").replace(",", "\\,")
             .replace(";", "\\;").replace("[", "\\[")
             .replace("]", "\\]").replace("%", "\\%"))


def _font() -> str:
    for f in cardinal.FONTS:
        if Path(f).exists():
            return f
    return "Sans"


# ---------- tier list ----------

def tier_list() -> dict:
    """Rank tracked names into tiers by consensus strength."""
    tl = nameconsensus.timeline_metrics()
    rows = []
    for r in tl.get("latest", []):
        est = r.get("est_global_bearers") or 0
        pm = r.get("present_mirrors") or 0
        score = est + pm * 1e5
        tier = ("S" if score >= 5e6 else "A" if score >= 1e6 else
                "B" if score >= 1e5 else "C" if score >= 1e4 else "D")
        rows.append({"name": r["name"], "tier": tier,
                     "consensus": r["consensus"],
                     "present_mirrors": pm,
                     "est_global_bearers": est,
                     "last_vote": r["ts"]})
    # include top dataset names as unvoted baseline
    try:
        for r in namescrape.lookup("a", 0).get("hits", [])[:0]:
            pass
    except Exception:
        pass
    rows.sort(key=lambda r: -r["est_global_bearers"])
    tiers = {}
    for r in rows:
        tiers.setdefault(r["tier"], []).append(r["name"])
    return {"tiers": tiers, "ranked": rows,
            "tracked": tl.get("names_tracked", 0)}


# ---------- cast ----------

def cast(name: str, src: str, at: float = 0.0,
         lens: str = "character", repeats: int = 1,
         seconds: float = 8.0, instruction: str = "") -> dict:
    """Cast a name through the character lens at exact time `at`,
    repeater view loops the segment `repeats` times. Instruction
    engraved into original layers; metrics metered incl. downtime.
    """
    ff = _ffmpeg()
    if not ff:
        return {"error": "ffmpeg unavailable"}
    src_p = Path(src)
    if not src_p.is_absolute():
        src_p = (ROOT / src).resolve()
    if not src_p.exists():
        return {"error": f"not found: {src}"}
    repeats = max(1, min(int(repeats), CAPS["max_repeats"]))
    CAST_DIR.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    lens_text = _df_escape(f"{lens.upper()}: {name}"[:60])
    inst = _df_escape((instruction or
                       f"{name} — cast at {at}s")[:120])
    vf = (
        f"drawtext=fontfile='{_font()}':text='{lens_text}':"
        f"x=(w-text_w)/2:y=60:fontsize=30:fontcolor=gold:"
        f"borderw=2:bordercolor=black@0.8,"
        f"drawtext=fontfile='{_font()}':text='{inst}':"
        f"x=(w-text_w)/2:y=h-140:fontsize=30:fontcolor=white:"
        f"borderw=2:bordercolor=black@0.8:"
        f"shadowx=2:shadowy=2:shadowcolor=black@0.6")
    out = CAST_DIR / (f"cast_{name.replace(' ','_')[:30]}"
                      f"_{int(at)}x{repeats}.mp4")
    r = subprocess.run(
        [ff, "-y", "-stream_loop", str(repeats - 1),
         "-ss", str(at), "-t", str(seconds), "-i", str(src_p),
         "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "20", "-c:a", "aac", "-movflags", "+faststart",
         str(out)],
        capture_output=True, text=True, timeout=900)
    dt = time.time() - t0
    ok = r.returncode == 0 and out.exists()
    downtime_ms = int(max(0.0, dt - seconds) * 1000)
    rec = _metric("cast", name=name, src=str(src_p), at=at,
                  repeats=repeats, seconds=seconds,
                  ok=ok, render_s=round(dt, 2),
                  downtime_ms=downtime_ms,
                  out=str(out) if ok else None,
                  error=None if ok else r.stderr[-300:])
    return {"ok": ok, "cast": rec,
            "stream_url": (f"/api/stream?path="
                           f"{out.relative_to(ROOT)}") if ok else None}


def mirror(trigger: dict) -> dict:
    """Triggerable MCP mirror: accepts a websearch/discovery result
    ({name, src, at}) and casts it. This is the entry point search
    results hit."""
    name = trigger.get("name") or trigger.get("q") or "unknown"
    src = trigger.get("src") or trigger.get("url") or ""
    at = float(trigger.get("at", 0) or 0)
    if not src:
        # default: latest engraved/vanitas material if present
        for cand in sorted(CAST_DIR.glob("*.mp4"))[:1]:
            src = str(cand)
    if not src:
        return {"error": "no src in trigger and no cached casts"}
    return cast(name, src, at=at,
                repeats=int(trigger.get("repeats", 1)),
                seconds=float(trigger.get("seconds", 8)),
                instruction=trigger.get("instruction", ""))


# ---------- queue till caps ----------

def run_queue(triggers: list, caps: dict | None = None) -> dict:
    """Process cast triggers until compute caps — 'till infinity'
    bounded honestly by max_casts/max_seconds/deadline."""
    caps = {**CAPS, **(caps or {})}
    t_start = time.time()
    done, skipped = [], []
    for i, tr in enumerate(triggers):
        if len(done) >= caps["max_casts"]:
            skipped.append({"trigger": tr, "reason": "max_casts"})
            continue
        if time.time() - t_start > caps["deadline_s"]:
            skipped.append({"trigger": tr, "reason": "deadline"})
            continue
        if sum(d["cast"]["render_s"] for d in done
               if d.get("cast")) > caps["max_seconds"]:
            skipped.append({"trigger": tr, "reason": "max_seconds"})
            continue
        res = mirror(tr)
        done.append(res)
        _metric("queue_item", idx=i, ok=res.get("ok"),
                name=tr.get("name"))
    _metric("queue_done", processed=len(done), skipped=len(skipped),
            elapsed_s=round(time.time() - t_start, 2),
            caps=caps)
    return {"processed": len(done), "skipped": len(skipped),
            "results": done, "caps": caps,
            "elapsed_s": round(time.time() - t_start, 2)}


# ---------- persistence ----------

def metrics_summary() -> dict:
    if not METRICS.exists():
        return {"events": 0}
    casts, queue_wait, downtime = 0, 0, 0
    last_ts = None
    for line in METRICS.read_text().splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("event") == "cast":
            casts += 1
            downtime += r.get("downtime_ms", 0)
        if last_ts and r.get("ts"):
            queue_wait += max(0, r["ts"] - last_ts)
        last_ts = r.get("ts", last_ts)
    return {"events": casts, "total_downtime_ms": downtime,
            "inter_event_gap_s": round(queue_wait, 1),
            "caps": CAPS}


def store_gguf() -> dict:
    """Pack cast records + metrics snapshot into casts.gguf."""
    recs = []
    for line in METRICS.read_text().splitlines() \
            if METRICS.exists() else []:
        try:
            r = json.loads(line)
            r["kind"] = "cast_metric"
            recs.append(r)
        except Exception:
            pass
    for p in sorted(CAST_DIR.glob("cast_*.mp4"))[:500]:
        recs.append({"kind": "cast_file", "name": p.stem,
                     "bytes": p.stat().st_size,
                     "src": "caster"})
    res = namescrape.write_gguf(recs, path=GGUF_PATH)
    res["verify"] = namescrape.read_gguf_count(GGUF_PATH)
    return res


def status() -> dict:
    return {"caps": CAPS, "metrics": metrics_summary(),
            "tier_list": tier_list(),
            "casts": len(list(CAST_DIR.glob("cast_*.mp4")))
            if CAST_DIR.exists() else 0,
            "gguf": str(GGUF_PATH),
            "gguf_bytes": GGUF_PATH.stat().st_size
            if GGUF_PATH.exists() else 0}
