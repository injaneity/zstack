---- MODULE PiRun ----
\* The run lifecycle bin/pi-run implements for one id. Each action is one step() call under the review dir's
\* flock, so actions are atomic. Two launchers race through Start, the supervisor through Exit, and the pickup
\* through Collect. Check: skills/statechart/scripts/statechart bin/PiRun.tla --view bin/PiRun.view.mts
\* tests/pirun-model.sh runs that check in CI and fails when bin/PiRun.mmd is not the chart it draws; tests/test_pi_run.py
\* fails when bin/pi-run's step() is not that chart.
EXTENDS Naturals

CONSTANT MaxAttempt

VARIABLES
  state,      \* run.json state: "absent" (no file), "running", "paused", "exited", "collected"
  attempt,    \* run.json attempt
  live,       \* live[a]: attempt a's supervisor or its process group still exists
  exited,     \* exited[a]: attempt a's pi has ended (exit or kill), so it has a result to take
  collects,   \* collects[a]: how many times the pickup took attempt a's result
  finish,     \* finish[a]: attempt a's brief has a `wake: finish` line (run.json wake_finish)
  wake        \* wake[a]: the wake attempt a's exit sent: "none", "done" or "failed" (run.json wake)

vars == <<state, attempt, live, exited, collects, finish, wake>>
A == 1..MaxAttempt

Init ==
  /\ state = "absent" /\ attempt = 0
  /\ live = [a \in A |-> FALSE] /\ exited = [a \in A |-> FALSE] /\ collects = [a \in A |-> 0]
  /\ finish = [a \in A |-> FALSE] /\ wake = [a \in A |-> "none"]

Lost == state = "running" /\ ~live[attempt]

\* A launcher (either of two racing ones: the flock makes them take turns) starts the next attempt, with or without
\* a `wake: finish` line in its brief.
Start ==
  /\ state \in {"absent", "collected"} /\ attempt < MaxAttempt
  /\ \E f \in BOOLEAN : finish' = [finish EXCEPT ![attempt + 1] = f]
  /\ state' = "running" /\ attempt' = attempt + 1
  /\ live' = [live EXCEPT ![attempt + 1] = TRUE]
  /\ UNCHANGED <<exited, collects, wake>>

\* pi ends with rc 0 (ok) or not, and the supervisor sends and records the wake step() picks, then writes the rc.
Exit ==
  /\ state = "running" /\ live[attempt]
  /\ \E ok \in BOOLEAN : wake' = [wake EXCEPT ![attempt] = IF ~ok THEN "failed" ELSE IF finish[attempt] THEN "done" ELSE "none"]
  /\ state' = "exited" /\ live' = [live EXCEPT ![attempt] = FALSE] /\ exited' = [exited EXCEPT ![attempt] = TRUE]
  /\ UNCHANGED <<attempt, collects, finish>>

\* pi stops on an upstream 429 (Claude Code or ChatGPT): the supervisor records a pause, sends no wake, and the attempt
\* has no result yet.
Limit ==
  /\ state = "running" /\ live[attempt]
  /\ state' = "paused" /\ live' = [live EXCEPT ![attempt] = FALSE]
  /\ UNCHANGED <<attempt, exited, collects, finish, wake>>

\* The hourly pass's `pi-run resume <id>` once resume_at has passed: the same attempt continues its pi session.
Resume ==
  /\ state = "paused"
  /\ state' = "running" /\ live' = [live EXCEPT ![attempt] = TRUE]
  /\ UNCHANGED <<attempt, exited, collects, finish, wake>>

\* KILL or OOM: the supervisor and pi die and run.json still says running.
Kill ==
  /\ state = "running" /\ live[attempt]
  /\ live' = [live EXCEPT ![attempt] = FALSE] /\ exited' = [exited EXCEPT ![attempt] = TRUE]
  /\ UNCHANGED <<state, attempt, collects, finish, wake>>

\* The pickup's `pi-run collect <id>`: takes an exited or lost run's result.
Collect ==
  /\ state = "exited" \/ Lost
  /\ state' = "collected" /\ collects' = [collects EXCEPT ![attempt] = @ + 1]
  /\ UNCHANGED <<attempt, live, exited, finish, wake>>

Done == state = "collected" /\ attempt = MaxAttempt /\ UNCHANGED vars

Next == Start \/ Exit \/ Limit \/ Resume \/ Kill \/ Collect \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Exit) /\ WF_vars(Collect) /\ WF_vars(Resume)

\* At most one attempt of an id is alive at a time.
OneLiveRun == \A a, b \in A : live[a] /\ live[b] => a = b
\* A result is taken at most once, and only after its pi ended.
CollectedAtMostOnce == \A a \in A : collects[a] <= 1 /\ (collects[a] = 1 => exited[a])
\* A relaunch never drops a result: every earlier attempt was collected before a newer one started.
RelaunchKeepsResults == \A a \in A : a < attempt => collects[a] = 1
\* A killed run is told apart from a live one: it is collectable, never refused as "already running".
LostIsCollectable == Lost => ENABLED Collect
\* A done wake goes only to an attempt that asked for it; a run that did not ask never wakes on success.
DoneOnlyWhenAsked == \A a \in A : wake[a] = "done" => finish[a]
\* A run that asked and exited sent a wake, done or failed: a monitored run is never silent at its exit.
AskedExitWakes == state = "exited" /\ finish[attempt] => wake[attempt] # "none"
\* A run that did not ask wakes only on failure, as before.
DefaultIsFailuresOnly == state = "exited" /\ ~finish[attempt] => wake[attempt] \in {"none", "failed"}
\* A pause is never a failure: it sends no wake and leaves nothing to collect, and its pi is not running.
PauseIsSilent == state = "paused" => wake[attempt] = "none" /\ ~exited[attempt] /\ ~live[attempt]
\* Every attempt that ends is collected.
EveryExitCollected == \A a \in A : exited[a] ~> collects[a] = 1
====
