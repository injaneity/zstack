---- MODULE Queue ----
EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Machines, Items, Cap, Accepts, Review, Pre, Prio, NoTree, Garbles, Paged, Queues, Woken

VARIABLES q, held, pre, unread, up, pr, status, stale, merged, inq, gone, lost, dropped, requeued, by, okAtMerge, thread, pending, startedHeld, jumped, crowded, endedUnverified, twoThreads, diedCleared

vars == <<q, held, pre, unread, up, pr, status, stale, merged, inq, gone, lost, dropped, requeued, by, okAtMerge, thread, pending, startedHeld, jumped, crowded, endedUnverified, twoThreads, diedCleared>>
States == {"none", "running", "waiting", "done"}

TypeOK ==
  /\ q \in [Machines -> [Items -> States]] /\ Paged \subseteq Items
  /\ held \subseteq Items /\ pre \subseteq Items /\ unread \subseteq Garbles /\ up \in BOOLEAN /\ pr \subseteq Items /\ stale \subseteq Items /\ merged \subseteq Items
  /\ Queues \subseteq Items /\ Woken \subseteq Items /\ inq \subseteq Queues /\ gone \subseteq Queues /\ lost \subseteq Queues /\ dropped \subseteq Queues /\ requeued \in BOOLEAN
  /\ status \in [Items -> {"none", "auto", "review"}]
  /\ by \in [Items -> {"none", "thread", "pass", "human"}] /\ okAtMerge \in [Items -> BOOLEAN]
  /\ thread \subseteq Items /\ pending \subseteq Items /\ twoThreads \in BOOLEAN /\ diedCleared \in BOOLEAN
  /\ startedHeld \in BOOLEAN /\ jumped \in BOOLEAN /\ crowded \in BOOLEAN /\ endedUnverified \in BOOLEAN

Init ==
  /\ q = [m \in Machines |-> [i \in Items |-> "none"]]
  /\ held = {} /\ pre = Pre /\ unread = {} /\ up = TRUE /\ pr = {} /\ stale = {} /\ merged = {}
  /\ status = [i \in Items |-> "none"]
  /\ inq = {} /\ gone = {} /\ lost = {} /\ dropped = {} /\ requeued = FALSE
  /\ by = [i \in Items |-> "none"] /\ okAtMerge = [i \in Items |-> FALSE]
  /\ thread = {} /\ pending = {} /\ twoThreads = FALSE /\ diedCleared = FALSE
  /\ startedHeld = FALSE /\ jumped = FALSE /\ crowded = FALSE /\ endedUnverified = FALSE

Set(m, i, s) == q' = [q EXCEPT ![m][i] = s]
Busy(m) == Cardinality({j \in Items : q[m][j] = "running"})
drops == <<gone, lost, dropped, requeued>>
work == <<pre, unread, up, pr, status, stale, merged, inq, drops, by, okAtMerge>>
history == <<startedHeld, jumped, crowded, endedUnverified, thread, pending, twoThreads, diedCleared>>

Ready(m, i) == q[m][i] = "none" /\ i \in Accepts[m] /\ i \notin held
First(m, i) == \A j \in Items : Ready(m, j) => Prio[j] <= Prio[i]
Launch(m, i) == q[m][i] = "none" /\ i \in Accepts[m] /\ i \notin held /\ First(m, i)
  /\ Busy(m) < Cap
  /\ Set(m, i, "running") /\ pending' = pending \cup {i} /\ twoThreads' = (twoThreads \/ i \in pending \/ i \in thread)
  /\ jumped' = (jumped \/ ~First(m, i)) /\ startedHeld' = (startedHeld \/ i \in held) /\ crowded' = (crowded \/ Busy(m) >= Cap)
  /\ UNCHANGED <<held, work, endedUnverified, thread, diedCleared>>

Takes(m, i) == q[m][i] \in {"running", "waiting"}
Push(m, i) == Takes(m, i) /\ i \notin NoTree /\ i \notin merged
  /\ pr' = pr \cup {i} /\ status' = [status EXCEPT ![i] = "none"] /\ stale' = stale \cup {i}
  /\ inq' = inq \ {i} /\ gone' = gone \ {i} /\ dropped' = dropped \ {i}
  /\ UNCHANGED <<q, held, pre, unread, up, merged, lost, requeued, by, okAtMerge, history>>
Verdict(m, i, v) == Takes(m, i) /\ i \in pr /\ i \notin merged
  /\ status' = [status EXCEPT ![i] = v] /\ stale' = stale \ {i}
  /\ UNCHANGED <<q, held, pre, unread, up, pr, merged, inq, drops, by, okAtMerge, history>>

