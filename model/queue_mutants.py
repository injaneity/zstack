import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
M = [
    (
        "no-claim",
        "two machines whose filters overlap both queue one item (no claim on the forge)",
        [('Enter(m, i) == q[m][i] = "none" /\\ claim[i] = None /\\ i \\notin held', 'Enter(m, i) == q[m][i] = "none" /\\ i \\notin held')],
        "OneMachinePerItem",
    ),
    (
        "recover-forgets-the-thread",
        "a started item whose thread did start goes back in the queue",
        [('IF threads[i] > 0 THEN "working" ELSE "queued"', '"queued"')],
        "OneThreadPerItem",
        ("Cap = 1", "Cap = 2"),
    ),
    (
        "launch-twice",
        "the dispatcher launches again for an item it already launched",
        [("Launch(m) == launching[m] # None /\\ ~fired[m]", "Launch(m) == launching[m] # None")],
        "OneThreadPerItem",
    ),
    ("launch-past-the-cap", "a thread starts with the machine at its cap", [("  /\\ Cardinality(live[m]) < Cap\n", "")], "CapHolds"),
    (
        "start-a-held-item",
        "an item with a hold row added after it was queued is started",
        [("/\\ launching[m] = None /\\ i \\notin held /\\ First(m, i)", "/\\ launching[m] = None /\\ First(m, i)")],
        "HeldNeverStarted",
    ),
    ("oldest-first", "the queue ignores priority", [("/\\ i \\notin held /\\ First(m, i)\n", "/\\ i \\notin held\n")], "PriorityOrder"),
    (
        "fix-cap-off-by-one",
        "a failed verification goes back to its thread once more than ci.fix_attempts",
        [('IF fixes[i] + 1 < MaxFix THEN "working" ELSE "owner"', 'IF fixes[i] < MaxFix + 1 THEN "working" ELSE "owner"')],
        "FixCap",
    ),
    (
        "status-survives-a-push",
        "the verified status stays green after a new push",
        [('  /\\ status\' = [status EXCEPT ![i] = "none"] /\\ Set(m, i, "working")', '  /\\ status\' = status /\\ Set(m, i, "working")')],
        "AutoMergeIsVerifiedAndLowRisk",
    ),
    (
        "arm-high-risk",
        "auto-merge is armed for an item the classifier flagged high risk",
        [("IF HighRisk(i) \\/ i \\in held THEN armed \\ {i} ELSE armed \\cup {i}", "armed \\cup {i}")],
        "AutoMergeIsVerifiedAndLowRisk",
    ),
    (
        "raise-does-not-disarm",
        "a thread raises its risk after auto-merge was armed, and it merges anyway",
        [("  /\\ raised' = raised \\cup {i} /\\ armed' = armed \\ {i}", "  /\\ raised' = raised \\cup {i} /\\ armed' = armed")],
        "AutoMergeIsVerifiedAndLowRisk",
    ),
    (
        "claim-before-launch",
        "an item is marked working for a thread that never started",
        [("Claim(m) == launching[m] # None /\\ fired[m]", "Claim(m) == launching[m] # None")],
        "WorkingHasThread",
    ),
    (
        "no-recovery",
        "an item whose launch died after it was started stays starting for good",
        [('Recover(m, i) == q[m][i] = "starting" /\\ launching[m] # i\n', 'Recover(m, i) == q[m][i] = "starting" /\\ launching[m] # i /\\ FALSE\n')],
        "a temporal property",
    ),
]


def main(jar, java, scratch):
    src, cfg = open(os.path.join(HERE, "Queue.tla")).read(), open(os.path.join(HERE, "Queue.cfg")).read()
    bad = 0
    for name, why, edits, rule, *conf in M:
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
            open(os.path.join(d, "Queue.tla"), "w").write(s)
            open(os.path.join(d, "Queue.cfg"), "w").write(cfg.replace(*conf[0]) if conf else cfg)
            out = subprocess.run(
                [
                    java,
                    "-XX:+UseParallelGC",
                    "-cp",
                    jar,
                    "tlc2.TLC",
                    "-workers",
                    "2",
                    "-metadir",
                    os.path.join(d, "meta"),
                    "-config",
                    "Queue.cfg",
                    "Queue.tla",
                ],
                cwd=d,
                capture_output=True,
                text=True,
                timeout=900,
            ).stdout
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
