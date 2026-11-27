---- MODULE Queue ----
EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Machines, Items, Cap, Accepts, Review, Pre, Prio, NoTree, Garbles, Paged, Queues, Retries, Resumes, Hooked, Fills, Idles

VARIABLES q, held, pre, unread, up, pr, status, stale, merged, inq, gone, lost, dropped, requeued, retried, broken, red, badMerge, okAtMerge, thread, pending, startedHeld, needless, jumped, crowded, endedUnverified, twoThreads, diedCleared, unmarkedCleared, refusedRerun, full, launchedFull, idle, cleanedLive

vars == <<q, held, pre, unread, up, pr, status, stale, merged, inq, gone, lost, dropped, requeued, retried, broken, red, badMerge, okAtMerge, thread, pending, startedHeld, needless, jumped, crowded, endedUnverified, twoThreads, diedCleared, unmarkedCleared, refusedRerun, full, launchedFull, idle, cleanedLive>>
States == {"none", "running", "waiting", "done"}

TypeOK ==
  /\ q \in [Machines -> [Items -> States]] /\ Paged \subseteq Items
  /\ held \subseteq Items /\ pre \subseteq Items /\ unread \subseteq Garbles /\ up \in BOOLEAN /\ pr \subseteq Items /\ stale \subseteq Items /\ merged \subseteq Items
  /\ Queues \subseteq Items /\ Retries \subseteq Queues /\ Resumes \subseteq Items /\ Hooked \subseteq Items /\ Fills \subseteq Machines /\ inq \subseteq Queues /\ gone \subseteq Queues /\ lost \subseteq Queues /\ dropped \subseteq Queues /\ requeued \in BOOLEAN /\ retried \subseteq Queues /\ broken \subseteq Items /\ red \subseteq Items
  /\ status \in [Items -> {"none", "auto", "review"}]
  /\ badMerge \in BOOLEAN /\ okAtMerge \in [Items -> BOOLEAN]
  /\ thread \subseteq Items /\ pending \subseteq Items /\ twoThreads \in BOOLEAN /\ diedCleared \in BOOLEAN /\ unmarkedCleared \in BOOLEAN /\ refusedRerun \in BOOLEAN
  /\ full \subseteq Machines /\ launchedFull \in BOOLEAN /\ idle \subseteq Items /\ cleanedLive \in BOOLEAN
  /\ startedHeld \in BOOLEAN /\ needless \in BOOLEAN /\ jumped \in BOOLEAN /\ crowded \in BOOLEAN /\ endedUnverified \in BOOLEAN

Init ==
  /\ q = [m \in Machines |-> [i \in Items |-> "none"]]
  /\ held = {} /\ pre = Pre /\ unread = {} /\ up = TRUE /\ pr = {} /\ stale = {} /\ merged = {}
  /\ status = [i \in Items |-> "none"]
  /\ inq = {} /\ gone = {} /\ lost = {} /\ dropped = {} /\ requeued = FALSE /\ retried = {} /\ broken = {} /\ red = {}
  /\ badMerge = FALSE /\ okAtMerge = [i \in Items |-> FALSE]
  /\ thread = {} /\ pending = {} /\ twoThreads = FALSE /\ diedCleared = FALSE /\ unmarkedCleared = FALSE /\ refusedRerun = FALSE
  /\ full = {} /\ launchedFull = FALSE /\ idle = {} /\ cleanedLive = FALSE
  /\ startedHeld = FALSE /\ needless = FALSE /\ jumped = FALSE /\ crowded = FALSE /\ endedUnverified = FALSE

Set(m, i, s) == q' = [q EXCEPT ![m][i] = s]
Busy(m) == Cardinality({j \in Items : q[m][j] = "running"})
drops == <<gone, lost, dropped, requeued, retried, broken, red>>
work == <<pre, unread, up, full, pr, status, stale, merged, inq, drops, badMerge, okAtMerge>>
history == <<startedHeld, needless, jumped, crowded, endedUnverified, thread, pending, twoThreads, diedCleared, unmarkedCleared, refusedRerun, launchedFull, idle, cleanedLive>>