NeedsPerson(i) == (status[i] = "review" \/ i \in Review) /\ i \notin pre
Open(i) == status[i] # "none" /\ ~NeedsPerson(i) /\ i \notin held
MayMerge(i, who) == status[i] # "none" /\ i \notin stale /\ ~NeedsPerson(i) /\ i \notin held /\ (who = "pass" => i \in pre /\ i \notin Review)
Merge(i, who) == i \in pr /\ i \notin merged /\ merged' = merged \cup {i} /\ by' = [by EXCEPT ![i] = who]
  /\ okAtMerge' = [okAtMerge EXCEPT ![i] = MayMerge(i, who)] /\ inq' = inq \ {i}
  /\ unread' = unread \ {i} /\ UNCHANGED <<q, held, pre, up, pr, status, stale, drops, history>>
Enqueue(i) == inq' = inq \cup {i} /\ okAtMerge' = [okAtMerge EXCEPT ![i] = MayMerge(i, "pass")] /\ requeued' = (requeued \/ i \in gone)
  /\ unread' = unread \ {i} /\ UNCHANGED <<q, held, pre, up, pr, status, stale, merged, gone, lost, dropped, by, history>>
MergeOrQueue(i) == IF i \in Queues THEN Enqueue(i) ELSE Merge(i, "pass")
ThreadMerge(m, i) == Takes(m, i) /\ Open(i) /\ Merge(i, "thread")
PassReady(m, i) == q[m][i] = "waiting" /\ i \in pr /\ i \notin merged /\ Open(i) /\ i \in pre /\ i \notin Review /\ i \notin unread /\ i \notin inq /\ i \notin lost /\ i \notin dropped
PassMerge(m, i) == PassReady(m, i) /\ MergeOrQueue(i)
Dequeue(m, i) == q[m][i] = "waiting" /\ i \in inq /\ i \in held /\ inq' = inq \ {i} /\ UNCHANGED <<q, held, pre, unread, up, pr, status, stale, merged, drops, by, okAtMerge, history>>
Land(i) == i \in inq /\ i \notin merged /\ merged' = merged \cup {i} /\ by' = [by EXCEPT ![i] = "pass"] /\ inq' = inq \ {i}
  /\ UNCHANGED <<q, held, pre, unread, up, pr, status, stale, drops, okAtMerge, history>>
Drop(i) == i \in inq /\ inq' = inq \ {i} /\ gone' = gone \cup {i} /\ lost' = lost \cup {i}
  /\ UNCHANGED <<q, held, pre, unread, up, pr, status, stale, merged, dropped, requeued, by, okAtMerge, history>>
Observe(m, i) == q[m][i] = "waiting" /\ i \in lost /\ lost' = lost \ {i} /\ dropped' = dropped \cup ({i} \cap gone)
  /\ UNCHANGED <<q, held, pre, unread, up, pr, status, stale, merged, inq, gone, requeued, by, okAtMerge, history>>
HumanMerge(i) == (\E m \in Machines : Takes(m, i)) /\ Merge(i, "human")

Unverified(i) == i \in pr /\ i \notin merged /\ i \in stale
Finished(i) == i \in merged \/ i \notin pr
Leave(m, i) ==
  /\ \/ Finished(i) /\ Set(m, i, "done")
     \/ i \notin merged /\ Set(m, i, "waiting")
  /\ i \in thread /\ thread' = thread \ {i}
  /\ UNCHANGED <<held, work, startedHeld, jumped, crowded, pending, twoThreads>>
TurnEnd(m, i) == q[m][i] = "running" /\ (up \/ i \in NoTree) /\ ~Unverified(i)
  /\ endedUnverified' = (endedUnverified \/ Unverified(i)) /\ Leave(m, i) /\ UNCHANGED diedCleared
Freed(m, i) == q[m][i] = "running" /\ UNCHANGED <<endedUnverified, diedCleared>> /\ Leave(m, i)
Die(m, i) == q[m][i] = "running" /\ i \in thread /\ UNCHANGED endedUnverified
  /\ (IF i \in pr THEN Leave(m, i)
      ELSE Set(m, i, "none") /\ thread' = thread \ {i} /\ UNCHANGED <<held, work, startedHeld, jumped, crowded, pending, twoThreads>>)
  /\ diedCleared' = (diedCleared \/ q'[m][i] = "done" /\ i \notin pr)
Started(i) == i \in pending /\ pending' = pending \ {i} /\ thread' = thread \cup {i}
  /\ UNCHANGED <<q, held, work, startedHeld, jumped, crowded, endedUnverified, twoThreads, diedCleared>>
Lost(i) == i \in pending /\ pending' = pending \ {i}
  /\ UNCHANGED <<q, held, work, startedHeld, jumped, crowded, endedUnverified, thread, twoThreads, diedCleared>>
Abort(m, i) == q[m][i] = "running" /\ i \notin thread /\ i \notin pending /\ Set(m, i, "none") /\ UNCHANGED <<held, work, history>>
Settle(m, i) == q[m][i] = "waiting" /\ i \in merged /\ Set(m, i, "done") /\ UNCHANGED <<held, work, history>>
Stuck(m, i) == q[m][i] = "waiting" /\ i \notin thread /\ i \in pr /\ i \notin merged /\ i \notin held /\ i \notin inq
  /\ (status[i] = "none" \/ i \in dropped)
