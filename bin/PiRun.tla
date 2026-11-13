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
  spent

vars == <<state, cont, live, ended, killed, collects, finish, wake, reopened, by, why, fixes, limits, spent>>
FixAttempts == 2
ResumeCap == 2

Init ==
  /\ state = "absent" /\ cont = FALSE /\ live = FALSE /\ ended = FALSE /\ killed = FALSE /\ collects = 0
  /\ finish = FALSE /\ wake = "none" /\ reopened = FALSE /\ by = "direct" /\ why = "limit" /\ fixes = 0
  /\ limits = 0 /\ spent = FALSE

Lost == state = "running" /\ ~live

Queue ==
  /\ state = "absent" /\ state' = "queued"
  /\ UNCHANGED <<cont, live, ended, killed, collects, finish, wake, reopened, by, why, fixes, limits, spent>>

Start ==
  /\ state \in {"absent", "queued"} /\ ~cont
  /\ \E f \in BOOLEAN, b \in {"drain", "direct"} : finish' = f /\ by' = b
  /\ state' = "running" /\ live' = TRUE /\ reopened' = FALSE
  /\ UNCHANGED <<cont, ended, killed, collects, wake, why, fixes, limits, spent>>

Resume ==
  /\ state = "queued" /\ cont
  /\ state' = "running" /\ live' = TRUE /\ cont' = FALSE /\ reopened' = TRUE /\ by' = "drain"
  /\ UNCHANGED <<ended, killed, collects, finish, wake, why, fixes, limits, spent>>

Limit ==
  /\ state = "running" /\ live /\ limits < ResumeCap
  /\ state' = "queued" /\ cont' = TRUE /\ live' = FALSE /\ why' = "limit" /\ limits' = limits + 1
  /\ UNCHANGED <<ended, killed, collects, finish, wake, reopened, by, fixes, spent>>

LimitSpent ==
  /\ state = "running" /\ live /\ limits = ResumeCap
  /\ state' = "exited" /\ live' = FALSE /\ ended' = TRUE /\ wake' = "failed" /\ spent' = TRUE
  /\ UNCHANGED <<cont, killed, collects, finish, reopened, by, why, fixes, limits>>

Exit ==
  /\ state = "running" /\ live
  /\ \E ok \in BOOLEAN : wake' = IF ~ok THEN "failed" ELSE IF finish THEN "done" ELSE "none"
  /\ state' = "exited" /\ live' = FALSE /\ ended' = TRUE
  /\ UNCHANGED <<cont, killed, collects, finish, reopened, by, why, fixes, limits, spent>>

Kill ==
  /\ state = "running" /\ live
  /\ live' = FALSE /\ ended' = TRUE /\ killed' = TRUE
  /\ UNCHANGED <<state, cont, collects, finish, wake, reopened, by, why, fixes, limits, spent>>

Collect ==
  /\ state = "exited" \/ Lost
  /\ state' = "collected" /\ collects' = collects + 1 /\ wake' = IF Lost THEN "failed" ELSE wake
  /\ UNCHANGED <<cont, live, ended, killed, finish, reopened, by, why, fixes, limits, spent>>

CiReopen ==
  /\ state = "collected" /\ fixes < FixAttempts
  /\ state' = "queued" /\ cont' = TRUE /\ why' = "ci" /\ fixes' = fixes + 1
  /\ ended' = FALSE /\ killed' = FALSE /\ collects' = 0 /\ wake' = "none" /\ spent' = FALSE
  /\ UNCHANGED <<live, finish, reopened, by, limits>>

Done == state = "collected" /\ UNCHANGED vars

Next == Queue \/ Start \/ Resume \/ Limit \/ LimitSpent \/ Exit \/ Kill \/ Collect \/ CiReopen \/ Done
Spec == Init /\ [][Next]_vars /\ WF_vars(Start) /\ WF_vars(Resume) /\ WF_vars(Exit) /\ WF_vars(Collect)

CollectedAtMostOnce == collects <= 1 /\ (collects = 1 => ended)
LostIsCollectable == Lost => ENABLED Collect
LostWakesAtCollect == state = "collected" /\ killed => wake = "failed"
DoneOnlyWhenAsked == wake = "done" => finish
AskedExitWakes == state = "exited" /\ finish => wake # "none"
DefaultIsFailuresOnly == state = "exited" /\ ~finish => wake \in {"none", "failed"}
PauseIsSilent == cont => wake = "none" /\ ~ended /\ ~live
ResumeGoesThroughTheQueue == state = "running" /\ reopened => by = "drain"
CollectedIsFinal == [][state = "collected" => (state' = "collected" \/ (state' = "queued" /\ cont' /\ why' = "ci" /\ fixes' = fixes + 1))]_vars
ResumesCapped == limits <= ResumeCap
SpentLimitFails == spent => wake = "failed" /\ state \in {"exited", "collected"}
FixesCapped == fixes <= FixAttempts
CiReopenAfterCollect == [][(state' = "queued" /\ why' = "ci" /\ state # "queued") => state = "collected"]_vars
EveryExitCollected == ended ~> collects = 1
ContinuationRuns == cont ~> state = "running"
====
