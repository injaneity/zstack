import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

LIVE = "a temporal property"
NO_QUEUE = ('  Queues = {"i1"}\n', "  Queues = {}\n")
ONE_MACHINE = ('  Retries = {}\n  Machines = {"mac", "linux"}\n', '  Retries = {"i1"}\n  Machines = {"mac"}\n')
RESUMES = ('  Retries = {}\n  Machines = {"mac", "linux"}\n  Garbles = {}\n  NoTree = {"i3"}\n  Resumes = {}\n  Fills = {}\n',
           '  Retries = {"i1"}\n  Machines = {"mac"}\n  Garbles = {}\n  NoTree = {"i3"}\n  Resumes = {"i1", "i2", "i3"}\n  Fills = {"mac"}\n')
IDLES = ('  Machines = {"mac", "linux"}\n  Garbles = {}\n  NoTree = {"i3"}\n  Resumes = {}\n  Fills = {}\n  Idles = {}\n',
         '  Machines = {"mac"}\n  Garbles = {}\n  NoTree = {"i3"}\n  Resumes = {}\n  Fills = {}\n  Idles = {"i1"}\n')
HERE = os.path.dirname(os.path.abspath(__file__))
M = [
    (
        "launch-below-the-disk-floor",
        "the pass launches a thread on a machine whose free disk is below run.disk_floor_gb",
        [("  /\\ m \\notin full\n  /\\ ~(i", "  /\\ ~(i")],
        "NoLaunchBelowTheDiskFloor",
        RESUMES,
    ),
    (
        "relaunch-below-the-disk-floor",
        "the pass sends an ended wait back to its thread on a machine whose free disk is below run.disk_floor_gb",
        [("/\\ Busy(m) < Cap /\\ m \\notin full\n  /\\ Needed(i)", "/\\ Busy(m) < Cap\n  /\\ Needed(i)")],
        "NoLaunchBelowTheDiskFloor",
        RESUMES,
    ),
    (
        "a-full-disk-stops-the-pass-merge",
        "a pass on a machine below its disk floor merges no approved waiting PR",
        [("PassMerge(m, i) == PassReady(m, i) /\\ Merge", "PassMerge(m, i) == PassReady(m, i) /\\ m \\notin full /\\ Merge")],
        LIVE,
        RESUMES,
    ),
    (
        "a-full-disk-stops-the-settle",
        "a pass on a machine below its disk floor never clears a waiting item whose PR merged",
        [('Settle(m, i) == q[m][i] = "waiting"', 'Settle(m, i) == m \\notin full /\\ q[m][i] = "waiting"')],
        LIVE,
        RESUMES,
    ),
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
        "a-merge-queue-base-is-stuck",
        "an approved waiting PR whose base branch has a merge queue waits for the owner for good, since the pass only merges directly",
        [("MergeOrQueue(i) == IF i \\in Queues THEN Enqueue(i) ELSE", "MergeOrQueue(i) == IF i \\in Queues THEN FALSE ELSE")],
        LIVE,
        ONE_MACHINE,
    ),
    (
        "a-hold-while-queued-is-ignored",
        "a hold or review label added to a PR the pass put in the merge queue leaves it queued, so GitHub still merges it",
        [("Dequeue(m, i) == q[m][i] = \"waiting\" /\\ i \\in inq", "Dequeue(m, i) == q[m][i] = \"waiting\" /\\ FALSE /\\ i \\in inq")],
        LIVE,
        ONE_MACHINE,
    ),
    (
        "requeue-a-dropped-head",
        "the pass queues again a head GitHub dropped from the merge queue after its one fresh run",
        [("/\\ i \\notin lost /\\ ~Stands(i)\n", "/\\ i \\notin lost\n")],
        "ASecondDropIsNotQueued",
        ONE_MACHINE,
    ),
    (
        "a-first-drop-goes-back-to-tasks",
        "a preapproved head GitHub drops from the merge queue for the first time is never queued again, so a flake sends it back to tasks "
        "and its preapproved verdict never merges",
        [("Stands(i) == i \\in dropped /\\ (i \\notin pre \\/ i \\notin Retries \\/ i \\in retried)", "Stands(i) == i \\in dropped")],
        LIVE,
        ONE_MACHINE,
    ),
    (
        "a-new-head-is-blamed-for-a-drop",
        "a pass that finds an entry gone charges the drop to the PR's current head, so a head pushed since, which never entered the merge queue, "
        "is never queued",
        [("dropped' = dropped \\cup ({i} \\cap gone)", "dropped' = dropped \\cup {i}")],
        "OnlyADroppedHeadIsBlocked",
        ONE_MACHINE,
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
        "an item whose thread's turn ended, or that went idle, holds its slot for good",
        [('Freed(m, i) == q[m][i] = "running" /\\ UNCHANGED', 'Freed(m, i) == q[m][i] = "running" /\\ FALSE /\\ UNCHANGED'),
         ('TurnEnd(m, i) == q[m][i] = "running" /\\ (up', 'TurnEnd(m, i) == q[m][i] = "running" /\\ FALSE /\\ (up'),
         ('Idle(m, i) == q[m][i] = "running"', 'Idle(m, i) == FALSE /\\ q[m][i] = "running"')],
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
        [('ELSE \\/ Finished(i) /\\ Set(m, i, "done")', 'ELSE \\/ Set(m, i, "done")')],
        "NothingUnfinishedIsCleared",
    ),
    (
        "clear-a-dead-thread-with-no-pr",
        "an item whose thread died before it opened a PR is cleared as done, so it never runs again",
        [('Set(m, i, IF r THEN "waiting" ELSE "none")', 'Set(m, i, IF r THEN "waiting" ELSE "done")')],
        "ADeadThreadWithNoPRIsRunAgain",
    ),
    (
        "clear-an-unmarked-turn-with-no-pr",
        "an item whose thread ended its turn with no PR and never ran `queue.py complete` is cleared as done, so it never runs again",
        [("IF i \\notin pr /\\ ~marked THEN", "IF FALSE THEN")],
        "AnUnmarkedTurnWithNoPRIsRunAgain",
    ),
    (
        "rerun-a-refused-push",
        "an item whose push the pre-push hook refused, with no allowed remote pushable, goes back to tasks at its turn end, so the next "
        "pass runs it again and the hook refuses it again",
        [('IF i \\notin pr /\\ refused THEN Set(m, i, "waiting")', 'IF FALSE THEN Set(m, i, "waiting")')],
        "ARefusedPushWaitsForAPerson",
    ),
    (
        "rerun-a-refused-push-that-died",
        "an item whose push the pre-push hook refused goes back to tasks when its thread then dies, so the next pass runs it again",
        [('Set(m, i, IF r THEN "waiting" ELSE "none")', 'Set(m, i, "none")')],
        "ARefusedPushWaitsForAPerson",
    ),
    (
        "an-ended-wait-is-stuck",
        "a waiting PR whose thread has ended, and whose head needs that thread's work, waits on the ended thread for good",
        [('Resume(m, i) == Stuck(m, i) /\\ Set', 'Resume(m, i) == Stuck(m, i) /\\ FALSE /\\ Set')],
        LIVE,
        RESUMES,
    ),
    (
        "resume-forgets-a-drop",
        "an item sent back to tasks loses its merge-queue drop record, so the pass queues the dropped head again",
        [('Resume(m, i) == Stuck(m, i) /\\ Set(m, i, "none") /\\ needless\' = (needless \\/ ~Needed(i))\n'
          '  /\\ UNCHANGED <<held, work, ',
          'Resume(m, i) == Stuck(m, i) /\\ Set(m, i, "none") /\\ needless\' = (needless \\/ ~Needed(i)) /\\ dropped\' = dropped \\ {i}\n'
          '  /\\ UNCHANGED <<held, pre, unread, up, full, pr, status, stale, merged, inq, gone, lost, requeued, retried, broken, red, '
          'by, okAtMerge, ')],
        "ASecondDropIsNotQueued",
        RESUMES,
    ),
    (
        "resume-a-wait-for-a-person",
        "a waiting PR whose head waits on a person's review, or carries the review label, goes back to tasks once its thread has ended",
        [('  /\\ ~NeedsPerson(i) /\\ i \\notin Review /\\ (status[i] = "none"', '  /\\ (status[i] = "none"')],
        "AResumedThreadIsNeeded",
        RESUMES,
    ),
    (
        "a-broken-passed-head-is-stuck",
        "a waiting PR whose passed head conflicts with its base waits on its ended thread for good",
        [(' \\/ Stands(i) \\/ i \\in broken)\nResume', ' \\/ Stands(i))\nResume')],
        LIVE,
        RESUMES,
    ),
    (
        "resume-a-red-passed-head",
        "a waiting PR whose head the verifier passed, and whose only need is a red ci-gate, goes back to tasks once its thread has ended",
        [(' \\/ Stands(i) \\/ i \\in broken)\nResume', ' \\/ Stands(i) \\/ i \\in broken \\/ i \\in red)\nResume')],
        "AResumedThreadIsNeeded",
        RESUMES,
    ),
    (
        "merge-a-broken-head",
        "the gate or the pass merges a passed head that conflicts with its base",
        [('/\\ i \\notin held /\\ i \\notin broken\n', '/\\ i \\notin held\n')],
        "OnlyAVerifiedAllowedHeadMerges",
        RESUMES,
    ),
    (
        "thread-merges-a-dropped-head",
        "the gate lets a thread merge a head GitHub dropped from the merge queue",
        [('ThreadMerge(m, i) == Takes(m, i) /\\ Open(i) /\\ ~Stands(i)', 'ThreadMerge(m, i) == Takes(m, i) /\\ Open(i)')],
        "OnlyAVerifiedAllowedHeadMerges",
        ONE_MACHINE,
    ),
    (
        "launch-a-resumed-item-unchecked",
        "a resumed item starts a thread on a PR that merged, entered the merge queue or was held after it went back to tasks",
        [("  /\\ Needed(i)\n  /\\ Set(m, i, \"running\")", "  /\\ Set(m, i, \"running\")")],
        "AResumedThreadIsNeeded",
        RESUMES,
    ),
    (
        "an-idle-thread-holds-its-slot",
        "a thread that hangs or waits on a question holds its slot for good, as when run.idle_min was read nowhere",
        [("\\A m \\in Machines, i \\in Idles : WF_vars(Idle(m, i))", "\\A m \\in Machines, i \\in Idles : TRUE")],
        LIVE,
        IDLES,
    ),
    (
        "rerun-a-live-idle-thread",
        "an item freed as idle runs again while its old thread still lives",
        [("IdleOver(m, i) == q[m][i] = \"waiting\" /\\ i \\in idle /\\ i \\notin thread", "IdleOver(m, i) == q[m][i] = \"waiting\" /\\ i \\in idle")],
        "OneThreadPerItem",
        IDLES,
    ),
    (
        "keep-an-ended-idle-item",
        "an item freed as idle waits for good after its old thread ends",
        [("Rerun(m, i) == IdleOver(m, i) /\\", "Rerun(m, i) == FALSE /\\ IdleOver(m, i) /\\")],
        LIVE,
        IDLES,
    ),
    (
        "settle-a-live-idle-tree",
        "the pass clears an idle item whose PR merged, removing its worktree while its thread may still run",
        [("/\\ i \\in merged /\\ i \\notin idle /\\ Set(m, i, \"done\")", "/\\ i \\in merged /\\ Set(m, i, \"done\")")],
        "ALiveThreadKeepsItsTree",
        IDLES,
    ),
    (
        "clean-a-live-idle-tree",
        "the pass cleans the ignored files of an idle thread's worktree while that thread may still run",
        [("/\\ cleanedLive' = (cleanedLive \\/ i \\in pr /\\ i \\notin idle')", "/\\ cleanedLive' = (cleanedLive \\/ i \\in pr)")],
        "ALiveThreadKeepsItsTree",
        IDLES,
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
    old, new = conf[0] if conf else NO_QUEUE
    if cfg.count(old) != 1:
        return f"BROKEN   {name}: config line not found once: {old.strip()!r}"
    open(os.path.join(d, "Queue.cfg"), "w").write(cfg.replace(old, new))
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