Resume(m, i) == Stuck(m, i) /\ Set(m, i, "none") /\ UNCHANGED <<held, work, history>>
Wake(m, i) == i \in Woken /\ q[m][i] = "waiting" /\ i \notin thread /\ thread' = thread \cup {i}
  /\ UNCHANGED <<q, held, work, startedHeld, jumped, crowded, endedUnverified, pending, twoThreads, diedCleared>>

Flip == up' = ~up /\ UNCHANGED <<q, held, pre, unread, pr, status, stale, merged, inq, drops, by, okAtMerge, history>>
Hold(i) == i \notin held /\ held' = held \cup {i} /\ UNCHANGED <<q, work, history>>
Unhold(i) == i \in held /\ held' = held \ {i} /\ UNCHANGED <<q, work, history>>
Approve(i) == i \notin pre /\ pre' = pre \cup {i} /\ UNCHANGED <<q, held, unread, up, pr, status, stale, merged, inq, drops, by, okAtMerge, history>>
Garble(i) == i \in Garbles /\ i \in pr /\ i \notin merged /\ (\E m \in Machines : q[m][i] = "waiting") /\ i \notin unread /\ unread' = unread \cup {i} /\ UNCHANGED <<q, held, pre, up, pr, status, stale, merged, inq, drops, by, okAtMerge, history>>
Mend(i) == i \in unread /\ unread' = unread \ {i} /\ UNCHANGED <<q, held, pre, up, pr, status, stale, merged, inq, drops, by, okAtMerge, history>>

Next ==
  \/ \E m \in Machines, i \in Items :
       Launch(m, i) \/ Push(m, i) \/ ThreadMerge(m, i) \/ PassMerge(m, i) \/ Dequeue(m, i) \/ Observe(m, i) \/ TurnEnd(m, i) \/ Freed(m, i) \/ Die(m, i) \/ Abort(m, i) \/ Settle(m, i)
       \/ Resume(m, i) \/ Wake(m, i)
       \/ Verdict(m, i, "auto") \/ Verdict(m, i, "review") \/ Verdict(m, i, "none")
  \/ \E i \in Items : HumanMerge(i) \/ Hold(i) \/ Unhold(i) \/ Approve(i) \/ Garble(i) \/ Mend(i) \/ Started(i) \/ Lost(i)
     \/ Land(i) \/ Drop(i)
  \/ Flip

Fair == \A m \in Machines, i \in Items : WF_vars(Freed(m, i)) /\ WF_vars(Abort(m, i)) /\ WF_vars(Settle(m, i)) /\ WF_vars(Lost(i)) /\ WF_vars(Resume(m, i))
  /\ WF_vars(PassMerge(m, i)) /\ WF_vars(Dequeue(m, i)) /\ WF_vars(Observe(m, i))
Spec == Init /\ [][Next]_vars /\ Fair

OneMachinePerItem == \A i \in Items : Cardinality({m \in Machines : q[m][i] # "none"}) <= 1
OneThreadPerItem == ~twoThreads
CapHolds == ~crowded
HeldNeverStarted == ~startedHeld
PriorityOrder == ~jumped
OnlyAVerifiedAllowedHeadMerges == \A i \in Items : by[i] \in {"thread", "pass"} => okAtMerge[i]
ATurnEndsVerified == ~endedUnverified
AVerdictAlwaysLands == \A m \in Machines, i \in Items : q[m][i] = "waiting" => Takes(m, i)
NothingUnfinishedIsCleared == \A m \in Machines, i \in Items : q[m][i] = "done" => Finished(i)
ADeadThreadWithNoPRIsRunAgain == ~diedCleared
RunningIsReleased == \A m \in Machines, i \in Items : (q[m][i] = "running") ~> (q[m][i] # "running")
AnApprovedWaitIsMerged == \A m \in Machines, i \in Items : PassReady(m, i) ~> ~PassReady(m, i)
NoDroppedHeadIsQueued == ~requeued
OnlyADroppedHeadIsBlocked == dropped \subseteq gone
AHeldEntryLeavesTheQueue == \A i \in Items : (i \in inq /\ i \in held) ~> (i \notin inq \/ i \notin held)
AnEndedWaitIsRunAgain == \A m \in Machines, i \in Items : Stuck(m, i) ~> ~Stuck(m, i)
AMergedWaitIsCleared == \A m \in Machines, i \in Items : (q[m][i] = "waiting" /\ i \in merged) ~> (q[m][i] = "done")

MCAccepts == ("mac" :> {"i1", "i3"} @@ "linux" :> {"i2"})
MCPrio == ("i1" :> 1 @@ "i2" :> 2 @@ "i3" :> 2)
====
