"""Underworld caster — grimware preset conditions + authority
exertion + creator-environment media + portal-entry mimic.

Preset condition set = TOA + Vanitas + Undertale grimware tokens
(blue moon, clockwork grimoire, true names, miasma, Score,
determination, mana, magic, door/terrain).

Authority: every named character is checked against the preset —
exert only when the grimware condition binds (dataset presence +
lore token match). Target = all named characters in the corpus.

Media: searches the original creator's environment (Toby Fox dev
footage / Vanitas PV) via yt-dlp, downloads, probes, then local
AI (llama3.2) determines its real condition.

Portal: passing characters get a mana+magic portal-entry cast —
instruction engraved into original layers via caster.
"""

import json
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import caster
import namescrape

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "underworld"
METRICS = OUT / "metrics.jsonl"
YTDLP = str(ROOT / ".venv" / "bin" / "yt-dlp")

# grimware preset condition — TOA + Vanitas + Undertale tokens
GRIMWARE_PRESET = {
    "blue_moon": "Vanitas — Vampire of the Blue Moon",
    "clockwork_grimoire": "Book of Vanitas — blue leather, "
                         "jet-black pages",
    "true_names": "vampire true names / identity binding",
    "miasma": "Tales of the Abyss — poisonous sea, Qliphoth",
    "score": "the Score — prophecy engine",
    "determination": "Undertale — the spirit stat",
    "mana": "spell resource — portal working",
    "magic": "grimoire working — cast condition",
    "door_terrain": "W-J-Gaster terrain boundary",
    "nameplate": "jhan jhonniel flores marte = arthur leywin",
}

NAMED_CHARACTERS = {
    # Vanitas no Karte
    "vanitas": {"lore": "vampire of the blue moon", "src": "vanitas"},
    "noe archiviste": {"lore": "chasseur, book seeker",
                       "src": "vanitas"},
    "dominique de sr": {"lore": "vampire countess", "src": "vanitas"},
    "chloe": {"lore": "werewolf doctor", "src": "vanitas"},
    # Tales of the Abyss / Senketsu no Asch
    "asch": {"lore": "god general, luke fon fabre",
             "src": "toa"},
    "luke fon fabre": {"lore": "seventh fonon", "src": "toa"},
    "peorth": {"lore": "god general", "src": "toa"},
    "ion": {"lore": "god general", "src": "toa"},
    "clea": {"lore": "god general", "src": "toa"},
    "yulia": {"lore": "score keeper", "src": "toa"},
    # Undertale
    "flowey": {"lore": "determination, sans-papyrus",
               "src": "undertale"},
    "sans": {"lore": "determination skeleton", "src": "undertale"},
    "papyrus": {"lore": "determination skeleton", "src": "undertale"},
    "undyne": {"lore": "determination warrior", "src": "undertale"},
    "alphys": {"lore": "determination scientist", "src": "undertale"},
    "asgore": {"lore": "king determination", "src": "undertale"},
    "toriel": {"lore": "determination goat", "src": "undertale"},
    "w.d. gaster": {"lore": "former royal scientist",
                    "src": "undertale"},
    "asriel": {"lore": "fallen determination", "src": "undertale"},
    "frisk": {"lore": "determination human", "src": "undertale"},
    "chara": {"lore": "determination shadow", "src": "undertale"},
    # nameplate
    "arthur leywin": {"lore": "the beginning after the end",
                      "src": "nameplate"},
    "jhan jhonniel flores marte": {"lore": "operator nameplate",
                                  "src": "nameplate"},
}


def _metric(event: str, **kw) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "event": event, **kw}
    with METRICS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def authority_check(character: str) -> dict:
    """Cast condition evaluation: does the grimware preset bind
    this named character strongly enough to exert authority in
    the underworld? Local-only checks (dataset + lore tokens)."""
    name = character.strip().lower()
    meta = NAMED_CHARACTERS.get(name, {})
    # 1. dataset presence (names GGUF/jsonl knowledge)
    hit = namescrape.lookup(name, limit=3)
    dataset = bool(hit.get("hits"))
    # 2. grimware token overlap with lore + preset keys
    tokens = set()
    lore = (meta.get("lore", "") + " " + name).lower()
    for k, v in GRIMWARE_PRESET.items():
        if k.replace("_", " ") in lore or \
                any(w in lore for w in v.lower().split()[:3]):
            tokens.add(k)
    # named-corpus characters always bind at least nameplate/door
    if meta.get("src") in ("undertale", "vanitas", "toa",
                           "nameplate"):
        tokens.add("door_terrain")
        tokens.add("nameplate" if meta.get("src") == "nameplate"
                   else "true_names")
    score = len(tokens) + (2 if dataset else 0)
    exert = score >= 3
    return {"character": name, "src": meta.get("src"),
            "dataset_hit": dataset, "grimware_tokens": sorted(tokens),
            "score": score, "exert": exert,
            "condition": ("authority exerted — grimware condition "
                          "bound" if exert else
                          "condition unmet — authority withheld")}


def authority_sweep() -> dict:
    """Target ALL named characters — returns per-character
    verdicts plus the exert set."""
    results = [authority_check(c) for c in NAMED_CHARACTERS]
    exert = [r["character"] for r in results if r["exert"]]
    _metric("authority_sweep", targets=len(results),
            exerted=len(exert))
    return {"targets": len(results), "exerted": len(exert),
            "verdicts": results}


