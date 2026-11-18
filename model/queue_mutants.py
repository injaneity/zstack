import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

LIVE = "a temporal property"
HERE = os.path.dirname(os.path.abspath(__file__))
M = [
    (
        "overlapping-filters",
        "two machines whose label filters overlap both queue one item",
        [('"mac" :> {"i1", "i3"}', '"mac" :> {"i1", "i2", "i3"}')],
        "OneMachinePerItem",
    ),
    (
        "merge-a-held-item",
        "a hold row added after a head passed does not close the gate",
        [(' /\\ ~NeedsPerson(i) /\\ i \\notin held\nMerge', ' /\\ ~NeedsPerson(i)\nMerge')],
        "ThreadMergesOnlyAVerifiedAllowedHead",
    ),
    (
        "merge-an-unverified-head",
        "the gate opens for a head the verifier never passed",
        [('Open(i) == status[i] # "none" /\\ ~NeedsPerson(i)', "Open(i) == ~NeedsPerson(i)")],
        "ThreadMergesOnlyAVerifiedAllowedHead",
    ),
    (
        "merge-a-review-pass",
        "the gate opens for a head the verifier passed as needing a person",
        [('Open(i) == status[i] # "none" /\\ ~NeedsPerson(i) /\\ i', 'Open(i) == status[i] # "none" /\\ i')],
        "ThreadMergesOnlyAVerifiedAllowedHead",
    ),
    (
        "pass-survives-a-push",
        "a pass stays on the PR after a new push",
        [('status[i] # "none" /\\ status\' = [status EXCEPT ![i] = "none"]', 'status[i] # "none" /\\ status\' = status')],
        "ThreadMergesOnlyAVerifiedAllowedHead",
    ),
    ("launch-past-the-cap", "a thread is claimed with the machine at its cap", [("  /\\ Busy(m) < Cap\n", "")], "CapHolds"),
    (
        "start-a-held-item",
        "an item with a hold row added after it was queued is claimed",
        [('Claim(m, i) == q[m][i] = "queued" /\\ i \\notin held /\\ First(m, i)', 'Claim(m, i) == q[m][i] = "queued" /\\ First(m, i)')],
        "HeldNeverStarted",
    ),
    ("oldest-first", "the queue ignores priority", [("/\\ i \\notin held /\\ First(m, i)\n", "/\\ i \\notin held\n")], "PriorityOrder"),
    (
        "no-release",
        "an item whose thread's turn ended holds its slot for good",
        [('Release(m, i) == q[m][i] = "running" /\\ UNCHANGED rest', 'Release(m, i) == q[m][i] = "running" /\\ FALSE /\\ UNCHANGED rest')],
        LIVE,
    ),
    (
        "a-late-verdict-is-refused",
        "the verdict of a verifier still running when its thread's turn ended is thrown away",
        [('Takes(m, i) == q[m][i] \\in {"running", "review"}', 'Takes(m, i) == q[m][i] = "running"')],
        "AVerdictAlwaysLands",
    ),
    (
        "clear-an-open-pr",
        "an item leaves the queue at its turn end though its passed PR is still open",
        [('  /\\ \\/ Finished(i) /\\ Set(m, i, "done")', '  /\\ \\/ Set(m, i, "done")')],
        "NothingUnfinishedIsCleared",
    ),
    (
        "keep-a-merged-item",
        "an item whose PR the owner merged stays in the queue for good",
        [('Settle(m, i) == q[m][i] = "review" /\\ i \\in merged', 'Settle(m, i) == q[m][i] = "review" /\\ FALSE /\\ i \\in merged')],
        LIVE,
    ),
]


def run(jar, java, scratch, src, cfg, name, why, edits, rule, *conf):
    s = src
    for old, new in edits:
        if s.count(old) != 1:
            return f"BROKEN   {name}: edit not found once: {old[:60]!r}"
        s = s.replace(old, new)
    d = os.path.join(scratch, name)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "Queue.tla"), "w").write(s)
    open(os.path.join(d, "Queue.cfg"), "w").write(cfg.replace(*conf[0]) if conf else cfg)
    cmd = [java, "-XX:+UseParallelGC", "-cp", jar, "tlc2.TLC", "-workers", "2", "-metadir", os.path.join(d, "meta")]
    out = subprocess.run([*cmd, "-config", "Queue.cfg", "Queue.tla"], cwd=d, capture_output=True, text=True, timeout=900).stdout
    m = re.search(r"Invariant (\w+) is violated|(Temporal properties were violated)|(not completely specified|Deadlock)", out)
    got = m and (m.group(1) or (m.group(2) and LIVE))
    if got == rule:
        return f"killed   {name}: {why} -> {got} breaks"
    return f"SURVIVED {name}: {why} -> {got or (m and 'model error: ' + m.group(3)) or 'no error'} (expected {rule})"


def main(jar, java, scratch):
    src = open(os.path.join(HERE, "Queue.tla")).read()
    cfg = open(os.path.join(HERE, "Queue.cfg")).read()
    with ThreadPoolExecutor(max(1, (os.cpu_count() or 2) // 2)) as pool:
        said = list(pool.map(lambda m: run(jar, java, scratch, src, cfg, *m), M))
    print("\n".join(said))
    return int(any(not x.startswith("killed") for x in said))


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
