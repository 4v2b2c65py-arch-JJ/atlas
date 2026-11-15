"""Auto-unlock — title-name query unlocks media across sources,
scoped per profile, governed by the lore registry.

Flow: title -> lore registry key (which rule-set governs) ->
candidate sweep (manga sources, archive.org, yt-dlp) -> download
best candidate into the profile's unlock dir -> record + comment
via the main-site engage bots.

Profiles: data/profiles/<name>/unlocks.jsonl — same API, per-
profile basis. Nothing overwrites; each unlock is a new record.
"""

import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
LORE = ROOT / "data" / "lore"
PROFILES = ROOT / "data" / "profiles"
YTDLP = str(ROOT / ".venv" / "bin" / "yt-dlp")


def registry() -> dict:
    try:
        return json.loads((LORE / "registry.json").read_text())
    except Exception:
        return {"keys": {}}


def lore_key_for(title: str) -> str:
    """Which lore registry key governs this title. 'cusa' covers
    game/dump titles; everything else falls to 'default' until
    more keys register."""
    t = title.lower()
    game_terms = ("ps4", "ps5", "ps3", "ps2", "psx", "dump",
                  "game", "iso", "rom", "pkg")
    keys = registry().get("keys", {})
    if "cusa" in keys and any(w in t for w in game_terms):
        return "cusa"
    return "default"


def _profile_dir(profile: str) -> Path:
    d = PROFILES / profile
    d.mkdir(parents=True, exist_ok=True)
    return d


def _record(profile: str, rec: dict) -> None:
    d = _profile_dir(profile)
    with (d / "unlocks.jsonl").open("a") as f:
        f.write(json.dumps({"ts": time.time(), **rec},
                           ensure_ascii=False) + "\n")


