---- MODULE Zstack ----
\* The zstack flow: one piece of work from the chat to merged. bin/PiRun.tla is the run lifecycle in detail; this is the
\* whole flow, with the merge job (hosts/vm/merge_job.py) and GitHub around it. Checked in CI by tests/zstack-model.sh:
\* TLC passes Zstack.cfg, every mutant in mutants.py fails it, and Race.cfg (a probe) breaks.
\* It keeps Zane's rules (merge gate = ci-gate green + no hold row + a preapproval at the head for deep/human;
\* drafts never merge; failures wake the chat; risk tiers are a fixed enum; a merge the job did not make wakes)
\* with no state that exists only to serve another one.
\*
\* What it holds the code to:
\*  - QUEUED is a run.json state, not a marker file next to it (one state machine, in bin/pi-run's step()).
\*  - The session's inner steps (plan, design, build, verify, push, staging) are the playbook's, not zstack's: one state.
\*  - A retry is a fresh id. step() refuses a start from collected, so a session is reopened only after a 429.
\*  - A 429 sends the run back to Queued with resume_at (no Paused state). It frees its slot, and the hourly queue drain
\*    starts it again, once resume_at has passed, in the same pi session (--session-id). No separate resume path.
\*  - A lost run (KILL or OOM) sends its failed wake when it is collected.
\*  - The merge job keeps one decision per PR: enqueue, rerun once, or nothing. Who must act is the reason, not a
\*    state. There is no buffer; the hourly reads the job's --dry-run lines.
\*  - Each pass re-gates the job's own entries in GitHub's merge queue and dequeues one that now fails the gate.
\*    Another actor's entry is not touched (whether it should be is an open question); its merge wakes.
\*  - merge-watch wakes for every merge of our PR that the job did not queue, whoever merged it (bots and apps too).
EXTENDS Naturals

VARIABLES
  phase,     \* "queued" "running" "failed" "done" | "open" "inqueue" "merged"
  draft,     \* isDraft
  lost,      \* the failed run was KILLed or OOM, so its supervisor sent nothing
  collected, \* the hourly ran `pi-run collect` on the ended run
  woke,      \* the failed wake went out for this attempt
  reopened,  \* the running session reopened an earlier transcript (only a 429's continuation may)
  cont,      \* the queued run is a 429's continuation: run.json queued with resume_at, same attempt and session
  due,       \* the continuation's resume_at has passed
  limited,   \* this attempt stopped on a 429 (a fresh id clears it)
  slot,      \* the run holds one of the shared slots (bin/hourly slots())
  drained,   \* the run was started by the hourly queue drain, not by a direct `pi-run <id>`
  ci,        \* ci-gate at the head: "pending" "green" "red"
  reran,     \* the job reran this head's failed runs
  tier,      \* the declared risk: "none" "light" (light|standard) "deep" (deep|human)
  pre,       \* a preapprove row is pinned to this head
  held,      \* a hold row names the PR (or the holds file is unreadable, or tick/PAUSED): Zane's stop
  conflict,  \* mergeable is CONFLICTING
  byJob,     \* the queue entry was made by the merge job
  by,        \* who merged: "none" "job" "other" (injaneity by hand, a bot, an app, a teammate)
  alerted,   \* an unexpected-merge wake went out
  logged,    \* events.log has `merged cua#N`
  atQueue,   \* the gate's facts when the job queued it
  atMerge    \* the facts when GitHub merged it

vars == <<phase, draft, lost, collected, woke, reopened, cont, due, limited, slot, drained, ci, reran, tier, pre, held, conflict, byJob, by,
          alerted, logged, atQueue, atMerge>>
RunVars == <<lost, collected, woke, reopened, cont, due, limited, slot, drained>>
PRVars == <<draft, ci, reran, tier, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
Tiers == {"light", "deep"}   \* TIERS in merge_job.py; "none" stands for a PR that declares none of them
NoFacts == [green |-> FALSE, held |-> FALSE, preOk |-> FALSE, tier |-> "none", draft |-> FALSE]
PRopen == {"open", "inqueue"}

Init ==
  /\ phase = "queued" /\ draft = FALSE /\ lost = FALSE /\ collected = FALSE /\ woke = FALSE /\ reopened = FALSE
  /\ cont = FALSE /\ due = FALSE /\ limited = FALSE /\ slot = FALSE /\ drained = FALSE
  /\ ci = "pending" /\ reran = FALSE /\ tier = "none" /\ pre = FALSE /\ held = FALSE /\ conflict = FALSE
  /\ byJob = FALSE /\ by = "none" /\ alerted = FALSE /\ logged = FALSE /\ atQueue = NoFacts /\ atMerge = NoFacts

\* ---------------- the run: bin/pi-run step() ----------------
\* queued --start--> running. A refused start (parity, no credential, slots full) changes nothing: the run stays queued.
\* The hourly drain starts the oldest queued run while a slot is free; a continuation also needs resume_at passed, and
\* reopens its own session. The drain's other checks (slots, peer at main, the floor) are in bin/hourly, not here.
Drain == phase = "queued" /\ (cont => due)
  /\ phase' = "running" /\ slot' = TRUE /\ drained' = TRUE /\ reopened' = cont /\ cont' = FALSE
  /\ UNCHANGED <<lost, collected, woke, due, limited>> /\ UNCHANGED PRVars
\* The chat's `pi-run <id> <role> <brief>` starts a fresh run now. It never starts a continuation.
StartNow == phase = "queued" /\ ~cont
  /\ phase' = "running" /\ slot' = TRUE /\ drained' = FALSE /\ reopened' = FALSE /\ cont' = FALSE
  /\ UNCHANGED <<lost, collected, woke, due, limited>> /\ UNCHANGED PRVars
\* running --limit--> queued: an upstream 429. No wake, the slot is freed, and resume_at is set.
Limit == phase = "running" /\ phase' = "queued" /\ cont' = TRUE /\ due' = FALSE /\ limited' = TRUE /\ slot' = FALSE
  /\ UNCHANGED <<lost, collected, woke, reopened, drained>> /\ UNCHANGED PRVars
\* Time passes resume_at. The run stays Queued.
ResumeAtPasses == phase = "queued" /\ cont /\ ~due /\ due' = TRUE
  /\ UNCHANGED <<phase, lost, collected, woke, reopened, cont, limited, slot, drained>> /\ UNCHANGED PRVars
\* running --exit rc!=0--> failed: the supervisor sends the failed wake, then writes exited.
ExitFailed == phase = "running" /\ phase' = "failed" /\ woke' = TRUE /\ slot' = FALSE
  /\ UNCHANGED <<lost, collected, reopened, cont, due, limited, drained>> /\ UNCHANGED PRVars
\* KILL or OOM: the supervisor dies with pi and sends nothing.
Killed == phase = "running" /\ phase' = "failed" /\ lost' = TRUE /\ woke' = FALSE /\ slot' = FALSE
  /\ UNCHANGED <<collected, reopened, cont, due, limited, drained>> /\ UNCHANGED PRVars
\* `pi-run collect`: a lost run's collect sends the failed wake the supervisor could not (wake, then write).
Collect == phase = "failed" /\ ~collected /\ collected' = TRUE /\ woke' = TRUE
  /\ UNCHANGED <<phase, lost, reopened, cont, due, limited, slot, drained>> /\ UNCHANGED PRVars
\* The chat files it again under a fresh id: a new review dir and a new session.
Retry == phase = "failed" /\ collected
  /\ phase' = "queued" /\ lost' = FALSE /\ collected' = FALSE /\ woke' = FALSE
  /\ reopened' = FALSE /\ cont' = FALSE /\ due' = FALSE /\ limited' = FALSE /\ slot' = FALSE /\ drained' = FALSE
  /\ UNCHANGED PRVars
\* running --exit rc 0-->: no trycua/cua PR (research, or a cua-staging PR the session merged itself), or a PR.
\* The session declares a tier, or forgets to.
ExitOk == phase = "running" /\ slot' = FALSE
  /\ \/ phase' = "done" /\ UNCHANGED PRVars
     \/ /\ phase' = "open" /\ draft' \in BOOLEAN /\ tier' \in {"none"} \cup Tiers
        /\ UNCHANGED <<ci, reran, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED <<lost, collected, woke, reopened, cont, due, limited, drained>>

\* ---------------- the PR: GitHub and the people around it ----------------
CiFinishes == phase \in PRopen /\ ci = "pending" /\ ci' \in {"green", "red"}
  /\ UNCHANGED <<phase, draft, reran, tier, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
\* A push by a session or the release app: new head, so ci restarts, the preapproval pin is void, a queue entry drops.
Push == phase \in PRopen
  /\ ci' = "pending" /\ reran' = FALSE /\ pre' = FALSE /\ conflict' = FALSE
  /\ phase' = (IF phase = "inqueue" THEN "open" ELSE phase) /\ byJob' = FALSE
  /\ UNCHANGED <<draft, tier, held, by, alerted, logged, atQueue, atMerge>> /\ UNCHANGED RunVars
\* The session adds the missing `risk:` line (a body edit, not a push).
DeclareTier == phase = "open" /\ tier = "none" /\ tier' \in Tiers
  /\ UNCHANGED <<phase, draft, ci, reran, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
MainMoves == phase = "open" /\ ~conflict /\ conflict' = TRUE
  /\ UNCHANGED <<phase, draft, ci, reran, tier, pre, held, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
\* Zane, or a session whose brief says `merge: allowed`, readies a draft. Nothing else does.
Ready == phase \in PRopen /\ draft /\ draft' = FALSE
  /\ UNCHANGED <<phase, ci, reran, tier, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
\* Zane edits the holds file: a hold row on or off, a preapprove row at today's head.
ZaneHolds == phase \in PRopen /\ held' = ~held
  /\ UNCHANGED <<phase, draft, ci, reran, tier, pre, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
ZanePreapproves == phase \in PRopen /\ tier = "deep" /\ ~pre /\ pre' = TRUE
  /\ UNCHANGED <<phase, draft, ci, reran, tier, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars

\* ---------------- the merge job: hosts/vm/merge_job.py, one pass ----------------
\* gate(): exactly three checks at the head.
Gate == ci = "green" /\ ~held /\ tier \in Tiers /\ (tier = "deep" => pre)
Facts == [green |-> ci = "green", held |-> held, preOk |-> (tier # "deep" \/ pre), tier |-> tier,
          draft |-> draft]
\* merge: gate and blockers (draft, conflict) pass, so `gh pr merge --match-head-commit` puts it in the queue.
JobEnqueues == phase = "open" /\ ~draft /\ Gate /\ ~conflict
  /\ phase' = "inqueue" /\ byJob' = TRUE /\ atQueue' = Facts
  /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, by, alerted, logged, atMerge>> /\ UNCHANGED RunVars
\* rerun: a red ci-gate's failed runs, once per head.
JobReruns == phase = "open" /\ ci = "red" /\ ~reran /\ ci' = "pending" /\ reran' = TRUE
  /\ UNCHANGED <<phase, draft, tier, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
\* The new step: the job's own queue entry that fails the gate now (a hold row came, PAUSED) leaves the queue.
JobDequeues == phase = "inqueue" /\ byJob /\ ~Gate /\ phase' = "open" /\ byJob' = FALSE
  /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
\* landed(): `merged` is logged only once GitHub says MERGED, and only for the job's own queue entry.
JobLogsMerged == phase = "merged" /\ by = "job" /\ ~logged /\ logged' = TRUE
  /\ UNCHANGED <<phase, draft, ci, reran, tier, pre, held, conflict, byJob, by, alerted, atQueue, atMerge>>
  /\ UNCHANGED RunVars
\* merge_watch(): every merge of our PR that the job did not queue wakes, whoever merged it.
JobAlerts == phase = "merged" /\ by = "other" /\ ~alerted /\ alerted' = TRUE
  /\ UNCHANGED <<phase, draft, ci, reran, tier, pre, held, conflict, byJob, by, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars

\* ---------------- GitHub's merge queue and other mergers ----------------
\* Another actor queues our PR (app/cua-agents queued #7037 and #7039 on 9 Nov). GitHub queues no draft.
OtherEnqueues == phase = "open" /\ ~draft /\ ci = "green" /\ ~conflict /\ phase' = "inqueue" /\ byJob' = FALSE
  /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
QueueMerges == phase = "inqueue" /\ phase' = "merged" /\ by' = (IF byJob THEN "job" ELSE "other") /\ atMerge' = Facts
  /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, byJob, alerted, logged, atQueue>> /\ UNCHANGED RunVars
\* The queue lets it go unmerged (checks_timed_out on #7039, a conflict in the merge group).
QueueDrops == phase = "inqueue" /\ phase' = "open" /\ byJob' = FALSE
  /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
\* A merge outside the queue: injaneity by hand, a bot, an app, a teammate. GitHub merges no draft.
OtherMerges == phase = "open" /\ ~draft /\ ~conflict /\ phase' = "merged" /\ by' = "other" /\ atMerge' = Facts
  /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, byJob, alerted, logged, atQueue>> /\ UNCHANGED RunVars

\* The chat may also stop after a failure instead of filing it again.
Ends == (phase \in {"done", "merged"} \/ (phase = "failed" /\ collected)) /\ UNCHANGED vars

Next == Drain \/ StartNow \/ Limit \/ ResumeAtPasses \/ ExitFailed \/ Killed \/ Collect \/ Retry \/ ExitOk
  \/ CiFinishes \/ Push \/ DeclareTier \/ MainMoves \/ Ready \/ ZaneHolds \/ ZanePreapproves
  \/ JobEnqueues \/ JobReruns \/ JobDequeues \/ JobLogsMerged \/ JobAlerts
  \/ OtherEnqueues \/ QueueMerges \/ QueueDrops \/ OtherMerges \/ Ends
\* The hourly drains the queue and collects every ended run, time passes resume_at, and the job's pass always comes round.
Spec == Init /\ [][Next]_vars /\ WF_vars(Drain) /\ WF_vars(ResumeAtPasses) /\ WF_vars(Collect) /\ WF_vars(JobLogsMerged) /\ WF_vars(JobAlerts) /\ WF_vars(JobDequeues)

\* ---------------- Zane's rules ----------------
JobMerged == phase = "merged" /\ by = "job"
\* Merge gate: ci-gate green, no hold row, a tier from the enum, and a preapproval at the head for deep/human.
MergedOnGate == JobMerged => atQueue.green /\ ~atQueue.held /\ atQueue.tier \in Tiers /\ atQueue.preOk
\* The pin is at the merged head: no push can come between the job's gate and GitHub's merge.
DeepNeedsPreapproveAtHead == JobMerged => atMerge.preOk /\ atMerge.green
\* Drafts never merge.
DraftIsNeverMerged == phase = "merged" => ~atMerge.draft /\ ~(JobMerged /\ atQueue.draft)
\* Risk tiers are a fixed enum: a PR with no tier from it is never merged by the job.
TierIsFromEnum == JobMerged => atQueue.tier \in Tiers
\* `merged cua#N` means GitHub merged it, through the job's own entry.
MergedEventMeansMerged == logged => JobMerged
\* The job's own queue entry that fails the gate (a hold row came after the job queued it) does not stay there.
OffGateLeavesQueue == [](phase = "inqueue" /\ byJob /\ ~Gate => <>(phase # "inqueue" \/ Gate))
\* Nothing restarts a session (SKILL.md:9): only a 429's continuation reopens one.
RetryIsFresh == phase = "running" /\ reopened => limited
\* A paused run frees its slot (Zane, 10 Nov): only a running run holds one.
SlotOnlyWhileRunning == slot <=> phase = "running"
\* The hourly queue drain resumes it (Zane, 10 Nov), once resume_at has passed (step()): no other path reopens a session.
ResumeGoesThroughTheQueue == phase = "running" /\ reopened => drained /\ due
\* A 429'd run comes back: it does not sit in Queued for ever.
ContinuationRuns == [](cont => <>(phase = "running"))
\* A 429 is never a failure: a queued continuation has sent no wake.
PauseIsSilent == cont => ~woke /\ ~collected
\* Failures wake the chat: every collected failure has sent its wake, and every failure wakes eventually.
CollectedFailureWoke == phase = "failed" /\ collected => woke
EveryFailureWakes == [](phase = "failed" => <>(woke \/ phase # "failed"))
\* A merge the job did not make raises an unexpected-merge wake.
EveryOtherMergeWakes == [](by = "other" => <>alerted)
\* The job's merges are all logged.
EveryJobMergeLogged == [](JobMerged => <>logged)

\* A probe, not a rule this design meets: Race.cfg checks it to show the one window left (a hold row added while the
\* job's entry waits in GitHub's queue; an open question).
HeldIsNeverMergedStrict == JobMerged => ~atMerge.held

\* For the chart only (Chart.cfg): slot is phase = "running" (SlotOnlyWhileRunning), and limited, drained,
\* atQueue and atMerge are history the rules read and no step reads (reopened once the session has ended, due once the run left Queued), so states
\* that differ only there draw as one. Zstack.cfg checks every rule without this view.
ChartView == <<phase, draft, lost, collected, woke, phase = "running" /\ reopened, cont, cont /\ due, ci, reran, tier, pre, held, conflict,
               byJob, by, alerted, logged>>
====
