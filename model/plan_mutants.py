import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
M = [
    ("no-reservation", "two passes offered the same queued item both launch it (no `plan start`)",
     [("  /\\ q[i] = \"queued\"\n  /\\ q' = [q EXCEPT ![i] = \"starting\"]", "  /\\ q' = [q EXCEPT ![i] = \"starting\"]")], "OneThreadPerItem"),
    ("recover-forgets-the-thread", "a stale item whose thread did start goes back in the queue",
     [("IF threads[i] > 0 THEN \"claimed\" ELSE \"queued\"", "\"queued\"")], "OneThreadPerItem"),
    ("recover-a-live-pass", "an item is recovered while the pass that started it is still launching",
     [("Stale(i) == q[i] = \"starting\" /\\ \\A p \\in Passes : cur[p] # i", "Stale(i) == q[i] = \"starting\" /\\ threads[i] = 0")],
     "OneThreadPerItem"),
    ("launch-past-the-cap", "a thread starts with the harness at its cap",
     [("  /\\ Cardinality(live) < Cap\n", "")], "CapHolds"),
    ("plan-ignores-holds", "a pass offers an item with a hold row",
     [("Ready(i) == q[i] \\in {\"none\", \"queued\"} /\\ i \\notin held", "Ready(i) == q[i] \\in {\"none\", \"queued\"}")], "HeldNeverOffered"),
    ("claim-before-launch", "an item is claimed for a thread that never started",
     [("Claim(p) == stage[p] = \"launched\"", "Claim(p) == stage[p] # \"none\"")], "ClaimedHasThread"),
    ("fix-cap-off-by-one", "a red PR goes back to its thread once more than ci.fix_attempts",
     [("FixOffer == red /\\ ~fixLive /\\ attempts < MaxFix", "FixOffer == red /\\ ~fixLive /\\ attempts <= MaxFix")], "FixCap"),
    ("no-recovery", "an item whose pass died after `plan start` stays starting for good",
     [("Recover(i) == Stale(i)\n", "Recover(i) == Stale(i) /\\ FALSE\n")], "a temporal property"),
]


def main(jar, java, scratch):
    src, cfg = open(os.path.join(HERE, "Plan.tla")).read(), open(os.path.join(HERE, "Plan.cfg")).read()
    bad = 0
    for name, why, edits, rule in M:
        s = src
        for old, new in edits:
            if s.count(old) != 1:
                print(f"BROKEN   {name}: edit not found once: {old[:60]!r}")
                bad = 1
                break
            s = s.replace(old, new)
        else:
            d = os.path.join(scratch, name)
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "Plan.tla"), "w").write(s)
            open(os.path.join(d, "Plan.cfg"), "w").write(cfg)
            out = subprocess.run([java, "-XX:+UseParallelGC", "-cp", jar, "tlc2.TLC", "-workers", "2", "-metadir",
                                  os.path.join(d, "meta"), "-config", "Plan.cfg", "Plan.tla"],
                                 cwd=d, capture_output=True, text=True, timeout=900).stdout
            m = re.search(r"Invariant (\w+) is violated|(Temporal properties were violated)|(not completely specified|Deadlock)", out)
            got = m and (m.group(1) or (m.group(2) and "a temporal property"))
            if got == rule:
                print(f"killed   {name}: {why} -> {got} breaks")
            else:
                print(f"SURVIVED {name}: {why} -> {got or (m and 'model error: ' + m.group(3)) or 'no error'} (expected {rule})")
                bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
