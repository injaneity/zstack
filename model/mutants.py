import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
M = [
    ("lost-run-sends-no-wake", "bin/pi-run collect sent nothing for a lost run (before #50)",
     [("collected' = TRUE /\\ woke' = TRUE", "collected' = TRUE /\\ woke' = woke")], "CollectedFailureWoke"),
    ("retry-reuses-id", "bin/pi-run step() started from collected with the same --session-id (before #50)",
     [("/\\ reopened' = FALSE /\\ cont' = FALSE /\\ due' = FALSE /\\ limited' = FALSE /\\ slot' = FALSE /\\ drained' = FALSE",
       "/\\ reopened' = TRUE /\\ cont' = FALSE /\\ due' = FALSE /\\ limited' = FALSE /\\ slot' = FALSE /\\ drained' = FALSE"),
      ("slot' = TRUE /\\ drained' = TRUE /\\ reopened' = cont /\\", "slot' = TRUE /\\ drained' = TRUE /\\ reopened' = (cont \\/ reopened) /\\"),
      ("slot' = TRUE /\\ drained' = FALSE /\\ reopened' = FALSE /\\", "slot' = TRUE /\\ drained' = FALSE /\\ reopened' = reopened /\\")],
     "RetryIsFresh"),
    ("paused-holds-slot", "bin/hourly slots() counted a paused run as running (before #50)",
     [("/\\ phase' = \"queued\" /\\ cont' = TRUE /\\ due' = FALSE /\\ limited' = TRUE /\\ slot' = FALSE",
       "/\\ phase' = \"queued\" /\\ cont' = TRUE /\\ due' = FALSE /\\ limited' = TRUE /\\ slot' = TRUE")],
     "SlotOnlyWhileRunning"),
    ("resume-skips-queue", "`pi-run resume <id>` (hourly playbook step 2) started a paused run outside the drain (before #50)",
     [("StartNow == phase = \"queued\" /\\ ~cont\n  /\\ phase' = \"running\" /\\ slot' = TRUE /\\ drained' = FALSE /\\ reopened' = FALSE",
       "StartNow == phase = \"queued\" /\\ (cont => due)\n  /\\ phase' = \"running\" /\\ slot' = TRUE /\\ drained' = FALSE /\\ reopened' = cont")],
     "ResumeGoesThroughTheQueue"),
    ("resume-before-resume-at", "a drain that starts a continuation before its resume_at",
     [("Drain == phase = \"queued\" /\\ (cont => due)", "Drain == phase = \"queued\"")], "ResumeGoesThroughTheQueue"),
    ("continuation-never-drained", "a drain that skips queued runs with resume_at (the queue has no fairness)",
     [("WF_vars(Drain) /\\ ", "")], "ContinuationRuns"),
    ("bot-merges-ignored", "merge_audit() ignored bot and app mergers (before #46)",
     [("JobAlerts == phase = \"merged\" /\\ by = \"other\" /\\ ~alerted", "JobAlerts == FALSE /\\ phase = \"merged\"")],
     "EveryOtherMergeWakes"),
    ("merged-logged-at-enqueue", "merge_job.py before #42 logged merged when gh pr merge returned",
     [("phase' = \"inqueue\" /\\ byJob' = TRUE /\\ atQueue' = Facts\n"
       "  /\\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, by, alerted, logged, atMerge>>",
       "phase' = \"inqueue\" /\\ byJob' = TRUE /\\ atQueue' = Facts /\\ logged' = TRUE\n"
       "  /\\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, by, alerted, atMerge>>")],
     "MergedEventMeansMerged"),
    ("no-dequeue", "the job never re-gated a queued PR (before #45)",
     [("JobDequeues == phase = \"inqueue\" /\\ byJob /\\ ~Gate", "JobDequeues == FALSE /\\ phase = \"inqueue\"")], "OffGateLeavesQueue"),
    ("no-tier-merges", "a gate without the tier enum",
     [("Gate == ci = \"green\" /\\ ~held /\\ tier \\in Tiers", "Gate == ci = \"green\" /\\ ~held")], "MergedOnGate"),
    ("no-preapprove-pin", "a gate without the deep/human preapproval",
     [("/\\ (tier = \"deep\" => pre)", "")], "MergedOnGate"),
    ("red-merges", "a gate that takes any finished ci-gate",
     [("Gate == ci = \"green\"", "Gate == ci # \"pending\"")], "MergedOnGate"),
    ("draft-enqueued", "a job that queues drafts",
     [("JobEnqueues == phase = \"open\" /\\ ~draft", "JobEnqueues == phase = \"open\"")], "DraftIsNeverMerged"),
    ("push-keeps-queue-entry", "a queue entry that survives a push (GitHub drops it)",
     [("/\\ phase' = (IF phase = \"inqueue\" THEN \"open\" ELSE phase) /\\ byJob' = FALSE", "/\\ phase' = phase /\\ byJob' = byJob")],
     "DeepNeedsPreapproveAtHead"),
    ("collect-never-runs", "no pickup collects ended runs",
     [("WF_vars(Collect) /\\ ", "")], "EveryFailureWakes"),
    ("red-pr-flagged-to-zane", "a PR red after its rerun waited for Zane or a fresh run, never its own thread (before 11 Nov)",
     [("  /\\ WF_vars(CiReopen) /\\ WF_vars(ThreadFixes)", "  /\\ WF_vars(ThreadFixes)")], "RedPrGoesBackToItsThread"),
    ("resume-uncapped", "a 429 resumed a run on every drain, with no config/policy.toml resume.cap (before the cap)",
     [("Limit == phase = \"running\" /\\ limits < ResumeCap", "Limit == phase = \"running\" /\\ limits <= ResumeCap")],
     "ResumesCapped"),
    ("spent-limit-silent", "a run out of resumes ends failed with no wake",
     [("LimitSpent == phase = \"running\" /\\ limits = ResumeCap /\\ phase' = \"failed\" /\\ woke' = TRUE",
       "LimitSpent == phase = \"running\" /\\ limits = ResumeCap /\\ phase' = \"failed\" /\\ woke' = FALSE")],
     "FailedExitWoke"),
    ("ci-fix-uncapped", "red CI reopens the thread past config/policy.toml ci.fix_attempts",
     [("~fixing /\\ fixes < FixAttempts\n", "~fixing /\\ fixes <= FixAttempts\n")], "FixesCapped"),
    ("reopened-thread-holds-no-slot", "the drain reopens a thread without counting its slot",
     [("/\\ fixing' = TRUE /\\ fixes' = fixes + 1 /\\ slot' = TRUE", "/\\ fixing' = TRUE /\\ fixes' = fixes + 1 /\\ slot' = FALSE")],
     "SlotOnlyWhileRunning"),
]


