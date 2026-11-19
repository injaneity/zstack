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
        "a hold added after a head passed does not close the gate",
        [('Open(i) == status[i] # "none" /\\ ~NeedsPerson(i) /\\ i \\notin held', 'Open(i) == status[i] # "none" /\\ ~NeedsPerson(i)')],
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
        [('pr\' = pr \\cup {i} /\\ status\' = [status EXCEPT ![i] = "none"]', 'pr\' = pr \\cup {i} /\\ status\' = status')],
        "ThreadMergesOnlyAVerifiedAllowedHead",
    ),
    (
        "launch-without-looking",
        "a pass that finds an item with no thread recorded launches one, though a thread already works in its worktree",
        [("Unattached(m, i) /\\ i \\in tree /\\ threads[i] = 0", "Unattached(m, i) /\\ i \\in tree /\\ threads[i] < 2")],
        "OneThreadPerItem",
    ),
    (
        "launch-before-the-worktree",
        "a thread is launched for an item the queue has made no worktree for",
        [("/\\ tree' = tree \\cup {i} /\\ UNCHANGED <<threads, known>>", "/\\ UNCHANGED <<tree, threads, known>>"),
         ("Launch(m, i) == Unattached(m, i) /\\ i \\in tree /\\", "Launch(m, i) == Unattached(m, i) /\\")],
        "AThreadWorksInTheQueuesTree",
    ),
    (
        "a-launched-thread-is-never-recorded",
        "an item whose thread never records itself at its session start holds its slot for good",
        [("Attach(m, i) == Unattached(m, i) /\\ threads[i] > 0", "Attach(m, i) == Unattached(m, i) /\\ FALSE /\\ threads[i] > 0")],
        LIVE,
    ),
    (
        "a-verdict-before-the-thread-is-recorded",
        "a verdict is recorded while the queue cannot tell the item's thread from its verifier",
        [("Verdict(m, i, v) == Takes(m, i) /\\ i \\in known /\\ i \\in pr", "Verdict(m, i, v) == Takes(m, i) /\\ i \\in pr")],
        "AVerdictNeedsARecordedThread",
    ),
    ("launch-past-the-cap", "a thread is claimed with the machine at its cap", [("  /\\ Busy(m) < Cap\n", "")], "CapHolds"),
    (
        "start-a-held-item",
        "an item is claimed while it is held",
        [('/\\ i \\in Accepts[m] /\\ i \\notin held /\\ First(m, i)', '/\\ i \\in Accepts[m] /\\ First(m, i)')],
        "HeldNeverStarted",
    ),
    ("oldest-first", "the queue ignores priority", [("/\\ i \\notin held /\\ First(m, i)\n", "/\\ i \\notin held\n")], "PriorityOrder"),
    (
        "no-release",
        "an item whose thread's turn ended holds its slot for good",
        [('Freed(m, i) == q[m][i] = "running" /\\ i', 'Freed(m, i) == q[m][i] = "running" /\\ FALSE /\\ i'),
         ('TurnEnd(m, i) == q[m][i] = "running" /\\ i', 'TurnEnd(m, i) == q[m][i] = "running" /\\ FALSE /\\ i')],
        LIVE,
    ),
    (
        "end-a-turn-unverified",
        "a thread's turn ends while its open PR's head has no verdict",
        [('(up \\/ i \\in NoTree) /\\ ~Unverified(i)', '(up \\/ i \\in NoTree)')],
        "ATurnEndsVerified",
    ),
    (
        "end-a-turn-when-the-forge-is-down",
        "a turn ends because the PR could not be read, though its head has no verdict",
        [('(up \\/ i \\in NoTree) /\\ ~Unverified(i)', '~(up /\\ Unverified(i))')],
        "ATurnEndsVerified",
    ),
    (
        "a-late-verdict-is-refused",
        "the verdict of a verifier still running when its thread's turn ended is thrown away",
        [('Takes(m, i) == q[m][i] \\in {"running", "waiting"}', 'Takes(m, i) == q[m][i] = "running"')],
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
        [('Settle(m, i) == q[m][i] = "waiting" /\\ i \\in merged', 'Settle(m, i) == q[m][i] = "waiting" /\\ FALSE /\\ i \\in merged')],
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
    for attempt in range(2):
        cmd = [java, "-XX:+UseParallelGC", "-cp", jar, "tlc2.TLC", "-workers", "2", "-metadir", os.path.join(d, f"meta{attempt}")]
        try:
            p = subprocess.run([*cmd, "-config", "Queue.cfg", "Queue.tla"], cwd=d, capture_output=True, text=True, timeout=900)
            out, end = p.stdout, f"exit {p.returncode}: " + ((p.stdout + p.stderr).strip().splitlines()[-1:] or ["no output"])[0][:200]
        except subprocess.TimeoutExpired:
            out, end = "", "no answer in 900 seconds"
        m = re.search(r"Invariant (\w+) is violated|(Temporal properties were violated)|(not completely specified|Deadlock)", out)
        if m or "Model checking completed" in out:
            break
    else:
        return f"BROKEN   {name}: the checker did not finish, twice ({end})"
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
