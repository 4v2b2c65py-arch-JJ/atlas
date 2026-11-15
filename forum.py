"""Forum bridge — load an external system URL into the
multiplayer layer.

Flow:

  1. FETCH the URL (plain HTTP, tag-stripped text).
  2. EXTRACT name records — <title>, headings, link texts,
     and capitalized entity tokens; cross-match against the
     ATLAS name record (familytree + charpack) so known
     characters join under their own ids.
  3. INITIALISE a multiplayer session per forum section —
     threads become chat messages, detected characters join
     as instances, unknown entities join as npc/title
     instances so the whole forum population is present.
  4. RETURN the forum view: roster + chatlog — a multiplayer
     session shaped like a forum.

Everything is derived scraping — no fabrication: entities
come only from text actually present on the page or in the
local name records.

Store: sessions created via multiplayer.join/say under
       data/multi/ — forum metadata at data/forum/links.jsonl
"""

import json
import re
import time
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = DATA / "forum"
LINKS = OUT / "links.jsonl"

KNOWN_NAMES = set()


def _known_names() -> set:
    """Local name record — familytree nodes + charpack chars
    + variant bases."""
    global KNOWN_NAMES
    if KNOWN_NAMES:
        return KNOWN_NAMES
    names = set()
    try:
        import familytree as _ft
        for k in getattr(_ft, "TREE", {}):
            names.add(k.lower().replace(" ", "_"))
        for k in getattr(_ft, "HIDDEN", {}):
            names.add(k.lower().replace(" ", "_"))
    except Exception:
        pass
    try:
        import charpack as _cp
        names.update(_cp.CHAR_PACK.keys())
    except Exception:
        pass
    try:
        import variants as _v
        for r in _v._jsonl(_v.OUT / "variants.jsonl") or []:
            n = r.get("node") or r.get("id")
            if n:
                names.add(str(n).lower().replace(" ", "_"))
    except Exception:
        pass
    KNOWN_NAMES = names
    return names


class _TextExtract(HTMLParser):
    """Pull headings, link texts, title and body text."""

    def __init__(self):
        super().__init__()
        self.title = ""
        self.headings = []
        self.links = []
        self.text = []
        self._tag = None
        self._buf = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in ("h1", "h2", "h3", "h4"):
            self._tag = tag
            self._buf = []
        if tag == "a":
            self._buf = []
            self._tag = "a"

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if self._tag == tag and self._buf:
            txt = " ".join("".join(self._buf).split())
            if txt:
                if tag == "a":
                    self.links.append(txt)
                else:
                    self.headings.append(txt)
            self._tag = None
            self._buf = []

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._tag:
            self._buf.append(data)
        self.text.append(data)


def fetch(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "ATLAS-forum/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(2_000_000)
        ctype = r.headers.get("Content-Type", "")
    text = raw.decode("utf-8", "replace")
    p = _TextExtract()
    p.feed(text)
    body = " ".join(" ".join(p.text).split())
    return {"url": url, "status": 200,
            "title": " ".join(p.title.split()),
            "headings": p.headings[:100],
            "links": p.links[:300],
            "text": body[:50_000],
            "content_type": ctype}


_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:[ '\-][A-Z]?[a-z]+){1,3})\b")


def extract_entities(page: dict) -> dict:
    """Name records from the page: known ATLAS characters found
    verbatim + candidate proper nouns for npc/title entities."""
    text = (page["title"] + " " + page["text"] + " " +
            " ".join(page["headings"]) + " " +
            " ".join(page["links"])).lower()
    known = _known_names()
    found_known = sorted(n for n in known
                         if re.search(r"\b" + re.escape(n) +
                                      r"\b", text))
    # proper-noun candidates (capitalized multiword) — these
    # become npc/title/forum entities, never fabricated lore
    candidates = []
    blob = page["title"] + " " + " ".join(page["headings"])
    for m in _NAME_RE.finditer(blob):
        name = m.group(1).strip()
        if len(name) > 3 and name.lower() not in {
                "the", "and", "for", "with", "from", "this",
                "that", "have", "will", "your"}:
            candidates.append(name)
    # dedupe preserve order
    seen, cands = set(), []
    for c in candidates:
        if c.lower() not in seen:
            seen.add(c.lower())
            cands.append(c)
    return {"known_characters": found_known,
            "candidate_entities": cands[:50],
            "title": page["title"]}


def _slug(url: str) -> str:
    host = urlparse(url).netloc or "web"
    return re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_")


def load(url: str, profile: str = "default") -> dict:
    """Fetch URL -> extract names -> initialise forum-style
    multiplayer session. Returns roster + chatlog view."""
    import multiplayer as mp
    page = fetch(url)
    ents = extract_entities(page)
    session = "forum_" + _slug(url)
    joined = []
    # known characters join under their own ids
    for name in ents["known_characters"]:
        r = mp.join(session, name, profile=profile,
                    timeline_phrase="forum_native",
                    label="forum:" + page["title"][:60])
        joined.append(r["instance_id"])
    # candidate entities join as npc/title instances
    for name in ents["candidate_entities"][:20]:
        inst = "npc_" + re.sub(r"[^a-z0-9]+", "_",
                               name.lower()).strip("_")
        r = mp.join(session, inst, profile=profile,
                    timeline_phrase="forum_scraped",
                    label="entity:" + name)
        joined.append(r["instance_id"])
    # forum system instance posts the thread structure
    sysid = "forum_system"
    mp.join(session, sysid, profile=profile,
            timeline_phrase="forum_native",
            label="source:" + url)
    for i, h in enumerate(page["headings"][:30]):
        mp.say(session, sysid, "[thread %d] %s" % (i + 1, h),
               meta={"kind": "forum_thread", "url": url})
    mp.say(session, sysid,
           "forum loaded from %s — %d entities, %d threads"
           % (url, len(joined),
              min(len(page["headings"]), 30)),
           meta={"kind": "forum_meta"})
    # persist link record
    OUT.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.time(), "kind": "forum_link",
           "url": url, "session": session,
           "title": page["title"],
           "entities": len(joined),
           "threads": min(len(page["headings"]), 30),
           "known": ents["known_characters"],
           "derived": True}
    with LINKS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"kind": "forum_load", "url": url,
            "session": session, "title": page["title"],
            "players_joined": len(joined),
            "threads": min(len(page["headings"]), 30),
            "known_characters": ents["known_characters"],
            "roster": mp.roster(session),
            "chat": mp.chatlog(session, 10)}


def status() -> dict:
    links = list(_jsonl(LINKS) or [])
    return {"kind": "forum_state", "ts": time.time(),
            "links": len(links),
            "sessions": [l.get("session") for l in links],
            "note": ("forum bridge: URL -> name records -> "
                     "multiplayer session; threads = chat, "
                     "entities = instances")}


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.open():
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue
