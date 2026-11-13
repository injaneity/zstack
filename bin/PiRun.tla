---- MODULE PiRun ----
\* The run lifecycle bin/pi-run implements for one id. Each action is one step() call under the review dir's
\* flock, so actions are atomic. Two launchers race through Start, the supervisor through Exit and Limit, and the
\* pickup through Collect. Check: skills/statechart/scripts/statechart bin/PiRun.tla --view bin/PiRun.view.mts
\* tests/pirun-model.sh runs that check in CI, fails when bin/PiRun.mmd is not the chart it draws, and runs the mutants in
\* bin/PiRun.mutants.py, each of which must make TLC fail. tests/test_pi_run.py fails when bin/pi-run's step() is not
\* that chart.
\*
\* One id is one attempt: a retry is a fresh id (onigiri-mode: nothing restarts a session). A 429 sends the run back to
\* queued with resume_at, which frees its slot; only the hourly queue drain starts it again, in the same pi session, once
\* resume_at has passed. The one way out of collected is the run's own PR going red after the merge job's rerun, at a
\* head the run pushed (Zane 11 Nov: "the thread itself should be responsible for resolving it"): `pi-run ci` queues the
\* same session again the same way, at most FixAttempts times (config/policy.toml ci.fix_attempts; 2 here keeps TLC small).
\* A run id resumes after an upstream limit at most ResumeCap times (config/policy.toml resume.cap; 2 here keeps TLC
\* small): the next limit ends it failed, so an upstream that always fails reaches the chat.
\* A pi can also block and never end on its own (cua-2879, 13 Nov: 20 h on a held slot). Its supervisor stops a pi that
\* shows no progress for config/policy.toml run.idle_min, and the run exits failed like any other failure.
EXTENDS Naturals

VARIABLES
  state,     \* run.json state: "absent" (no file), "queued", "running", "exited", "collected"
  cont,      \* the queued run is a 429's continuation (run.json resume_at set)
  live,      \* the supervisor or its process group still exists: the run holds a slot
  ended,     \* pi has ended (exit or kill), so there is a result to take
  killed,    \* it ended by KILL or OOM, so the supervisor sent nothing
  collects,  \* how many times the pickup took the result
  finish,    \* the brief has a `wake: finish` line (run.json wake_finish)
  wake,      \* the wake sent for this attempt: "none", "done" or "failed" (run.json wake)
  reopened,  \* the running session reopened an earlier transcript
  by,        \* who started the running session: "drain" (bin/hourly queue) or "direct" (`pi-run <id> <role> <brief>`)
  why,       \* what the queued continuation waits to continue for: "limit" (a 429) or "ci" (its PR is red)
  fixes,     \* how many times its PR's red CI reopened the run (run.json ci: the heads, per PR)
  limits,    \* how many times an upstream limit sent it back to the queue (run.json limit_resumes)
  spent,     \* it ended on an upstream limit with no resumes left
  stuck      \* the running pi is blocked: it shows no progress and never ends on its own

vars == <<state, cont, live, ended, killed, collects, finish, wake, reopened, by, why, fixes, limits, spent, stuck>>
FixAttempts == 2
ResumeCap == 2

Init ==
  /\ state = "absent" /\ cont = FALSE /\ live = FALSE /\ ended = FALSE /\ killed = FALSE /\ collects = 0
  /\ finish = FALSE /\ wake = "none" /\ reopened = FALSE /\ by = "direct" /\ why = "limit" /\ fixes = 0
  /\ limits = 0 /\ spent = FALSE /\ stuck = FALSE

Lost == state = "running" /\ ~live

\* `pi-run queue <id>` (bin/queue): the run waits for the hourly drain.
Queue ==
  /\ state = "absent" /\ state' = "queued"
  /\ UNCHANGED <<cont, live, ended, killed, collects, finish, wake, reopened, by, why, fixes, limits, spent, stuck>>