TEMPORAL = {"EveryFailureWakes", "EveryOtherMergeWakes", "EveryJobMergeLogged", "OffGateLeavesQueue", "ContinuationRuns",
            "RedPrGoesBackToItsThread", "ReopenedThreadEnds"}


def main(jar, java, scratch):
    src = open(os.path.join(HERE, "Zstack.tla")).read()
    cfg = open(os.path.join(HERE, "Zstack.cfg")).read()
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
            open(os.path.join(d, "Zstack.tla"), "w").write(s)
            open(os.path.join(d, "Zstack.cfg"), "w").write(cfg)
            out = subprocess.run([java, "-cp", jar, "tlc2.TLC", "-workers", "2", "-metadir", os.path.join(d, "meta"),
                                  "-config", "Zstack.cfg", "Zstack.tla"], cwd=d, capture_output=True, text=True, timeout=600).stdout
            m = re.search(r"Invariant (\w+) is violated|(Temporal properties were violated)|(not completely specified|Deadlock)", out)
            got = m and (m.group(1) or (m.group(2) and "a temporal property"))
            if got and (got == rule or got == "a temporal property" and rule in TEMPORAL):
                print(f"killed   {name}: {why} -> {rule if got != rule else got} breaks")
            else:
                print(f"SURVIVED {name}: {why} -> {got or (m and 'model error: ' + m.group(3)) or 'no error'} (expected {rule})")
                bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