Ready(m, i) == q[m][i] = "none" /\ i \in Accepts[m] /\ i \notin held
Stands(i) == i \in dropped /\ (i \notin pre \/ i \notin Retries \/ i \in retried)
NeedsPerson(i) == (status[i] = "review" \/ i \in Review) /\ i \notin pre
Needed(i) == i \notin merged /\ i \notin inq /\ i \notin held /\ ~NeedsPerson(i) /\ i \notin Review
  /\ (status[i] = "none" \/ Stands(i) \/ i \in broken)
First(m, i) == \A j \in Items : Ready(m, j) => Prio[j] <= Prio[i]
Launch(m, i) == q[m][i] = "none" /\ i \in Accepts[m] /\ i \notin held /\ First(m, i)
  /\ Busy(m) < Cap
  /\ m \notin full
  /\ ~(i \in Resumes /\ i \in pr)
  /\ Set(m, i, "running") /\ pending' = pending \cup {i} /\ twoThreads' = (twoThreads \/ i \in pending \/ i \in thread)
  /\ jumped' = (jumped \/ ~First(m, i)) /\ startedHeld' = (startedHeld \/ i \in held) /\ crowded' = (crowded \/ Busy(m) >= Cap) /\ launchedFull' = (launchedFull \/ m \in full)
  /\ UNCHANGED <<held, work, needless, endedUnverified, thread, diedCleared, unmarkedCleared, refusedRerun, idle, cleanedLive>>
Relaunch(m, i) == q[m][i] = "none" /\ i \in Resumes /\ i \in pr /\ i \notin held /\ i \in Accepts[m] /\ First(m, i) /\ Busy(m) < Cap /\ m \notin full
  /\ Needed(i)
  /\ Set(m, i, "running") /\ twoThreads' = (twoThreads \/ i \in pending \/ i \in thread) /\ pending' = pending \cup {i}
  /\ jumped' = (jumped \/ ~First(m, i)) /\ startedHeld' = (startedHeld \/ i \in held)
  /\ needless' = (needless \/ ~Needed(i)) /\ crowded' = (crowded \/ Busy(m) >= Cap) /\ launchedFull' = (launchedFull \/ m \in full)
  /\ UNCHANGED <<held, work, endedUnverified, thread, diedCleared, unmarkedCleared, refusedRerun, idle, cleanedLive>>

Takes(m, i) == q[m][i] \in {"running", "waiting"}
Push(m, i) == Takes(m, i) /\ i \notin NoTree /\ i \notin merged
  /\ pr' = pr \cup {i} /\ status' = [status EXCEPT ![i] = "none"] /\ stale' = stale \cup {i}
  /\ inq' = inq \ {i} /\ gone' = gone \ {i} /\ dropped' = dropped \ {i} /\ retried' = retried \ {i} /\ broken' = broken \ {i} /\ red' = red \ {i}
  /\ UNCHANGED <<q, held, pre, unread, up, full, merged, lost, requeued, badMerge, okAtMerge, history>>
Verdict(m, i, v) == Takes(m, i) /\ i \in pr /\ i \notin merged
  /\ status' = [status EXCEPT ![i] = v] /\ stale' = stale \ {i}
  /\ UNCHANGED <<q, held, pre, unread, up, full, pr, merged, inq, drops, badMerge, okAtMerge, history>>

Open(i) == status[i] # "none" /\ ~NeedsPerson(i) /\ i \notin held /\ i \notin broken
  /\ i \notin red