\* A fresh start, by the drain or directly (either of two racing launchers: the flock makes them take turns), with or
\* without a `wake: finish` line in its brief. A 429's continuation is never started this way.
Start ==
  /\ state \in {"absent", "queued"} /\ ~cont
  /\ \E f \in BOOLEAN, b \in {"drain", "direct"} : finish' = f /\ by' = b
  /\ state' = "running" /\ live' = TRUE /\ reopened' = FALSE /\ stuck' = FALSE
  /\ UNCHANGED <<cont, ended, killed, collects, wake, why, fixes, limits, spent>>

\* The hourly drain, once resume_at has passed: the same attempt continues its pi session.
Resume ==
  /\ state = "queued" /\ cont
  /\ state' = "running" /\ live' = TRUE /\ cont' = FALSE /\ reopened' = TRUE /\ by' = "drain" /\ stuck' = FALSE
  /\ UNCHANGED <<ended, killed, collects, finish, wake, why, fixes, limits, spent>>

\* pi stops on an upstream 429 (Claude Code or ChatGPT) or an incomplete Claude Code upstream response (bin/pi-run
\* LIMIT): back to the queue with resume_at, while it has resumes left. No wake, the slot is freed,
\* and there is no result yet.
Limit ==
  /\ state = "running" /\ live /\ ~stuck /\ limits < ResumeCap
  /\ state' = "queued" /\ cont' = TRUE /\ live' = FALSE /\ why' = "limit" /\ limits' = limits + 1
  /\ UNCHANGED <<ended, killed, collects, finish, wake, reopened, by, fixes, spent, stuck>>

\* The same stop with its ResumeCap resumes spent: the run exits failed and the supervisor sends the failed wake.
LimitSpent ==
  /\ state = "running" /\ live /\ ~stuck /\ limits = ResumeCap
  /\ state' = "exited" /\ live' = FALSE /\ ended' = TRUE /\ wake' = "failed" /\ spent' = TRUE
  /\ UNCHANGED <<cont, killed, collects, finish, reopened, by, why, fixes, limits, stuck>>

\* pi ends with rc 0 (ok) or not, and the supervisor sends and records the wake step() picks, then writes the rc.
Exit ==
  /\ state = "running" /\ live /\ ~stuck
  /\ \E ok \in BOOLEAN : wake' = IF ~ok THEN "failed" ELSE IF finish THEN "done" ELSE "none"
  /\ state' = "exited" /\ live' = FALSE /\ ended' = TRUE
  /\ UNCHANGED <<cont, killed, collects, finish, reopened, by, why, fixes, limits, spent, stuck>>

\* pi blocks on something that never ends (a tool call with no timeout on a process that never exits): no progress,
\* and no exit of its own.
Stall ==
  /\ state = "running" /\ live /\ ~stuck /\ stuck' = TRUE
  /\ UNCHANGED <<state, cont, live, ended, killed, collects, finish, wake, reopened, by, why, fixes, limits, spent>>

\* The supervisor sees no progress for run.idle_min (bin/pi-run supervise): it stops pi, and the run exits failed with
\* the failed wake, so the pickup collects it and its slot frees.
IdleStop ==
  /\ state = "running" /\ live /\ stuck
  /\ state' = "exited" /\ live' = FALSE /\ ended' = TRUE /\ wake' = "failed" /\ stuck' = FALSE
  /\ UNCHANGED <<cont, killed, collects, finish, reopened, by, why, fixes, limits, spent>>

\* KILL or OOM: the supervisor and pi die and run.json still says running.
Kill ==
  /\ state = "running" /\ live
  /\ live' = FALSE /\ ended' = TRUE /\ killed' = TRUE /\ stuck' = FALSE
  /\ UNCHANGED <<state, cont, collects, finish, wake, reopened, by, why, fixes, limits, spent>>

\* The pickup's `pi-run collect <id>`: takes an exited or lost run's result. A lost run's collect sends its failed wake.
Collect ==
  /\ state = "exited" \/ Lost
  /\ state' = "collected" /\ collects' = collects + 1 /\ wake' = IF Lost THEN "failed" ELSE wake
  /\ UNCHANGED <<cont, live, ended, killed, finish, reopened, by, why, fixes, limits, spent, stuck>>

