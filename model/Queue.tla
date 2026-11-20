---- MODULE Queue ----
EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Machines, Items, Cap, Accepts, Review, Pre, Prio, NoTree, Garbles

VARIABLES q, held, pre, unread, up, pr, status, stale, merged, by, okAtMerge, thread, pending, startedHeld, jumped, crowded, endedUnverified, twoThreads

vars == <<q, held, pre, unread, up, pr, status, stale, merged, by, okAtMerge, thread, pending, startedHeld, jumped, crowded, endedUnverified, twoThreads>>
States == {"none", "running", "waiting", "done"}

TypeOK ==
  /\ q \in [Machines -> [Items -> States]]
  /\ held \subseteq Items /\ pre \subseteq Items /\ unread \subseteq Garbles /\ up \in BOOLEAN /\ pr \subseteq Items /\ stale \subseteq Items /\ merged \subseteq Items
  /\ status \in [Items -> {"none", "auto", "review"}]
  /\ by \in [Items -> {"none", "thread", "pass", "human"}] /\ okAtMerge \in [Items -> BOOLEAN]
  /\ thread \subseteq Items /\ pending \subseteq Items /\ twoThreads \in BOOLEAN
  /\ startedHeld \in BOOLEAN /\ jumped \in BOOLEAN /\ crowded \in BOOLEAN /\ endedUnverified \in BOOLEAN

Init ==
  /\ q = [m \in Machines |-> [i \in Items |-> "none"]]
  /\ held = {} /\ pre = Pre /\ unread = {} /\ up = TRUE /\ pr = {} /\ stale = {} /\ merged = {}
  /\ status = [i \in Items |-> "none"]
  /\ by = [i \in Items |-> "none"] /\ okAtMerge = [i \in Items |-> FALSE]
  /\ thread = {} /\ pending = {} /\ twoThreads = FALSE
  /\ startedHeld = FALSE /\ jumped = FALSE /\ crowded = FALSE /\ endedUnverified = FALSE

Set(m, i, s) == q' = [q EXCEPT ![m][i] = s]
Busy(m) == Cardinality({j \in Items : q[m][j] = "running"})
work == <<pre, unread, up, pr, status, stale, merged, by, okAtMerge>>
history == <<startedHeld, jumped, crowded, endedUnverified, thread, pending, twoThreads>>

Ready(m, i) == q[m][i] = "none" /\ i \in Accepts[m] /\ i \notin held
First(m, i) == \A j \in Items : Ready(m, j) => Prio[j] <= Prio[i]
Launch(m, i) == q[m][i] = "none" /\ i \in Accepts[m] /\ i \notin held /\ First(m, i)
  /\ Busy(m) < Cap
  /\ Set(m, i, "running") /\ pending' = pending \cup {i} /\ twoThreads' = (twoThreads \/ i \in pending \/ i \in thread)
  /\ jumped' = (jumped \/ ~First(m, i)) /\ startedHeld' = (startedHeld \/ i \in held) /\ crowded' = (crowded \/ Busy(m) >= Cap)
  /\ UNCHANGED <<held, work, endedUnverified, thread>>

Takes(m, i) == q[m][i] \in {"running", "waiting"}
Push(m, i) == Takes(m, i) /\ i \notin NoTree /\ i \notin merged
  /\ pr' = pr \cup {i} /\ status' = [status EXCEPT ![i] = "none"] /\ stale' = stale \cup {i}
  /\ UNCHANGED <<q, held, pre, unread, up, merged, by, okAtMerge, history>>
Verdict(m, i, v) == Takes(m, i) /\ i \in pr /\ i \notin merged
  /\ status' = [status EXCEPT ![i] = v] /\ stale' = stale \ {i}
  /\ UNCHANGED <<q, held, pre, unread, up, pr, merged, by, okAtMerge, history>>

NeedsPerson(i) == (status[i] = "review" \/ i \in Review) /\ i \notin pre
Open(i) == status[i] # "none" /\ ~NeedsPerson(i) /\ i \notin held
Merge(i, who) == i \in pr /\ i \notin merged /\ merged' = merged \cup {i} /\ by' = [by EXCEPT ![i] = who]
  /\ okAtMerge' = [okAtMerge EXCEPT ![i] = status[i] # "none" /\ i \notin stale /\ ~NeedsPerson(i) /\ i \notin held
                                           /\ (who = "pass" => i \in pre /\ i \notin Review)]
  /\ UNCHANGED <<q, held, pre, unread, up, pr, status, stale, history>>
