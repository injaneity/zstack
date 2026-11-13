---- MODULE Zstack ----
EXTENDS Naturals

VARIABLES
  phase,
  draft,
  lost,
  collected,
  woke,
  reopened,
  cont,
  due,
  limited,
  slot,
  drained,
  ci,
  reran,
  tier,
  pre,
  held,
  conflict,
  byJob,
  by,
  alerted,
  logged,
  atQueue,
  atMerge,
  fixing,
  fixes,
  limits

vars == <<phase, draft, lost, collected, woke, reopened, cont, due, limited, slot, drained, ci, reran, tier, pre, held, conflict, byJob, by,
          alerted, logged, atQueue, atMerge, fixing, fixes, limits>>
RunVars == <<lost, collected, woke, reopened, cont, due, limited, slot, drained, fixing, fixes, limits>>
FixAttempts == 2
ResumeCap == 1
PRVars == <<draft, ci, reran, tier, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
Tiers == {"light", "deep"}
NoFacts == [green |-> FALSE, held |-> FALSE, preOk |-> FALSE, tier |-> "none", draft |-> FALSE]
PRopen == {"open", "inqueue"}

Init ==
  /\ phase = "queued" /\ draft = FALSE /\ lost = FALSE /\ collected = FALSE /\ woke = FALSE /\ reopened = FALSE
  /\ cont = FALSE /\ due = FALSE /\ limited = FALSE /\ slot = FALSE /\ drained = FALSE
  /\ ci = "pending" /\ reran = FALSE /\ tier = "none" /\ pre = FALSE /\ held = FALSE /\ conflict = FALSE
  /\ byJob = FALSE /\ by = "none" /\ alerted = FALSE /\ logged = FALSE /\ atQueue = NoFacts /\ atMerge = NoFacts
  /\ fixing = FALSE /\ fixes = 0 /\ limits = 0

Drain == phase = "queued" /\ (cont => due)
  /\ phase' = "running" /\ slot' = TRUE /\ drained' = TRUE /\ reopened' = cont /\ cont' = FALSE
  /\ UNCHANGED <<lost, collected, woke, due, limited, fixing, fixes, limits>> /\ UNCHANGED PRVars
StartNow == phase = "queued" /\ ~cont
  /\ phase' = "running" /\ slot' = TRUE /\ drained' = FALSE /\ reopened' = FALSE /\ cont' = FALSE
  /\ UNCHANGED <<lost, collected, woke, due, limited, fixing, fixes, limits>> /\ UNCHANGED PRVars
Limit == phase = "running" /\ limits < ResumeCap
  /\ phase' = "queued" /\ cont' = TRUE /\ due' = FALSE /\ limited' = TRUE /\ slot' = FALSE /\ limits' = limits + 1
  /\ UNCHANGED <<lost, collected, woke, reopened, drained, fixing, fixes>> /\ UNCHANGED PRVars
LimitSpent == phase = "running" /\ limits = ResumeCap /\ phase' = "failed" /\ woke' = TRUE /\ slot' = FALSE
  /\ UNCHANGED <<lost, collected, reopened, cont, due, limited, drained, fixing, fixes, limits>> /\ UNCHANGED PRVars
ResumeAtPasses == phase = "queued" /\ cont /\ ~due /\ due' = TRUE
  /\ UNCHANGED <<phase, lost, collected, woke, reopened, cont, limited, slot, drained, fixing, fixes, limits>> /\ UNCHANGED PRVars
ExitFailed == phase = "running" /\ phase' = "failed" /\ woke' = TRUE /\ slot' = FALSE
  /\ UNCHANGED <<lost, collected, reopened, cont, due, limited, drained, fixing, fixes, limits>> /\ UNCHANGED PRVars
Killed == phase = "running" /\ phase' = "failed" /\ lost' = TRUE /\ woke' = FALSE /\ slot' = FALSE
  /\ UNCHANGED <<collected, reopened, cont, due, limited, drained, fixing, fixes, limits>> /\ UNCHANGED PRVars
Collect == phase = "failed" /\ ~collected /\ collected' = TRUE /\ woke' = TRUE
  /\ UNCHANGED <<phase, lost, reopened, cont, due, limited, slot, drained, fixing, fixes, limits>> /\ UNCHANGED PRVars
