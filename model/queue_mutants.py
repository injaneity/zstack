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
        "OnlyAVerifiedAllowedHeadMerges",
    ),
    (
        "merge-an-unverified-head",
        "the gate opens for a head the verifier never passed",
        [('Open(i) == status[i] # "none" /\\ ~NeedsPerson(i)', "Open(i) == ~NeedsPerson(i)")],
        "OnlyAVerifiedAllowedHeadMerges",
    ),
    (
        "merge-a-review-pass",
        "the gate opens for a head the verifier passed as needing a person",
        [('Open(i) == status[i] # "none" /\\ ~NeedsPerson(i) /\\ i', 'Open(i) == status[i] # "none" /\\ i')],
        "OnlyAVerifiedAllowedHeadMerges",
    ),
    (
        "pass-merges-a-labelled-pr",
        "the pass merges an approved waiting PR that carries the review label",
        [("/\\ Open(i) /\\ i \\in pre /\\ i \\notin Review", "/\\ Open(i) /\\ i \\in pre")],
        "OnlyAVerifiedAllowedHeadMerges",
    ),
    (
        "pass-merges-an-unapproved-pr",
        "the pass merges a waiting PR nobody approved",
        [("/\\ Open(i) /\\ i \\in pre /\\ i \\notin Review", "/\\ Open(i) /\\ i \\notin Review")],
        "OnlyAVerifiedAllowedHeadMerges",
    ),
    (
        "pass-merges-an-unverified-pr",
        "the pass merges an approved waiting PR whose head has no pass",
        [("/\\ i \\notin merged /\\ Open(i) /\\ i \\in pre", "/\\ i \\notin merged /\\ i \\notin held /\\ i \\in pre")],
        "OnlyAVerifiedAllowedHeadMerges",
    ),
    (
        "an-approved-wait-is-stuck",
        "an approved waiting PR whose head may merge stays waiting for the owner for good, as before `queue.py approve`",
        [("PassMerge(m, i) == PassReady(m, i) /\\ Merge", "PassMerge(m, i) == PassReady(m, i) /\\ FALSE /\\ Merge")],
        LIVE,
    ),
    (
        "one-unreadable-pr-stops-the-pass",
        "a PR whose read comes back malformed aborts the pass, so no other approved waiting PR merges",
        [("PassMerge(m, i) == PassReady(m, i) /\\ Merge", "PassMerge(m, i) == PassReady(m, i) /\\ unread = {} /\\ Merge")],
        LIVE,
        ('  Machines = {"mac", "linux"}\n  Garbles = {}\n  NoTree = {"i3"}\n', '  Machines = {"mac"}\n  Garbles = {"i3"}\n  NoTree = {}\n'),
    ),
    (
        "a-paged-pr-is-never-read-whole",
        "an approved waiting PR whose checks run past one page of the last read waits for the owner for good",
        [("PassMerge(m, i) == PassReady(m, i) /\\ Merge", "PassMerge(m, i) == PassReady(m, i) /\\ i \\notin Paged /\\ Merge")],
        LIVE,
    ),
    (
        "pass-survives-a-push",
        "a pass stays on the PR after a new push",
        [('pr\' = pr \\cup {i} /\\ status\' = [status EXCEPT ![i] = "none"]', 'pr\' = pr \\cup {i} /\\ status\' = status')],
        "OnlyAVerifiedAllowedHeadMerges",
    ),
    (
        "launch-without-a-record",
        "a thread is started for an item and the queue never records it, so the next pass starts a second",
        [('/\\ Set(m, i, "running") /\\ pending\'', '/\\ UNCHANGED q /\\ pending\'')],
        "OneThreadPerItem",
    ),
    (
        "relaunch-a-start-that-landed",
        "a start whose answer was lost is launched again without checking that no thread came of it",
        [('Abort(m, i) == q[m][i] = "running" /\\ i \\notin thread /\\ i', 'Abort(m, i) == q[m][i] = "running" /\\ i')],
        "OneThreadPerItem",
    ),
    (
        "relaunch-a-start-still-in-flight",
        "an item whose start has not answered yet is launched again before the start can no longer land",
        [('/\\ i \\notin thread /\\ i \\notin pending /\\ Set', '/\\ i \\notin thread /\\ Set')],
        "OneThreadPerItem",
    ),
    ("launch-past-the-cap", "a thread is launched with the machine at its cap", [("  /\\ Busy(m) < Cap\n", "")], "CapHolds"),
    (
        "start-a-held-item",
        "an item is launched while it is held",
        [('/\\ i \\in Accepts[m] /\\ i \\notin held /\\ First(m, i)', '/\\ i \\in Accepts[m] /\\ First(m, i)')],
        "HeldNeverStarted",
    ),
    ("oldest-first", "the queue ignores priority", [("/\\ i \\notin held /\\ First(m, i)\n", "/\\ i \\notin held\n")], "PriorityOrder"),
    (
        "no-release",
        "an item whose thread's turn ended holds its slot for good",
        [('Freed(m, i) == q[m][i] = "running" /\\ UNCHANGED', 'Freed(m, i) == q[m][i] = "running" /\\ FALSE /\\ UNCHANGED'),
         ('TurnEnd(m, i) == q[m][i] = "running" /\\ (up', 'TurnEnd(m, i) == q[m][i] = "running" /\\ FALSE /\\ (up')],
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


def run(checker, scratch, src, cfg, name, why, edits, rule, *conf):
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
        cmd = [checker, "-workers", "2", "-metadir", os.path.join(d, f"meta{attempt}")]
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


def main(checker, scratch):
    src = open(os.path.join(HERE, "Queue.tla")).read()
    cfg = open(os.path.join(HERE, "Queue.cfg")).read()
    with ThreadPoolExecutor(max(1, (os.cpu_count() or 2) // 2)) as pool:
        said = list(pool.map(lambda m: run(checker, scratch, src, cfg, *m), M))
    print("\n".join(said))
    return int(any(not x.startswith("killed") for x in said))


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
