"""TOA-01 deep decode — maximum-data-size analysis of toa_algo.txt.

Read-only on the spec. Emits every candidate decoding to
data/toa_decode_log.jsonl and resolves barcode candidates against
archive.org + MangaDex. Discovery != validation: hits are
candidates for review.
"""

import hashlib
import json
import re
import string
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "toa_algo.txt"
LOG = ROOT / "data" / "toa_decode_log.jsonl"

LINES = SRC.read_text().splitlines()

# leet/visual normalization maps — tried independently and combined
NORM_SETS = [
    {"|": "I", "l": "I", "1": "I", "!": "I"},
    {"|": "I", "l": "1", "O": "0", "o": "0"},
    {"|": "I", "l": "I", "O": "0", "o": "0", "!": "1", "S": "5", "Z": "2"},
]


def norm(s: str, table: dict) -> str:
    for a, b in table.items():
        s = s.replace(a, b)
    return s


def tokens() -> list[str]:
    out = []
    for ln in LINES:
        for t in re.findall(r"[A-Za-z0-9^|@>_\-—]+", ln.strip()):
            if len(t) >= 2:
                out.append(t)
    return out


# ---- cipher families -------------------------------------------------

def atbash(s: str) -> str:
    return "".join(chr(155 - ord(c)) if c.isalpha() else c
                   for c in s)


def rot(s: str, n: int) -> str:
    out = []
    for c in s:
        if c.isalpha():
            base = ord("A") if c.isupper() else ord("a")
            out.append(chr((ord(c) - base + n) % 26 + base))
        else:
            out.append(c)
    return "".join(out)


QWERTY_L = {"q": "", "w": "q", "e": "w", "r": "e", "t": "r", "y": "t",
            "u": "y", "i": "u", "o": "i", "p": "o",
            "a": "", "s": "a", "d": "s", "f": "d", "g": "f", "h": "g",
            "j": "h", "k": "j", "l": "k",
            "z": "", "x": "z", "c": "x", "v": "c", "b": "v", "n": "b",
            "m": "n"}
QWERTY_R = {v: k for k, v in QWERTY_L.items() if v}
QWERTY_R.update({"p": "[", "l": ";", "m": ","})
DVORAK = dict(zip("qwertyuiopasdfghjklzxcvbnm",
                  "pyfgcrlaoeuidhtns;qjkxbmwvz"))


def keyboard(s: str, table: dict) -> str:
    return "".join(table.get(c.lower(), c) if c.isalpha() else c
                   for c in s)


def letter_freq_map(s: str) -> dict:
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return {}
    freq = {}
    for c in letters:
        freq[c.lower()] = freq.get(c.lower(), 0) + 1
    # etaoin shrdlu order — assign most frequent cipher letter to 'e'
    order = "etaoinshrdlucmfwypvbgkjqxz"
    src_order = sorted(freq, key=freq.get, reverse=True)
    return {src_order[i]: order[i] for i in range(len(src_order))}


def subst(s: str, table: dict) -> str:
    return "".join(table.get(c.lower(), c) if c.isalpha() else c
                   for c in s)


# ---- pair structure analysis -----------------------------------------

def pairs() -> list[dict]:
    out = []
    for ln in LINES:
        s = ln.strip()
        if "oo" in s and re.search(r"[—\-|_^]", s):
            left, _, right = s.partition("oo")
            out.append({"raw": s, "left": left.strip(),
                        "right": right.strip()})
    return out


def pair_features() -> list[dict]:
    feats = []
    for p in pairs():
        L, R = p["left"], p["right"]
        feats.append({
            "left_len": len(L), "right_len": len(R),
            "left_I": L.count("I") + L.count("|") + L.count("l"),
            "right_I": R.count("I") + R.count("|") + R.count("l"),
            "left_digits": re.findall(r"\d", L),
            "right_digits": re.findall(r"\d", R),
            "left_letters": "".join(sorted(set(
                c for c in L if c.isalpha()))),
            "right_letters": "".join(sorted(set(
                c for c in R if c.isalpha()))),
        })
    return feats


# ---- source resolution -----------------------------------------------

