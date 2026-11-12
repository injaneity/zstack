"""Mutation check for model/Slots.tla: each mutant puts back one bug (the shared 7-total/5-auto caps among them) and must
make TLC fail on Slots.cfg with the rule it names. A mutant that still passes, or that fails on another rule, means the
model cannot see that bug.
usage: python3 -I model/slots_mutants.py <tla2tools.jar> <java> <scratch dir>   (one line per mutant, exit 1 on a survivor)"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
M = [
    # name, the bug, [(old, new)], the rule that must catch it
    ("shared-total", "the 7-total cap before the split: quick runs and every pool count toward one total",
     [('Waits(h, p) == p # "quick" /\\ (Unknown(h) \\/ Count(p) >= Cap(p))',
       'Waits(h, p) == p # "quick" /\\ (Unknown(h) \\/ Count(p) >= Cap(p) \\/ run.auto + Count("hand") + run.quick >= 7)')],
     "QuickNotCounted"),
    ("auto-takes-hand", "the 5-auto cap before the split: an auto run is counted against a cap above 3",
     [('Cap(p) == IF p = "auto" THEN Auto ELSE', 'Cap(p) == IF p = "auto" THEN 5 ELSE')], "AutoCap"),
    ("untagged-not-counted", "a running run with no pool tag counted in no pool",
     [('Count(p) == IF p = "hand" THEN run.hand + legacy ELSE run[p]', 'Count(p) == run[p]')], "HandCap"),
    ("quick-waits", "a quick-check launch waits behind a full hand pool",
     [('Waits(h, p) == p # "quick" /\\', 'Waits(h, p) == (p = "quick" => Count("hand") >= Hand) /\\')], "QuickNotCounted"),
    ("direct-skips-lock", "`pi-run <id>` counts without slots.lock while the drain fills the pool",
     [('  /\\ (p # "quick" /\\ h = "mini") => lock = "free"\n', '')], "AutoCap"),
    ("vm-starts-unlocked", "a launch on the VM counts and starts by itself, outside the mini's slots.lock",
     [('Unknown(h) == h = "vm" \\/ blind', 'Unknown(h) == blind')], "AutoCap"),
    ("skip-while-free", "the fingerprint skip ignores a free auto slot with Todos waiting",
     [('Acts == blind \\/ queue # None \\/ (AutoFree > 0 /\\ todo # {})', 'Acts == blind \\/ queue # None')],
     "NoSilentPassWhenCouldPick"),
    ("held-picked", "a pick ignores the Todo's blocker",
     [('Picks(i) == pc = "pick" /\\ Eligible(i)', 'Picks(i) == pc = "pick" /\\ i \\in todo')], "HeldNeverPicked"),
    ("pick-ends-early", "the pick step ends with a free auto slot before every Todo has its row",
     [('PickDone == pc = "pick" /\\ (free = 0 \\/ \\A i \\in todo : Handled(i))', 'PickDone == pc = "pick"')],
     "EveryTodoGetsARow"),
    ("lock-kept", "the drain keeps slots.lock after its pass",
     [('/\\ pc\' = "pick" /\\ lock\' = "free"', '/\\ pc\' = "pick" /\\ lock\' = lock')], "EveryPassEnds"),
]


def main(jar, java, scratch):
    src = open(os.path.join(HERE, "Slots.tla")).read()
    cfg = open(os.path.join(HERE, "Slots.cfg")).read()
    bad = 0
    for name, bug, edits, rule in M:
        d = os.path.join(scratch, name)
        os.makedirs(d, exist_ok=True)
        text = src
        for old, new in edits:
            if text.count(old) != 1:
                print(f"FAIL  {name}: its edit does not match model/Slots.tla once: {old[:60]}")
                bad = 1
                break
            text = text.replace(old, new)
        else:
            open(os.path.join(d, "Slots.tla"), "w").write(text)
            open(os.path.join(d, "Slots.cfg"), "w").write(cfg)
            out = subprocess.run([java, "-XX:+UseParallelGC", "-jar", jar, "-workers", "4", "-metadir", os.path.join(d, "meta"),
                                  "-config", "Slots.cfg", "Slots.tla"], cwd=d, capture_output=True, text=True).stdout
            hit = re.search(r"(?:Invariant|Temporal properties were) (\w+)? ?(?:is )?violated", out)
            got = (hit.group(1) or "temporal") if hit else None
            want = "temporal" if rule == "EveryPassEnds" else rule
            if got == want:
                print(f"ok    {name}: {bug} -> {rule}")
            else:
                print(f"FAIL  {name}: {bug}: TLC said {got or 'no violation'}, want {rule}")
                bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
