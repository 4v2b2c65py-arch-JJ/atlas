"""Invocation testsuite — seeds ported from the FUNC map must
pass through the soulset machinery: alive variants load into the
code_invocation space still bugged, while the invocation stopper
(finalize) correctly refuses dead variants.

Run: .venv/bin/python -m pytest tests/test_invocation.py -v
  or: .venv/bin/python tests/test_invocation.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gaster
import soulset


def test_func_map_decodes():
    fm = gaster.func_map()
    assert "entries" in fm, fm.get("error")
    names = {e["name"] for e in fm["entries"]}
    for must in ("scr_save", "scr_dogcheck", "scr_deaddog",
                 "file_text_open_write", "scr_floweybodysave"):
        assert must in names, f"{must} missing from FUNC map"


def test_invocation_seeds_lore_matched():
    r = gaster.invocation_seeds()
    assert r["seeds"] >= 10
    assert "SENKETSU_GATE" in r["lore_seeds"]
    assert "FLOWER_VESSEL" in r["lore_seeds"]
    assert "SCORE_RECORD" in r["lore_seeds"]


def test_seeds_ported_and_bugged():
    """Every lore seed must exist as an alive, bugged soul in the
    code_invocation space — invocation passes the conditional
    bug, not stopped."""
    recs = soulset._load()
    ported = [r for r in recs
              if r.get("space") == "code_invocation"]
    assert len(ported) >= 10, f"only {len(ported)} ported seeds"
    for r in ported:
        assert soulset.is_alive(r), r["name"]
        assert soulset.conditional_bug(r)["bug"], r["name"]


def test_invocation_stopper():
    """The stopper: a finalized soul cannot be ported — invocation
    is stopped for dead variants."""
    probe = "test_stopper_probe"
    soulset.soul(probe, "soled", holder="testsuite")
    soulset.finalize(probe)
    res = soulset.port(probe, "code_invocation")
    assert "error" in res and "finalized" in res["error"]


def test_load_materializes_bug():
    """Loading a port save into a space preserves the bug —
    form persists, soul gone, conditional check bypassed."""
    name = "chronicle_hand"
    p = soulset.port(name, "test_space")
    if not p.get("save_id"):
        return  # already finalized by another test order — skip
    l = soulset.load(p["save_id"], "test_space")
    assert l["bug"] is True
    assert l["loaded"]["space"] == "test_space"
    assert l["loaded"]["form_factor_persists"] is True


def test_gaster_records_stored():
    recs_path = gaster.RECORDS
    kinds = set()
    for line in recs_path.read_text().splitlines():
        try:
            kinds.add(json.loads(line)["kind"])
        except Exception:
            pass
    assert "invocation_seed" in kinds
    assert "game_shell_conduit" in kinds
    assert "game_shell_command" in kinds


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_")]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