def _http_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "atlas-toa-decode/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def resolve(term: str) -> list[dict]:
    hits = []
    q = urllib.parse.quote(term)
    try:
        d = _http_json(
            "https://archive.org/advancedsearch.php?q=" + q +
            "&fl[]=identifier&fl[]=title&rows=5&output=json")
        for doc in d.get("response", {}).get("docs", []):
            hits.append({"source": "archive.org",
                         "id": doc.get("identifier"),
                         "title": doc.get("title")})
    except Exception as e:
        hits.append({"source": "archive.org", "error": str(e)})
    try:
        d = _http_json(
            "https://api.mangadex.org/manga?title=" + q + "&limit=5")
        for m in d.get("data", []):
            hits.append({"source": "mangadex", "id": m.get("id"),
                         "title": (m.get("attributes") or {})
                                  .get("title", {}).get("en")})
    except Exception as e:
        hits.append({"source": "mangadex", "error": str(e)})
    try:
        d = _http_json(
            "https://openlibrary.org/search.json?q=" + q + "&limit=5")
        for doc in d.get("docs", []):
            hits.append({"source": "openlibrary",
                         "id": doc.get("key"),
                         "title": doc.get("title")})
    except Exception as e:
        hits.append({"source": "openlibrary", "error": str(e)})
    return hits


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    report = {"ts": time.time(), "spec_sha256":
              hashlib.sha256(SRC.read_bytes()).hexdigest()[:16],
              "line_count": len(LINES)}

    # 1. token census
    all_tokens = tokens()
    report["token_count"] = len(all_tokens)
    report["charset"] = "".join(sorted(set(
        "".join(all_tokens))))

    # 2. pair features — the cipher skeleton
    report["pairs"] = pair_features()

    # 3. decode attempts on every token and on pair left/right
    candidates = set()
    corpus = []
    for ln in LINES:
        corpus.append(ln.strip())
        for t in re.findall(r"[A-Za-z0-9^|@>_\-—]+", ln.strip()):
            if len(t) >= 3:
                corpus.append(t)
    for s in corpus:
        for nt in NORM_SETS:
            n = norm(s, nt)
            candidates.add(("norm", n))
            candidates.add(("norm+atbash", atbash(n)))
            for r in range(1, 26):
                candidates.add((f"norm+rot{r}", rot(n, r)))
        candidates.add(("atbash", atbash(s)))
        candidates.add(("qwertyL", keyboard(s, QWERTY_L)))
        candidates.add(("qwertyR", keyboard(s, QWERTY_R)))
        candidates.add(("dvorak", keyboard(s, DVORAK)))
        fm = letter_freq_map(s)
        if fm:
            candidates.add(("freqsub", subst(s, fm)))

    # score candidates: prefer strings with vowels+dictionary shape
    def score(c):
        txt = re.sub(r"[^a-z]", "", c.lower())
        if len(txt) < 4:
            return -1
        vow = sum(txt.count(v) for v in "aeiou") / len(txt)
        common = sum(txt.count(w) for w in
                     ("the", "ing", "ion", "ent", "tha"))
        return vow * 2 + common

    ranked = sorted(((score(c), kind, c) for kind, c in candidates),
                    reverse=True)[:40]
    report["top_decodes"] = [
        {"score": round(s, 2), "kind": k, "text": c}
        for s, k, c in ranked if s > 0]

    # 4. barcode hunt — resolve distinct plausible IDs
    #    pool-left tokens, section-joined strings, spec hash
    ids = set()
    ids.add(report["spec_sha256"])
    for p in pairs():
        ids.add(re.sub(r"[^A-Za-z0-9]", "", p["left"]))
        ids.add(re.sub(r"[^A-Za-z0-9]", "", p["right"]))
    for kind, c in list(candidates)[:200]:
        cc = re.sub(r"[^A-Za-z0-9]", "", c)
        if 6 <= len(cc) <= 40:
            ids.add(cc)
    resolutions = []
    for term in sorted(ids):
        hits = [h for h in resolve(term) if "error" not in h]
        good = [h for h in hits
                if any(w in (h.get("title") or "").lower()
                       for w in ("toa", "abyss", "asch", "bionicle",
                                 "tales"))]
        if good:
            resolutions.append({"term": term, "hits": good})
    report["barcode_resolutions"] = resolutions

    with LOG.open("a") as f:
        f.write(json.dumps(report) + "\n")
    print(json.dumps({k: report[k] for k in
                      ("spec_sha256", "token_count", "charset",
                       "top_decodes", "barcode_resolutions")},
                     indent=1)[:6000])


if __name__ == "__main__":
    main()