Retry == phase = "failed" /\ collected
  /\ phase' = "queued" /\ lost' = FALSE /\ collected' = FALSE /\ woke' = FALSE
  /\ reopened' = FALSE /\ cont' = FALSE /\ due' = FALSE /\ limited' = FALSE /\ slot' = FALSE /\ drained' = FALSE
  /\ limits' = 0 /\ UNCHANGED <<fixing, fixes>> /\ UNCHANGED PRVars
ExitOk == phase = "running" /\ slot' = FALSE
  /\ \/ phase' = "done" /\ UNCHANGED PRVars
     \/ /\ phase' = "open" /\ draft' \in BOOLEAN /\ tier' \in {"none"} \cup Tiers
        /\ UNCHANGED <<ci, reran, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED <<lost, collected, woke, reopened, cont, due, limited, drained, fixing, fixes, limits>>

CiReopen == phase = "open" /\ ~draft /\ ci = "red" /\ reran /\ ~held /\ ~fixing /\ fixes < FixAttempts
  /\ fixing' = TRUE /\ fixes' = fixes + 1 /\ slot' = TRUE /\ reopened' = TRUE /\ drained' = TRUE
  /\ UNCHANGED <<phase, lost, collected, woke, cont, due, limited, limits>> /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
ThreadFixes == fixing
  /\ \/ phase \in PRopen /\ ci' = "pending" /\ reran' = FALSE /\ pre' = FALSE /\ conflict' = FALSE /\ byJob' = FALSE
        /\ phase' = "open"
     \/ UNCHANGED <<phase, ci, reran, pre, conflict, byJob>>
  /\ fixing' = FALSE /\ slot' = FALSE /\ reopened' = FALSE
  /\ UNCHANGED <<draft, tier, held, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED <<lost, collected, woke, cont, due, limited, drained, fixes, limits>>

CiFinishes == phase \in PRopen /\ ci = "pending" /\ ci' \in {"green", "red"}
  /\ UNCHANGED <<phase, draft, reran, tier, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
Push == phase \in PRopen
  /\ ci' = "pending" /\ reran' = FALSE /\ pre' = FALSE /\ conflict' = FALSE
  /\ phase' = (IF phase = "inqueue" THEN "open" ELSE phase) /\ byJob' = FALSE
  /\ UNCHANGED <<draft, tier, held, by, alerted, logged, atQueue, atMerge>> /\ UNCHANGED RunVars
DeclareTier == phase = "open" /\ tier = "none" /\ tier' \in Tiers
  /\ UNCHANGED <<phase, draft, ci, reran, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
MainMoves == phase = "open" /\ ~conflict /\ conflict' = TRUE
  /\ UNCHANGED <<phase, draft, ci, reran, tier, pre, held, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
Ready == phase \in PRopen /\ draft /\ draft' = FALSE
  /\ UNCHANGED <<phase, ci, reran, tier, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
ZaneHolds == phase \in PRopen /\ held' = ~held
  /\ UNCHANGED <<phase, draft, ci, reran, tier, pre, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
ZanePreapproves == phase \in PRopen /\ tier = "deep" /\ ~pre /\ pre' = TRUE
  /\ UNCHANGED <<phase, draft, ci, reran, tier, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars

