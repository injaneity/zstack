---- MODULE Queue ----
EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Machines, Items, Cap, Accepts, Review, Pre, Prio

VARIABLES q, held, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale

vars == <<q, held, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale>>
States == {"none", "running", "waiting", "done"}

TypeOK ==
  /\ q \in [Machines -> [Items -> States]]
  /\ held \subseteq Items
  /\ status \in [Items -> {"none", "auto", "review"}]
  /\ merged \subseteq Items
  /\ by \in [Items -> {"none", "thread", "human"}] /\ okAtMerge \in [Items -> BOOLEAN]
  /\ startedHeld \in BOOLEAN /\ jumped \in BOOLEAN /\ crowded \in BOOLEAN /\ stale \subseteq Items

Init ==
  /\ q = [m \in Machines |-> [i \in Items |-> "none"]]
  /\ held = {} /\ status = [i \in Items |-> "none"]
  /\ merged = {} /\ by = [i \in Items |-> "none"] /\ okAtMerge = [i \in Items |-> FALSE]
  /\ startedHeld = FALSE /\ jumped = FALSE /\ crowded = FALSE /\ stale = {}

Set(m, i, s) == q' = [q EXCEPT ![m][i] = s]
Busy(m) == Cardinality({j \in Items : q[m][j] = "running"})
rest == <<held, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale>>

Ready(m, i) == q[m][i] = "none" /\ i \in Accepts[m] /\ i \notin held
First(m, i) == \A j \in Items : Ready(m, j) => Prio[j] <= Prio[i]
Claim(m, i) == q[m][i] = "none" /\ i \in Accepts[m] /\ i \notin held /\ First(m, i)
  /\ Busy(m) < Cap
  /\ Set(m, i, "running")
  /\ jumped' = (jumped \/ ~First(m, i)) /\ startedHeld' = (startedHeld \/ i \in held) /\ crowded' = (crowded \/ Busy(m) >= Cap)
  /\ UNCHANGED <<held, status, merged, by, okAtMerge, stale>>

Takes(m, i) == q[m][i] \in {"running", "waiting"}
Verdict(m, i, v) == Takes(m, i) /\ i \notin merged /\ status' = [status EXCEPT ![i] = v] /\ stale' = stale \ {i}
  /\ UNCHANGED <<q, held, merged, by, okAtMerge, startedHeld, jumped, crowded>>
Push(m, i) == Takes(m, i) /\ i \notin merged /\ status[i] # "none" /\ status' = [status EXCEPT ![i] = "none"] /\ stale' = stale \cup {i}
  /\ UNCHANGED <<q, held, merged, by, okAtMerge, startedHeld, jumped, crowded>>

NeedsPerson(i) == (status[i] = "review" \/ i \in Review) /\ i \notin Pre
Open(i) == status[i] # "none" /\ ~NeedsPerson(i) /\ i \notin held
Merge(i, who) == i \notin merged /\ merged' = merged \cup {i} /\ by' = [by EXCEPT ![i] = who]
  /\ okAtMerge' = [okAtMerge EXCEPT ![i] = status[i] # "none" /\ i \notin stale /\ ~NeedsPerson(i) /\ i \notin held]
  /\ UNCHANGED <<q, held, status, startedHeld, jumped, crowded, stale>>
ThreadMerge(m, i) == Takes(m, i) /\ Open(i) /\ Merge(i, "thread")
HumanMerge(i) == (\E m \in Machines : Takes(m, i)) /\ Merge(i, "human")

Finished(i) == i \in merged \/ status[i] = "none"
Release(m, i) == q[m][i] = "running" /\ UNCHANGED rest
  /\ \/ Finished(i) /\ Set(m, i, "done")
     \/ i \notin merged /\ Set(m, i, "waiting")
Settle(m, i) == q[m][i] = "waiting" /\ i \in merged /\ Set(m, i, "done") /\ UNCHANGED rest

Hold(i) == i \notin held /\ held' = held \cup {i}
  /\ UNCHANGED <<q, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale>>
Unhold(i) == i \in held /\ held' = held \ {i}
  /\ UNCHANGED <<q, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale>>

Next ==
  \/ \E m \in Machines, i \in Items :
       Claim(m, i) \/ Push(m, i) \/ ThreadMerge(m, i) \/ Release(m, i) \/ Settle(m, i)
       \/ Verdict(m, i, "auto") \/ Verdict(m, i, "review") \/ Verdict(m, i, "none")
  \/ \E i \in Items : HumanMerge(i) \/ Hold(i) \/ Unhold(i)

Fair == \A m \in Machines, i \in Items : WF_vars(Release(m, i)) /\ WF_vars(Settle(m, i))
Spec == Init /\ [][Next]_vars /\ Fair

OneMachinePerItem == \A i \in Items : Cardinality({m \in Machines : q[m][i] # "none"}) <= 1
CapHolds == ~crowded
HeldNeverStarted == ~startedHeld
PriorityOrder == ~jumped
ThreadMergesOnlyAVerifiedAllowedHead == \A i \in Items : by[i] = "thread" => okAtMerge[i]
AVerdictAlwaysLands == \A m \in Machines, i \in Items : q[m][i] = "waiting" => Takes(m, i)
NothingUnfinishedIsCleared == \A m \in Machines, i \in Items : q[m][i] = "done" => Finished(i)
RunningIsReleased == \A m \in Machines, i \in Items : (q[m][i] = "running") ~> (q[m][i] # "running")
AMergedWaitIsCleared == \A m \in Machines, i \in Items : (q[m][i] = "waiting" /\ i \in merged) ~> (q[m][i] = "done")

MCAccepts == ("mac" :> {"i1", "i3"} @@ "linux" :> {"i2"})
MCPrio == ("i1" :> 1 @@ "i2" :> 2 @@ "i3" :> 2)
====