MayMerge(i, who) == status[i] # "none" /\ i \notin stale /\ ~NeedsPerson(i) /\ i \notin held /\ ~Stands(i) /\ i \notin broken /\ i \notin red /\ (who = "pass" => i \notin Review)
Merge(i, who) == i \in pr /\ i \notin merged /\ merged' = merged \cup {i} /\ badMerge' = (badMerge \/ (who # "human" /\ ~MayMerge(i, who)))
  /\ inq' = inq \ {i}
  /\ unread' = unread \ {i} /\ UNCHANGED <<q, held, pre, up, full, pr, status, stale, drops, okAtMerge, history>>
Enqueue(i) == inq' = inq \cup {i} /\ okAtMerge' = [okAtMerge EXCEPT ![i] = MayMerge(i, "pass")] /\ requeued' = (requeued \/ i \in gone /\ (i \notin Retries \/ i \in retried))
  /\ retried' = retried \cup ({i} \cap gone) /\ gone' = gone \ {i} /\ dropped' = dropped \ {i}
  /\ unread' = unread \ {i} /\ UNCHANGED <<q, held, pre, up, full, pr, status, stale, merged, lost, broken, red, badMerge, history>>
MergeOrQueue(i) == IF i \in Queues THEN Enqueue(i) ELSE Merge(i, "pass")
ThreadMerge(m, i) == Takes(m, i) /\ Open(i) /\ ~Stands(i) /\ Merge(i, "thread")
PassReady(m, i) == q[m][i] = "waiting" /\ i \in pr /\ i \notin merged /\ Open(i) /\ i \notin Review /\ i \notin unread /\ i \notin inq /\ i \notin lost /\ ~Stands(i)
PassMerge(m, i) == PassReady(m, i) /\ MergeOrQueue(i)
Dequeue(m, i) == q[m][i] = "waiting" /\ i \in inq /\ i \in held /\ inq' = inq \ {i} /\ UNCHANGED <<q, held, pre, unread, up, full, pr, status, stale, merged, drops, badMerge, okAtMerge, history>>
Land(i) == i \in inq /\ i \notin merged /\ merged' = merged \cup {i} /\ badMerge' = (badMerge \/ ~okAtMerge[i]) /\ inq' = inq \ {i}
  /\ UNCHANGED <<q, held, pre, unread, up, full, pr, status, stale, drops, okAtMerge, history>>
Drop(i) == i \in inq /\ inq' = inq \ {i} /\ gone' = gone \cup {i} /\ lost' = lost \cup {i}
  /\ UNCHANGED <<q, held, pre, unread, up, full, pr, status, stale, merged, dropped, requeued, retried, broken, red, badMerge, okAtMerge, history>>
Observe(m, i) == q[m][i] = "waiting" /\ i \in lost /\ lost' = lost \ {i} /\ dropped' = dropped \cup ({i} \cap gone)
  /\ UNCHANGED <<q, held, pre, unread, up, full, pr, status, stale, merged, inq, gone, requeued, retried, broken, red, badMerge, okAtMerge, history>>
HumanMerge(i) == (\E m \in Machines : Takes(m, i)) /\ Merge(i, "human")

Unverified(i) == i \in pr /\ i \notin merged /\ i \in stale
Finished(i) == i \in merged \/ i \notin pr
Refusals(i) == IF i \in Hooked THEN BOOLEAN ELSE {FALSE}
Leave(m, i, marked, refused) ==
  /\ IF i \notin pr /\ refused THEN Set(m, i, "waiting")
     ELSE IF i \notin pr /\ ~marked THEN Set(m, i, "none")
     ELSE \/ Finished(i) /\ Set(m, i, "done")
          \/ i \notin merged /\ Set(m, i, "waiting")
  /\ unmarkedCleared' = (unmarkedCleared \/ q'[m][i] = "done" /\ i \notin pr /\ ~marked)
  /\ refusedRerun' = (refusedRerun \/ refused /\ i \notin pr /\ q'[m][i] = "none")
  /\ i \in thread /\ thread' = thread \ {i}
  /\ UNCHANGED <<held, work, startedHeld, needless, jumped, crowded, pending, twoThreads, launchedFull, idle, cleanedLive>>