def candidates(title: str, lore_key: str | None = None) -> list[dict]:
    """Sweep sources for the title — DLPS store (cusa key), manga
    index, archive.org, YouTube. Candidates only; nothing is
    validated as 'the' work."""
    out = []
    key = lore_key or lore_key_for(title)
    # DLPS store first when the cusa registry governs the title
    if key == "cusa":
        try:
            import dlps
            for r in dlps.search(title)[:5]:
                out.append({"source": "dlps", "title": r["title"],
                            "id": r["url"], "url": r["url"],
                            "cat": r.get("cat"),
                            "site": r.get("site")})
        except Exception:
            pass
    # manga sources (ToA/Vanitas pipeline)
    try:
        import manga
        for r in manga.search_all(title, limit=4):
            out.append({"source": "manga", "title": r.get("title"),
                        "id": r.get("id"), "url": r.get("url"),
                        "cover": r.get("cover")})
    except Exception:
        pass
    # archive.org
    try:
        q = urllib.parse.quote(f'title:"{title}"')
        url = ("https://archive.org/advancedsearch.php?q=" + q +
               "&fl[]=identifier&fl[]=title&rows=5&output=json")
        with urllib.request.urlopen(url, timeout=30) as r:
            docs = json.loads(r.read()).get("response", {}) \
                .get("docs", [])
        for d in docs:
            out.append({"source": "archive",
                        "title": d.get("title"),
                        "id": d.get("identifier"),
                        "url": "https://archive.org/details/" +
                               d.get("identifier", "")})
    except Exception:
        pass
    # youtube via yt-dlp
    try:
        r = subprocess.run(
            [YTDLP, "--flat-playlist", "--dump-single-json",
             "--playlist-end", "4", f"ytsearch4:{title}"],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            for e in json.loads(r.stdout).get("entries", []):
                live = str(e.get("live_status") or "").lower()
                if e.get("is_live") or live in ("is_live",
                                                "is_upcoming"):
                    continue
                u = e.get("webpage_url") or e.get("url") or ""
                if u and not u.startswith("http"):
                    u = "https://www.youtube.com/watch?v=" + u
                out.append({"source": "youtube",
                            "title": e.get("title"), "url": u,
                            "uploader": e.get("uploader") or
                            e.get("channel"),
                            "duration": e.get("duration") or 0})
    except Exception:
        pass
    return [c for c in out if c.get("url") or c.get("id")]


def unlock_title(title: str, profile: str = "default",
                 download: bool = True) -> dict:
    """Auto-unlock a title under a profile: registry key ->
    candidates -> download best -> record -> comment."""
    key = lore_key_for(title)
    cands = candidates(title, lore_key=key)
    _record(profile, {"event": "unlock_query", "title": title,
                      "lore_key": key, "candidates": len(cands)})
    result = {"title": title, "profile": profile,
              "lore_key": key, "candidates": cands}
    if not cands:
        result["error"] = "no candidates"
        _record(profile, {"event": "unlock_fail",
                          "title": title, "reason": "no candidates"})
        return result
    # prefer archive.org full works, then youtube, then manga
    pref = {"archive": 0, "youtube": 1, "manga": 2}
    pick = sorted(cands, key=lambda c: pref.get(c["source"], 9))[0]
    result["pick"] = pick
    if download and pick["source"] == "youtube" and pick.get("url"):
        dest = _profile_dir(profile) / "unlocks"
        dest.mkdir(exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_"
                       for c in title)[:60]
        out = dest / f"{safe}.mp4"
        if not out.exists():
            r = subprocess.run(
                [YTDLP, "-f", "best[height<=720]/best",
                 "--merge-output-format", "mp4",
                 "-o", str(out), pick["url"]],
                capture_output=True, text=True, timeout=600)
            if r.returncode != 0 or not out.exists():
                result["download"] = "failed"
                _record(profile, {"event": "unlock_download_fail",
                                  "title": title,
                                  "url": pick["url"]})
            else:
                result["download"] = str(out)
                result["bytes"] = out.stat().st_size
        else:
            result["download"] = str(out)
            result["bytes"] = out.stat().st_size
    elif download and pick["source"] == "archive":
        result["download"] = "archive_item — use " \
            "archive.org/details/" + pick["id"]
    _record(profile, {"event": "unlock_done", "title": title,
                      "pick": pick.get("title"),
                      "source": pick["source"],
                      "download": result.get("download")})
    return result


def unlock_comment(result: dict) -> dict:
    """Post the unlock outcome as a comment through the main-site
    engage mechanism (same path the commenting bots use)."""
    title = result.get("title", "?")
    pick = result.get("pick") or {}
    if result.get("error"):
        text = f"unlock miss: {title} — no candidates"
    else:
        text = (f"unlocked: {title} -> {pick.get('title','?')} "
                f"[{pick.get('source')}] profile="
                f"{result.get('profile')}")
    body = {"video": f"unlock:{title}", "action": "comment",
            "text": text, "author": "unlock-bot",
            "ts": time.time()}
    try:
        import urllib.request as u
        req = u.Request(
            "http://localhost:8917/api/engage",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with u.urlopen(req, timeout=10) as r:
            return {"posted": True, "resp": r.status}
    except Exception as e:
        # offline fallback — record the comment locally
        _record(result.get("profile", "default"),
                {"event": "unlock_comment", "text": text,
                 "offline": str(e)[:120]})
        return {"posted": False, "offline_recorded": True,
                "text": text}


def profile_status(profile: str = "default") -> dict:
    d = _profile_dir(profile)
    recs = []
    f = d / "unlocks.jsonl"
    if f.exists():
        recs = [json.loads(l) for l in f.read_text().splitlines()
                if l.strip()]
    return {"profile": profile,
            "events": len(recs),
            "titles": sorted({r.get("title") for r in recs
                              if r.get("title")}),
            "downloads": [r.get("download") for r in recs
                          if r.get("event") == "unlock_done"
                          and r.get("download")],
            "lore_registry": list(registry().get("keys", {}))}
