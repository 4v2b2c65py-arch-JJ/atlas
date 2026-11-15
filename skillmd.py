"""Undertale glyph-skill -> JSON converter.

Parses blocks that mix plaintext anchor labels with I/N/O/T
glyph payload rows (same family as data/lore/cusa.txt). Preserves
the raw block verbatim, sections by anchor, tokens per line.
Cipher is unresolved -> decoded: null, never guessed.

Usage: python3 skillmd.py <file>   -> data/skills/<name>.json
       import skillmd; skillmd.convert(text)
"""

import hashlib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
OUT = ROOT / "data" / "skills"

ANCHORS = {
    "outfit": "outfit",
    "loop": "loop",
    "monarch": "monarch",
    "attire": "attire",
    "grimware assign presence": "grimware_presence",
    "artifact select json": "artifact_select_json",
}

GLYPH_CHARS = set("INOoTt01234_ ={}()<>/.-|")
TOKEN_RE = re.compile(r"[^\s]+")


def _is_label(line: str) -> str | None:
    """Return anchor role if the line is a plaintext label."""
    s = line.strip()
    low = s.lower()
    if not s:
        return None
    for label, role in ANCHORS.items():
        if low == label or low.startswith(label):
            return role
    # generic: prose-like line (letters/space/&/'/- only) that is
    # lowercase-dominant — glyph tokens are uppercase/digit heavy
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9 '&/+-]*", s):
        lower = sum(1 for ch in s if ch.islower())
        upper = sum(1 for ch in s if ch.isupper())
        if lower >= 3 and lower >= upper:
            return "section"
    return None


def _is_payload(line: str) -> bool:
    s = line.strip()
    return bool(s) and all(ch in GLYPH_CHARS or ch == " "
                           for ch in s)


def convert(text: str) -> dict:
    """Glyph-skill block -> structured JSON dict."""
    raw = text if text.endswith("\n") else text + "\n"
    lines = text.splitlines()
    sections = []
    cur = None
    for line in lines:
        role = _is_label(line)
        if role:
            cur = {"label": line.strip(), "role": role,
                   "lines": [], "tokens": []}
            sections.append(cur)
        elif cur is not None and (_is_payload(line) or
                                  line.strip() == ""):
            if line.strip():
                cur["lines"].append(line)
                cur["tokens"].extend(TOKEN_RE.findall(line))
        else:
            # art/payload that isn't a clean label — attach to the
            # current section, keep tokens too so nothing drops
            if cur is None:
                cur = {"label": "(preamble)", "role": "preamble",
                       "lines": [], "tokens": []}
                sections.insert(0, cur)
            cur["lines"].append(line)
            cur["tokens"].extend(TOKEN_RE.findall(line))
    # glyph stats
    alpha = sorted({ch for t in
                    (tok for s in sections for tok in s["tokens"])
                    for ch in t if not ch.isalnum() or ch.isupper()
                    or ch in "_"})
    alpha = sorted(set("".join(
        tok for s in sections for tok in s["tokens"])))
    tok_count = sum(len(s["tokens"]) for s in sections)
    rec = {"kind": "undertale_skill", "format": "glyph-skill-1",
           "ts": time.time(),
           "fingerprint": hashlib.sha256(
               raw.encode()).hexdigest()[:16],
           "raw": raw, "sections": sections,
           "glyph_stats": {"alphabet": alpha,
                           "token_count": tok_count},
           "decoded": None}
    return rec


def store(rec: dict, name: str = "") -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    stem = name or rec["fingerprint"]
    p = OUT / f"{stem}.json"
    p.write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    return p


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: skillmd.py <glyph-skill file>")
        return
    src = Path(sys.argv[1])
    rec = convert(src.read_text())
    p = store(rec, src.stem)
    print(json.dumps({"stored": str(p),
                      "sections": [s["role"] for s in
                                   rec["sections"]],
                      "tokens": rec["glyph_stats"]["token_count"],
                      "fp": rec["fingerprint"]}, indent=1))


if __name__ == "__main__":
    main()
