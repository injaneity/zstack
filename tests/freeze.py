"""Regenerate tests/fixtures/expected.json.gz: the ORIGINAL tick's verdict for every fixture in every gate state.

Run against a tick that has not been modified for the extraction, so the expectations come from production
behaviour and not from this engine:

    ONIGIRI_TICK=/path/to/tick python3 tests/freeze.py

Any diff in expected.json.gz is a deliberate behaviour change and needs saying out loud in the commit.
"""
import gzip
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "gate"))
sys.path.insert(0, os.environ.get("ONIGIRI_TICK", "/home/box/agent-data/onigiri/vm-live/tick"))
import engine
import tick

CUA_CFG = engine.load(os.path.join(HERE, "..", "gate", "cua.json"))
ZSTACK_CFG = engine.load(os.path.join(HERE, "..", "gate", "zstack.json"))


def case_key(clef, held, rerun, loc):
    """A stable name for one gate state. Named, not counted: `local_pass` has three shapes, two of size one."""
    return f"{clef}|{held}|{int(bool(rerun))}|{loc}"


def locals_(head):
    """No local PASS, a PASS for this head, and a PASS for some other head."""
    return (frozenset(), frozenset({head}), frozenset({"0" * 40}))


def main():
    with gzip.open(os.path.join(HERE, "fixtures", "prs.json.gz")) as f:
        prs = json.load(f)
    out, n = {}, 0
    for key, pr in sorted(prs.items()):
        cfg = ZSTACK_CFG if pr["repo"] == "injaneity/zstack" else CUA_CFG
        helds = (None, "hold list", "no-move")
        clefs = (None, "light", "standard", "deep", "human") if cfg.requires_clef else (None, "deep")
        reruns = (None, pr["headRefOid"])
        locs = locals_(pr["headRefOid"])
        cases = {}
        for clef, held, rerun, loc in itertools.product(clefs, helds, reruns, locs):
            k = case_key(clef, held, rerun, loc)
            cases[k] = list(tick.merge_decision(dict(pr), repo=cfg.repo, local_pass=set(loc), clef=clef,
                                                held=held, rerun_head=rerun))
            n += 1
        out[key] = {"repo": pr["repo"], "decisions": cases}
    path = os.path.join(HERE, "fixtures", "expected.json.gz")
    with gzip.open(path, "wt") as f:
        json.dump(out, f, indent=0, sort_keys=True)
    print(f"froze {n} decisions for {len(out)} fixtures -> {path}")


if __name__ == "__main__":
    main()