def ytsearch(query: str, n: int = 5) -> list[dict]:
    try:
        r = subprocess.run(
            [YTDLP, "--flat-playlist", "--dump-single-json",
             "--playlist-end", str(n), f"ytsearch{n}:{query}"],
            capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return []
        data = json.loads(r.stdout)
        out = []
        for e in data.get("entries", []):
            live = str(e.get("live_status") or "").lower()
            if e.get("is_live") or live in ("is_live",
                                            "is_upcoming"):
                continue
            url = e.get("webpage_url") or e.get("url") or ""
            if url and not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={url}"
            out.append({"title": e.get("title", ""), "url": url,
                        "duration": e.get("duration") or 0,
                        "uploader": e.get("uploader") or
                        e.get("channel")})
        return [c for c in out if c["url"]]
    except Exception:
        return []


def find_creator_media() -> dict:
    """Hunt the original creator's environment — Toby Fox dev
    footage first, Vanitas official PV as alternate. Download the
    best candidate into data/underworld/."""
    OUT.mkdir(parents=True, exist_ok=True)
    queries = [
        ("toby fox undertale developer room", "undertale"),
        ("toby fox gamemaker undertale dev", "undertale"),
        ("vanitas no karte pv bones", "vanitas"),
        ("jun mochizuki interview", "vanitas"),
    ]
    found = []
    for q, tag in queries:
        for hit in ytsearch(q, 3):
            found.append({**hit, "query": q, "tag": tag})
        if found:
            break
    if not found:
        return {"error": "no creator media found"}
    # prefer shortest reasonable clip from original uploader
    cand = sorted(found, key=lambda h: abs(
        (h.get("duration") or 300) - 300))[:1]
    pick = cand[0]
    dest = OUT / f"creator_{pick['tag']}.mp4"
    if not dest.exists():
        r = subprocess.run(
            [YTDLP, "-f", "best[height<=720]/best",
             "--merge-output-format", "mp4",
             "-o", str(dest), pick["url"]],
            capture_output=True, text=True, timeout=600)
        if r.returncode != 0 or not dest.exists():
            _metric("media_download_fail", url=pick["url"],
                    err=r.stderr[-200:])
            return {"error": "download failed", "pick": pick}
    _metric("media_found", title=pick["title"], url=pick["url"],
            file=str(dest))
    return {"pick": pick, "file": str(dest),
            "bytes": dest.stat().st_size}


def ai_condition(media: dict) -> dict:
    """Local AI (llama3.2) determines the media's REAL condition —
    honest classification from metadata, no guessing."""
    import llm
    pick = media.get("pick", {})
    prompt = (
        "Classify this video file's real condition. JSON only: "
        '{"real_condition": "...", "confidence": 0-1, "evidence": "..."}. '
        f"Title: {pick.get('title')}\n"
        f"Uploader: {pick.get('uploader')}\n"
        f"Duration: {pick.get('duration')}s\n"
        f"Query origin: {pick.get('query')}\n"
        f"File: {media.get('file')} ({media.get('bytes')} bytes)\n"
        "Is it official creator content, fan content, repost, or "
        "something else? Answer JSON only.")
    try:
        out = llm._chat("llama3.2:latest", prompt, timeout=270)
        cond = json.loads(out[out.index("{"):out.rindex("}") + 1])
    except Exception as e:
        cond = {"real_condition": "unclassified",
                "confidence": 0.0, "evidence": str(e)[:200],
                "note": "local ai unavailable — metadata only"}
    cond["media"] = {"title": pick.get("title"),
                     "uploader": pick.get("uploader"),
                     "url": pick.get("url")}
    _metric("ai_condition", result=cond.get("real_condition"),
            confidence=cond.get("confidence"))
    return cond


def portal_entry(character: str, media_path: str,
                 at: float = 0.0) -> dict:
    """Mimic a portal entry: character casts through the underworld
    lens with mana+magic instruction engraved in original layers."""
    return caster.cast(
        character, media_path, at=at, lens="underworld",
        repeats=1, seconds=8,
        instruction=(f"MANA:full MAGIC:grimware "
                     f"PORTAL:{character.upper()}"))


def run() -> dict:
    """Full underworld pass: preset -> authority sweep -> creator
    media -> local-ai condition -> portal casts for exerted."""
    sweep = authority_sweep()
    exerted = [v["character"] for v in sweep["verdicts"]
               if v["exert"]]
    media = find_creator_media()
    cond = ai_condition(media) if media.get("file") else \
        {"real_condition": "no_media", "confidence": 0}
    casts = []
    if media.get("file"):
        for ch in exerted[:6]:  # cap portal casts per run
            casts.append(portal_entry(ch, media["file"]))
    _metric("underworld_run", targets=sweep["targets"],
            exerted=len(exerted), media=bool(media.get("file")),
            condition=cond.get("real_condition"))
    return {"preset": "grimware+toa+vanitas+undertale",
            "sweep": sweep, "media": media, "condition": cond,
            "casts": casts}


def status() -> dict:
    return {"preset_conditions": GRIMWARE_PRESET,
            "named_characters": len(NAMED_CHARACTERS),
            "metrics": sum(1 for _ in METRICS.open())
            if METRICS.exists() else 0,
            "media": [p.name for p in OUT.glob("creator_*")]
            if OUT.exists() else []}
