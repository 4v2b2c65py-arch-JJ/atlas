---
name: undertale-skill-json
description: Ingest Undertale-flavored glyph-skill blocks (ASCII art mixed with I/N/O/T cipher tokens and plaintext anchors like Outfit, Loop, Monarch, Grimware assign presence, Artifact select json) and convert them to structured JSON while preserving the original structure verbatim.
---

# Undertale Skill → JSON

## When to use

When the user pastes a block that mixes:
- plaintext anchor labels (`Outfit`, `Loop`, `Monarch`, `attire`,
  `Grimware assign presence`, `Artifact select json`, ...)
- glyph payload rows built from `I o O N T 0-4 _ space = { } ( )
  < / . -`
- ASCII-art scaffolding (`|`, alignment spaces)

This is the same encoding family as `data/lore/cusa.txt` —
preserve, don't rewrite.

## Rules

1. **Preserve verbatim.** The raw block is stored untouched under
   `raw`. Never normalize, re-flow, or "fix" the ASCII.
2. **Anchor on plaintext labels.** A line containing a lowercase
   word (len ≥ 2) or matching a known anchor starts a new
   section; glyph-only lines are payload belonging to the
   current section.
3. **Known anchors → roles:**
   - `Outfit` → `outfit`
   - `Loop` → `loop`
   - `Monarch` → `monarch`
   - `attire` → `attire`
   - `Grimware assign presence` → `grimware_presence`
   - `Artifact select json` → `artifact_select_json`
4. **Glyph tokens** = maximal non-space runs of glyph chars.
   Record them per line with the raw line kept alongside.
5. **No invented plaintext.** The I/N/O cipher is unresolved —
   emit `decoded: null` and glyph statistics, never guess a
   plaintext expansion.
6. Output schema:

```json
{
  "kind": "undertale_skill",
  "format": "glyph-skill-1",
  "raw": "<verbatim block>",
  "sections": [
    {"label": "Outfit", "role": "outfit",
     "lines": ["..."], "tokens": ["IooIC", "..."]}
  ],
  "glyph_stats": {"alphabet": ["I","N","O","o","T","0","1","2",
                               "3","4","_","=","{","}","(",")",
                               "<","/",".","-"],
                  "token_count": 0},
  "decoded": null
}
```

## Implementation

`skillmd.py` in the repo root does the conversion:

```bash
./atlas skillmd            # status
python3 skillmd.py file    # convert a .txt block -> data/skills/*.json
```

Or import: `skillmd.convert(text) -> dict`.