TurnEnd(m, i) == q[m][i] = "running" /\ (up \/ i \in NoTree) /\ ~Unverified(i)
  /\ endedUnverified' = (endedUnverified \/ Unverified(i)) /\ (\E c \in BOOLEAN, r \in Refusals(i) : Leave(m, i, c, r)) /\ UNCHANGED diedCleared
Freed(m, i) == q[m][i] = "running" /\ UNCHANGED <<endedUnverified, diedCleared>> /\ \E c \in BOOLEAN, r \in Refusals(i) : Leave(m, i, c, r)
Die(m, i) == q[m][i] = "running" /\ i \in thread /\ UNCHANGED endedUnverified
  /\ (IF i \in pr THEN Leave(m, i, TRUE, FALSE)
      ELSE \E r \in Refusals(i) : Set(m, i, IF r THEN "waiting" ELSE "none") /\ thread' = thread \ {i}
        /\ refusedRerun' = (refusedRerun \/ r /\ q'[m][i] = "none")
        /\ UNCHANGED <<held, work, startedHeld, needless, jumped, crowded, pending, twoThreads, unmarkedCleared, launchedFull, idle, cleanedLive>>)
  /\ diedCleared' = (diedCleared \/ q'[m][i] = "done" /\ i \notin pr)
Idle(m, i) == q[m][i] = "running" /\ i \in Idles /\ i \in thread /\ Set(m, i, "waiting") /\ idle' = idle \cup {i}
  /\ cleanedLive' = (cleanedLive \/ i \in pr /\ i \notin idle')
  /\ UNCHANGED <<held, work, startedHeld, needless, jumped, crowded, endedUnverified, thread, pending, twoThreads, diedCleared, unmarkedCleared, refusedRerun, launchedFull>>
IdleEnds(i) == i \in idle /\ i \in thread /\ thread' = thread \ {i}
  /\ UNCHANGED <<q, held, work, startedHeld, needless, jumped, crowded, endedUnverified, pending, twoThreads, diedCleared, unmarkedCleared, refusedRerun, launchedFull, idle, cleanedLive>>
IdleOver(m, i) == q[m][i] = "waiting" /\ i \in idle /\ i \notin thread
Rerun(m, i) == IdleOver(m, i) /\ idle' = idle \ {i} /\ (IF i \in pr THEN UNCHANGED q ELSE Set(m, i, "none"))
  /\ UNCHANGED <<held, work, startedHeld, needless, jumped, crowded, endedUnverified, thread, pending, twoThreads, diedCleared, unmarkedCleared, refusedRerun, launchedFull, cleanedLive>>
Started(i) == i \in pending /\ pending' = pending \ {i} /\ thread' = thread \cup {i}
  /\ UNCHANGED <<q, held, work, startedHeld, needless, jumped, crowded, endedUnverified, twoThreads, diedCleared, unmarkedCleared, refusedRerun, launchedFull, idle, cleanedLive>>
Lost(i) == i \in pending /\ pending' = pending \ {i}
  /\ UNCHANGED <<q, held, work, startedHeld, needless, jumped, crowded, endedUnverified, thread, twoThreads, diedCleared, unmarkedCleared, refusedRerun, launchedFull, idle, cleanedLive>>
Abort(m, i) == q[m][i] = "running" /\ i \notin thread /\ i \notin pending /\ Set(m, i, "none") /\ UNCHANGED <<held, work, history>>
Settle(m, i) == q[m][i] = "waiting" /\ i \in merged /\ i \notin idle /\ Set(m, i, "done") /\ cleanedLive' = (cleanedLive \/ i \in idle /\ i \in thread)
  /\ UNCHANGED <<held, work, startedHeld, needless, jumped, crowded, endedUnverified, thread, pending, twoThreads, diedCleared, unmarkedCleared, refusedRerun, launchedFull, idle>>
Ended(m, i) == i \in Resumes /\ q[m][i] = "waiting" /\ i \notin thread /\ i \notin idle /\ i \in pr
Stuck(m, i) == Ended(m, i) /\ i \notin merged /\ i \notin held /\ i \notin inq
  /\ ~NeedsPerson(i) /\ i \notin Review /\ (status[i] = "none" \/ Stands(i) \/ i \in broken)
