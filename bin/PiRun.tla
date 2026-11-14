---- MODULE PiRun ----
EXTENDS Naturals

VARIABLES
  state,
  cont,
  live,
  ended,
  killed,
  collects,
  finish,
  wake,
  reopened,
  by,
  why,
  fixes,
  limits,
  spent,
  stuck,
  stopped,
  daemon

vars == <<state, cont, live, ended, killed, collects, finish, wake, reopened, by, why, fixes, limits, spent, stuck, stopped, daemon>>
FixAttempts == 2
ResumeCap == 2

Init ==
  /\ state = "absent" /\ cont = FALSE /\ live = FALSE /\ ended = FALSE /\ killed = FALSE /\ collects = 0
  /\ finish = FALSE /\ wake = "none" /\ reopened = FALSE /\ by = "direct" /\ why = "limit" /\ fixes = 0
  /\ limits = 0 /\ spent = FALSE /\ stuck = FALSE /\ stopped = FALSE /\ daemon = FALSE

Lost == state = "running" /\ ~live

Queue ==
  /\ state = "absent" /\ state' = "queued"
  /\ UNCHANGED <<cont, live, ended, killed, collects, finish, wake, reopened, by, why, fixes, limits, spent, stuck, stopped, daemon>>

Start ==
  /\ state \in {"absent", "queued"} /\ ~cont
  /\ \E f \in BOOLEAN, b \in {"drain", "direct"} : finish' = f /\ by' = b
  /\ state' = "running" /\ live' = TRUE /\ reopened' = FALSE /\ stuck' = FALSE
  /\ UNCHANGED <<cont, ended, killed, collects, wake, why, fixes, limits, spent, stopped, daemon>>

Resume ==
  /\ state = "queued" /\ cont
  /\ state' = "running" /\ live' = TRUE /\ cont' = FALSE /\ reopened' = TRUE /\ by' = "drain" /\ stuck' = FALSE
  /\ UNCHANGED <<ended, killed, collects, finish, wake, why, fixes, limits, spent, stopped, daemon>>

Limit ==
  /\ state = "running" /\ live /\ ~stuck /\ ~stopped /\ limits < ResumeCap
  /\ state' = "queued" /\ cont' = TRUE /\ live' = FALSE /\ why' = "limit" /\ limits' = limits + 1
  /\ UNCHANGED <<ended, killed, collects, finish, wake, reopened, by, fixes, spent, stuck, stopped, daemon>>

LimitSpent ==
  /\ state = "running" /\ live /\ ~stuck /\ ~stopped /\ limits = ResumeCap
  /\ state' = "exited" /\ live' = FALSE /\ ended' = TRUE /\ wake' = "failed" /\ spent' = TRUE /\ daemon' = FALSE
  /\ UNCHANGED <<cont, killed, collects, finish, reopened, by, why, fixes, limits, stuck, stopped>>

Exit ==
  /\ state = "running" /\ live /\ ~stuck
  /\ \E ok \in BOOLEAN : wake' = IF stopped THEN "none" ELSE IF ~ok THEN "failed" ELSE IF finish THEN "done" ELSE "none"
  /\ state' = "exited" /\ live' = FALSE /\ ended' = TRUE /\ daemon' = FALSE
  /\ UNCHANGED <<cont, killed, collects, finish, reopened, by, why, fixes, limits, spent, stuck, stopped>>

Stop ==
  /\ state = "running" /\ live /\ ~stopped /\ stopped' = TRUE
  /\ UNCHANGED <<state, cont, live, ended, killed, collects, finish, wake, reopened, by, why, fixes, limits, spent, stuck, daemon>>

Stall ==
  /\ state = "running" /\ live /\ ~stuck /\ stuck' = TRUE
  /\ UNCHANGED <<state, cont, live, ended, killed, collects, finish, wake, reopened, by, why, fixes, limits, spent, stopped, daemon>>

IdleStop ==
  /\ state = "running" /\ live /\ stuck
  /\ state' = "exited" /\ live' = FALSE /\ ended' = TRUE /\ wake' = (IF stopped THEN "none" ELSE "failed") /\ stuck' = FALSE /\ daemon' = FALSE
  /\ UNCHANGED <<cont, killed, collects, finish, reopened, by, why, fixes, limits, spent, stopped>>

Kill ==
  /\ state = "running" /\ live
  /\ live' = FALSE /\ ended' = TRUE /\ killed' = TRUE /\ stuck' = FALSE
  /\ UNCHANGED <<state, cont, collects, finish, wake, reopened, by, why, fixes, limits, spent, stopped, daemon>>

Collect ==
  /\ state = "exited" \/ Lost
  /\ state' = "collected" /\ collects' = collects + 1 /\ wake' = (IF Lost /\ ~stopped THEN "failed" ELSE IF Lost THEN "none" ELSE wake) /\ daemon' = FALSE
  /\ UNCHANGED <<cont, live, ended, killed, finish, reopened, by, why, fixes, limits, spent, stuck, stopped>>

CiReopen ==
  /\ state = "collected" /\ fixes < FixAttempts
  /\ state' = "queued" /\ cont' = TRUE /\ why' = "ci" /\ fixes' = fixes + 1
  /\ ended' = FALSE /\ killed' = FALSE /\ collects' = 0 /\ wake' = "none" /\ spent' = FALSE /\ stopped' = FALSE
  /\ UNCHANGED <<live, finish, reopened, by, limits, stuck, daemon>>

Serve ==
  /\ state = "running" /\ live /\ ~daemon /\ daemon' = TRUE
  /\ UNCHANGED <<state, cont, live, ended, killed, collects, finish, wake, reopened, by, why, fixes, limits, spent, stuck, stopped>>

Done == state = "collected" /\ UNCHANGED vars

Next == Queue \/ Start \/ Resume \/ Limit \/ LimitSpent \/ Exit \/ Stop \/ Stall \/ IdleStop \/ Kill \/ Collect \/ CiReopen \/ Serve \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Start) /\ WF_vars(Resume) /\ WF_vars(Exit) /\ WF_vars(IdleStop) /\ WF_vars(Collect)

CollectedAtMostOnce == collects <= 1 /\ (collects = 1 => ended)
LostIsCollectable == Lost => ENABLED Collect
LostWakesAtCollect == state = "collected" /\ killed /\ ~stopped => wake = "failed"
DoneOnlyWhenAsked == wake = "done" => finish
AskedExitWakes == state = "exited" /\ finish /\ ~stopped => wake # "none"
DefaultIsFailuresOnly == state = "exited" /\ ~finish => wake \in {"none", "failed"}
StopIsSilent == stopped => wake = "none"
PauseIsSilent == cont => wake = "none" /\ ~ended /\ ~live
ResumeGoesThroughTheQueue == state = "running" /\ reopened => by = "drain"
CollectedIsFinal == [][state = "collected" => (state' = "collected" \/ (state' = "queued" /\ cont' /\ why' = "ci" /\ fixes' = fixes + 1))]_vars
ResumesCapped == limits <= ResumeCap
SpentLimitFails == spent => wake = "failed" /\ state \in {"exited", "collected"}
FixesCapped == fixes <= FixAttempts
CiReopenAfterCollect == [][(state' = "queued" /\ why' = "ci" /\ state # "queued") => state = "collected"]_vars
EveryExitCollected == ended ~> collects = 1
ContinuationRuns == cont ~> state = "running"
DaemonEndsWithTheRun == state \in {"exited", "collected"} => ~daemon
StuckFreesItsSlot == stuck ~> ~live
ChartSteps == daemon' = daemon
====