ThreadMerge(m, i) == Takes(m, i) /\ Open(i) /\ Merge(i, "thread")
PassReady(m, i) == q[m][i] = "waiting" /\ i \in pr /\ i \notin merged /\ Open(i) /\ i \in pre /\ i \notin Review /\ i \notin unread
PassMerge(m, i) == PassReady(m, i) /\ Merge(i, "pass")
HumanMerge(i) == (\E m \in Machines : Takes(m, i)) /\ Merge(i, "human")

Unverified(i) == i \in pr /\ i \notin merged /\ i \in stale
Finished(i) == i \in merged \/ i \notin pr
Leave(m, i) ==
  /\ \/ Finished(i) /\ Set(m, i, "done")
     \/ i \notin merged /\ Set(m, i, "waiting")
  /\ i \in thread /\ thread' = thread \ {i}
  /\ UNCHANGED <<held, work, startedHeld, jumped, crowded, pending, twoThreads>>
TurnEnd(m, i) == q[m][i] = "running" /\ (up \/ i \in NoTree) /\ ~Unverified(i)
  /\ endedUnverified' = (endedUnverified \/ Unverified(i)) /\ Leave(m, i)
Freed(m, i) == q[m][i] = "running" /\ UNCHANGED endedUnverified /\ Leave(m, i)
Started(i) == i \in pending /\ pending' = pending \ {i} /\ thread' = thread \cup {i}
  /\ UNCHANGED <<q, held, work, startedHeld, jumped, crowded, endedUnverified, twoThreads>>
Lost(i) == i \in pending /\ pending' = pending \ {i}
  /\ UNCHANGED <<q, held, work, startedHeld, jumped, crowded, endedUnverified, thread, twoThreads>>
Abort(m, i) == q[m][i] = "running" /\ i \notin thread /\ i \notin pending /\ Set(m, i, "none") /\ UNCHANGED <<held, work, history>>
Settle(m, i) == q[m][i] = "waiting" /\ i \in merged /\ Set(m, i, "done") /\ UNCHANGED <<held, work, history>>

Flip == up' = ~up /\ UNCHANGED <<q, held, pre, unread, pr, status, stale, merged, by, okAtMerge, history>>
Hold(i) == i \notin held /\ held' = held \cup {i} /\ UNCHANGED <<q, work, history>>
Unhold(i) == i \in held /\ held' = held \ {i} /\ UNCHANGED <<q, work, history>>
Approve(i) == i \notin pre /\ pre' = pre \cup {i} /\ UNCHANGED <<q, held, unread, up, pr, status, stale, merged, by, okAtMerge, history>>
Garble(i) == i \in Garbles /\ i \notin unread /\ unread' = unread \cup {i} /\ UNCHANGED <<q, held, pre, up, pr, status, stale, merged, by, okAtMerge, history>>
Mend(i) == i \in unread /\ unread' = unread \ {i} /\ UNCHANGED <<q, held, pre, up, pr, status, stale, merged, by, okAtMerge, history>>

Next ==
  \/ \E m \in Machines, i \in Items :
       Launch(m, i) \/ Push(m, i) \/ ThreadMerge(m, i) \/ PassMerge(m, i) \/ TurnEnd(m, i) \/ Freed(m, i) \/ Abort(m, i) \/ Settle(m, i)
       \/ Verdict(m, i, "auto") \/ Verdict(m, i, "review") \/ Verdict(m, i, "none")
  \/ \E i \in Items : HumanMerge(i) \/ Hold(i) \/ Unhold(i) \/ Approve(i) \/ Garble(i) \/ Mend(i) \/ Started(i) \/ Lost(i)
  \/ Flip

Fair == \A m \in Machines, i \in Items : WF_vars(Freed(m, i)) /\ WF_vars(Abort(m, i)) /\ WF_vars(Settle(m, i)) /\ WF_vars(Lost(i))
  /\ WF_vars(PassMerge(m, i))
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
RunningIsReleased == \A m \in Machines, i \in Items : (q[m][i] = "running") ~> (q[m][i] # "running")
AnApprovedWaitIsMerged == \A m \in Machines, i \in Items : PassReady(m, i) ~> ~PassReady(m, i)
AMergedWaitIsCleared == \A m \in Machines, i \in Items : (q[m][i] = "waiting" /\ i \in merged) ~> (q[m][i] = "done")

MCAccepts == ("mac" :> {"i1", "i3"} @@ "linux" :> {"i2"})
MCPrio == ("i1" :> 1 @@ "i2" :> 2 @@ "i3" :> 2)
====