Resume(m, i) == Stuck(m, i) /\ Set(m, i, "none") /\ needless' = (needless \/ ~Needed(i))
  /\ UNCHANGED <<held, work, startedHeld, jumped, crowded, endedUnverified, thread, pending, twoThreads, diedCleared, unmarkedCleared, refusedRerun, launchedFull, idle, cleanedLive>>
Unblock(m, i) == q[m][i] = "waiting" /\ i \in Hooked /\ i \notin pr /\ i \notin idle /\ pre' = pre \cup {i} /\ Set(m, i, "none")
  /\ UNCHANGED <<held, unread, up, full, pr, status, stale, merged, inq, drops, badMerge, okAtMerge, history>>
Unresume(m, i) == i \in Resumes /\ q[m][i] = "none" /\ i \in pr /\ i \notin pending /\ Set(m, i, "waiting")
  /\ UNCHANGED <<held, work, history>>

Flip == up' = ~up /\ UNCHANGED <<q, held, pre, unread, full, pr, status, stale, merged, inq, drops, badMerge, okAtMerge, history>>
Hold(i) == i \notin held /\ held' = held \cup {i} /\ UNCHANGED <<q, work, history>>
Unhold(i) == i \in held /\ held' = held \ {i} /\ UNCHANGED <<q, work, history>>
Approve(i) == i \notin pre /\ pre' = pre \cup {i} /\ UNCHANGED <<q, held, unread, up, full, pr, status, stale, merged, inq, drops, badMerge, okAtMerge, history>>
Garble(i) == i \in Garbles /\ i \in pr /\ i \notin merged /\ (\E m \in Machines : q[m][i] = "waiting") /\ i \notin unread /\ unread' = unread \cup {i} /\ UNCHANGED <<q, held, pre, up, full, pr, status, stale, merged, inq, drops, badMerge, okAtMerge, history>>
Break(i) == i \in Resumes /\ i \in pr /\ i \notin merged /\ i \notin broken /\ broken' = broken \cup {i}
  /\ UNCHANGED <<q, held, pre, unread, up, full, pr, status, stale, merged, inq, gone, lost, dropped, requeued, retried, red, badMerge, okAtMerge, history>>
Redden(i) == i \in Resumes /\ i \in pr /\ i \notin merged /\ i \notin red /\ red' = red \cup {i}
  /\ UNCHANGED <<q, held, pre, unread, up, full, pr, status, stale, merged, inq, gone, lost, dropped, requeued, retried, broken, badMerge, okAtMerge, history>>
Fill(m) == m \in Fills /\ m \notin full /\ full' = full \cup {m} /\ UNCHANGED <<q, held, pre, unread, up, pr, status, stale, merged, inq, drops, badMerge, okAtMerge, history>>
Prune(m) == m \in full /\ full' = full \ {m} /\ UNCHANGED <<q, held, pre, unread, up, pr, status, stale, merged, inq, drops, badMerge, okAtMerge, history>>
Mend(i) == i \in unread /\ unread' = unread \ {i} /\ UNCHANGED <<q, held, pre, up, full, pr, status, stale, merged, inq, drops, badMerge, okAtMerge, history>>

Next ==
  \/ \E m \in Machines, i \in Items :
       Launch(m, i) \/ Relaunch(m, i) \/ Push(m, i) \/ ThreadMerge(m, i) \/ PassMerge(m, i) \/ Dequeue(m, i) \/ Observe(m, i) \/ TurnEnd(m, i) \/ Freed(m, i) \/ Die(m, i) \/ Idle(m, i) \/ Rerun(m, i) \/ Abort(m, i) \/ Settle(m, i)
       \/ Resume(m, i) \/ Unresume(m, i) \/ Unblock(m, i)
       \/ Verdict(m, i, "auto") \/ Verdict(m, i, "review") \/ Verdict(m, i, "none")
  \/ \E i \in Items : HumanMerge(i) \/ Hold(i) \/ Unhold(i) \/ Approve(i) \/ Garble(i) \/ Mend(i) \/ Break(i) \/ Redden(i) \/ Started(i) \/ Lost(i) \/ IdleEnds(i)
     \/ Land(i) \/ Drop(i)
  \/ \E m \in Machines : Fill(m) \/ Prune(m)
  \/ Flip