Gate == ci = "green" /\ ~held /\ tier \in Tiers /\ (tier = "deep" => pre)
Facts == [green |-> ci = "green", held |-> held, preOk |-> (tier # "deep" \/ pre), tier |-> tier,
          draft |-> draft]
JobEnqueues == phase = "open" /\ ~draft /\ Gate /\ ~conflict
  /\ phase' = "inqueue" /\ byJob' = TRUE /\ atQueue' = Facts
  /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, by, alerted, logged, atMerge>> /\ UNCHANGED RunVars
JobReruns == phase = "open" /\ ci = "red" /\ ~reran /\ ci' = "pending" /\ reran' = TRUE
  /\ UNCHANGED <<phase, draft, tier, pre, held, conflict, byJob, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
JobDequeues == phase = "inqueue" /\ byJob /\ ~Gate /\ phase' = "open" /\ byJob' = FALSE
  /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
JobLogsMerged == phase = "merged" /\ by = "job" /\ ~logged /\ logged' = TRUE
  /\ UNCHANGED <<phase, draft, ci, reran, tier, pre, held, conflict, byJob, by, alerted, atQueue, atMerge>>
  /\ UNCHANGED RunVars
JobAlerts == phase = "merged" /\ by = "other" /\ ~alerted /\ alerted' = TRUE
  /\ UNCHANGED <<phase, draft, ci, reran, tier, pre, held, conflict, byJob, by, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars

OtherEnqueues == phase = "open" /\ ~draft /\ ci = "green" /\ ~conflict /\ phase' = "inqueue" /\ byJob' = FALSE
  /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
QueueMerges == phase = "inqueue" /\ phase' = "merged" /\ by' = (IF byJob THEN "job" ELSE "other") /\ atMerge' = Facts
  /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, byJob, alerted, logged, atQueue>> /\ UNCHANGED RunVars
QueueDrops == phase = "inqueue" /\ phase' = "open" /\ byJob' = FALSE
  /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, by, alerted, logged, atQueue, atMerge>>
  /\ UNCHANGED RunVars
OtherMerges == phase = "open" /\ ~draft /\ ~conflict /\ phase' = "merged" /\ by' = "other" /\ atMerge' = Facts
  /\ UNCHANGED <<draft, ci, reran, tier, pre, held, conflict, byJob, alerted, logged, atQueue>> /\ UNCHANGED RunVars

Ends == (phase \in {"done", "merged"} \/ (phase = "failed" /\ collected)) /\ ~fixing /\ UNCHANGED vars

Next == Drain \/ StartNow \/ Limit \/ LimitSpent \/ ResumeAtPasses \/ ExitFailed \/ Killed \/ Collect \/ Retry \/ ExitOk
  \/ CiFinishes \/ Push \/ DeclareTier \/ MainMoves \/ Ready \/ ZaneHolds \/ ZanePreapproves
  \/ JobEnqueues \/ JobReruns \/ JobDequeues \/ JobLogsMerged \/ JobAlerts
  \/ OtherEnqueues \/ QueueMerges \/ QueueDrops \/ OtherMerges \/ CiReopen \/ ThreadFixes \/ Ends
Spec == Init /\ [][Next]_vars /\ WF_vars(Drain) /\ WF_vars(ResumeAtPasses) /\ WF_vars(Collect) /\ WF_vars(JobLogsMerged) /\ WF_vars(JobAlerts) /\ WF_vars(JobDequeues)
  /\ WF_vars(CiReopen) /\ WF_vars(ThreadFixes)

JobMerged == phase = "merged" /\ by = "job"
MergedOnGate == JobMerged => atQueue.green /\ ~atQueue.held /\ atQueue.tier \in Tiers /\ atQueue.preOk
DeepNeedsPreapproveAtHead == JobMerged => atMerge.preOk /\ atMerge.green
DraftIsNeverMerged == phase = "merged" => ~atMerge.draft /\ ~(JobMerged /\ atQueue.draft)
TierIsFromEnum == JobMerged => atQueue.tier \in Tiers
MergedEventMeansMerged == logged => JobMerged
OffGateLeavesQueue == [](phase = "inqueue" /\ byJob /\ ~Gate => <>(phase # "inqueue" \/ Gate))
RetryIsFresh == phase = "running" /\ reopened => limited
SlotOnlyWhileRunning == slot <=> (phase = "running" \/ fixing)
ResumeGoesThroughTheQueue == phase = "running" /\ reopened => drained /\ due
ContinuationRuns == [](cont => <>(phase = "running"))
PauseIsSilent == cont => ~woke /\ ~collected
CollectedFailureWoke == phase = "failed" /\ collected => woke
EveryFailureWakes == [](phase = "failed" => <>(woke \/ phase # "failed"))
FailedExitWoke == phase = "failed" /\ ~lost => woke
RedPrGoesBackToItsThread == [](phase = "open" /\ ci = "red" /\ reran /\ ~held /\ ~draft /\ fixes < FixAttempts
                               => <>(fixing \/ ci # "red" \/ phase # "open" \/ held \/ draft))
ResumesCapped == limits <= ResumeCap
FixesCapped == fixes <= FixAttempts
ReopenedThreadEnds == [](fixing => <>~fixing)
EveryOtherMergeWakes == [](by = "other" => <>alerted)
EveryJobMergeLogged == [](JobMerged => <>logged)

HeldIsNeverMergedStrict == JobMerged => ~atMerge.held

ChartView == <<phase, draft, lost, collected, woke, phase = "running" /\ reopened, cont, cont /\ due, ci, reran, tier, pre, held, conflict,
               byJob, by, alerted, logged, fixing>>
====