\* `pi-run ci <id> <pr> <head>` (bin/hourly queue, before it drains): the PR is red after the merge job's rerun at a
\* head this run pushed, and it has fix attempts left. The collected run is queued again with resume_at now, for the
\* drain to reopen in its own session. A new attempt result comes later, so the last one's is cleared.
CiReopen ==
  /\ state = "collected" /\ fixes < FixAttempts
  /\ state' = "queued" /\ cont' = TRUE /\ why' = "ci" /\ fixes' = fixes + 1
  /\ ended' = FALSE /\ killed' = FALSE /\ collects' = 0 /\ wake' = "none" /\ spent' = FALSE
  /\ UNCHANGED <<live, finish, reopened, by, limits, stuck>>

\* Collected with its PR green, merged, or its fix attempts spent: it stays collected.
Done == state = "collected" /\ UNCHANGED vars

Next == Queue \/ Start \/ Resume \/ Limit \/ LimitSpent \/ Exit \/ Stall \/ IdleStop \/ Kill \/ Collect \/ CiReopen \/ Done
\* The drain starts every queued run and resumes every continuation eventually; a pi that is not stuck ends; the
\* supervisor stops a stuck one; the pickup collects. Nothing is fair about a pi ending once it is stuck.
Spec == Init /\ [][Next]_vars /\ WF_vars(Start) /\ WF_vars(Resume) /\ WF_vars(Exit) /\ WF_vars(IdleStop) /\ WF_vars(Collect)

\* A result is taken at most once, and only after its pi ended.
CollectedAtMostOnce == collects <= 1 /\ (collects = 1 => ended)
\* A killed run is told apart from a live one: it is collectable, never refused as "already running".
LostIsCollectable == Lost => ENABLED Collect
\* A lost run wakes the chat when it is collected.
LostWakesAtCollect == state = "collected" /\ killed => wake = "failed"
\* A done wake goes only to an attempt that asked for it; a run that did not ask never wakes on success.
DoneOnlyWhenAsked == wake = "done" => finish
\* A run that asked and exited sent a wake, done or failed: a monitored run is never silent at its exit.
AskedExitWakes == state = "exited" /\ finish => wake # "none"
\* A run that did not ask wakes only on failure.
DefaultIsFailuresOnly == state = "exited" /\ ~finish => wake \in {"none", "failed"}
\* A 429 is never a failure: it sends no wake, leaves nothing to collect, and holds no slot.
PauseIsSilent == cont => wake = "none" /\ ~ended /\ ~live
\* Only the hourly drain reopens a session, and only a 429's continuation is reopened.
ResumeGoesThroughTheQueue == state = "running" /\ reopened => by = "drain"
\* Collected is final but for its own PR's red CI: a retry is a fresh id, and only CiReopen reopens it, as a continuation.
CollectedIsFinal == [][state = "collected" => (state' = "collected" \/ (state' = "queued" /\ cont' /\ why' = "ci" /\ fixes' = fixes + 1))]_vars
\* An upstream limit resumes a run id at most resume.cap times.
ResumesCapped == limits <= ResumeCap
\* A run that ran out of resumes is a failure the chat hears about: its exit sent the failed wake.
SpentLimitFails == spent => wake = "failed" /\ state \in {"exited", "collected"}
\* The thread fixes its PR's CI at most fix_attempts times; then it is Zane's.
FixesCapped == fixes <= FixAttempts
\* A CI continuation went through collect first: the run's last result was taken before its session reopened.
CiReopenAfterCollect == [][(state' = "queued" /\ why' = "ci" /\ state # "queued") => state = "collected"]_vars
\* Every attempt that ends is collected, and a 429's continuation runs again.
EveryExitCollected == ended ~> collects = 1
ContinuationRuns == cont ~> state = "running"
\* A stuck pi never holds its slot for good: the run stops holding it (cua-2879 held one for 20 h).
StuckFreesItsSlot == stuck ~> ~live
====