Fair ==
  /\ \A m \in Machines, i \in Items : WF_vars(Abort(m, i)) /\ WF_vars(Settle(m, i)) /\ WF_vars(Lost(i)) /\ WF_vars(Resume(m, i))
       /\ WF_vars(PassMerge(m, i)) /\ WF_vars(Dequeue(m, i)) /\ WF_vars(Observe(m, i))
  /\ \A m \in Machines, i \in Items \ Idles : WF_vars(Freed(m, i))
  /\ \A m \in Machines, i \in Idles : WF_vars(Idle(m, i)) /\ WF_vars(Rerun(m, i))
Spec == Init /\ [][Next]_vars /\ Fair

OneMachinePerItem == \A i \in Items : Cardinality({m \in Machines : q[m][i] # "none"}) <= 1
OneThreadPerItem == ~twoThreads
CapHolds == ~crowded
NoLaunchBelowTheDiskFloor == ~launchedFull
ALiveThreadKeepsItsTree == ~cleanedLive
HeldNeverStarted == ~startedHeld
AResumedThreadIsNeeded == ~needless
PriorityOrder == ~jumped
OnlyAVerifiedAllowedHeadMerges == ~badMerge
ATurnEndsVerified == ~endedUnverified
AVerdictAlwaysLands == \A m \in Machines, i \in Items : q[m][i] = "waiting" => Takes(m, i)
NothingUnfinishedIsCleared == \A m \in Machines, i \in Items : q[m][i] = "done" => Finished(i)
ADeadThreadWithNoPRIsRunAgain == ~diedCleared
AnUnmarkedTurnWithNoPRIsRunAgain == ~unmarkedCleared
ARefusedPushWaitsForAPerson == ~refusedRerun
RunningIsReleased == \A m \in Machines, i \in Items : (q[m][i] = "running") ~> (q[m][i] # "running")
AnApprovedWaitIsMerged == \A m \in Machines, i \in Items : PassReady(m, i) ~> ~PassReady(m, i)
FreshRun(m, i) == q[m][i] = "waiting" /\ i \in dropped /\ i \in pre /\ i \in Retries /\ i \notin retried /\ i \notin Review /\ Open(i)
  /\ i \notin merged /\ i \notin unread /\ i \notin inq /\ i \notin lost
AFirstDropIsQueuedAgain == \A m \in Machines, i \in Items : FreshRun(m, i) ~> ~FreshRun(m, i)
ASecondDropIsNotQueued == ~requeued
OnlyADroppedHeadIsBlocked == dropped \subseteq gone
AHeldEntryLeavesTheQueue == \A i \in Items : (i \in inq /\ i \in held) ~> (i \notin inq \/ i \notin held)
AnEndedWaitIsRunAgain == \A m \in Machines, i \in Items : (Ended(m, i) /\ Needed(i)) ~> ~(Ended(m, i) /\ Needed(i))
AnEndedIdleThreadIsRunAgain == \A m \in Machines, i \in Idles : IdleOver(m, i) ~> ~IdleOver(m, i)
AMergedWaitIsCleared == \A m \in Machines, i \in Items : (q[m][i] = "waiting" /\ i \in merged /\ ~(i \in idle /\ i \in thread)) ~> (q[m][i] = "done")

MCAccepts == ("mac" :> {"i1", "i3"} @@ "linux" :> {"i2"})
MCPrio == ("i1" :> 1 @@ "i2" :> 2 @@ "i3" :> 2)
====
