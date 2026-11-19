---- MODULE Queue ----
EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Machines, Items, Cap, Accepts, Review, Pre, Prio, NoTree

VARIABLES q, tree, threads, known, held, up, pr, status, stale, merged, by, okAtMerge, startedHeld, jumped, crowded, endedUnverified

vars == <<q, tree, threads, known, held, up, pr, status, stale, merged, by, okAtMerge, startedHeld, jumped, crowded, endedUnverified>>
States == {"none", "running", "waiting", "done"}

TypeOK ==
  /\ q \in [Machines -> [Items -> States]]
  /\ tree \subseteq Items /\ threads \in [Items -> 0..2] /\ known \subseteq Items
  /\ held \subseteq Items /\ up \in BOOLEAN /\ pr \subseteq Items /\ stale \subseteq Items /\ merged \subseteq Items
  /\ status \in [Items -> {"none", "auto", "review"}]
  /\ by \in [Items -> {"none", "thread", "human"}] /\ okAtMerge \in [Items -> BOOLEAN]
  /\ startedHeld \in BOOLEAN /\ jumped \in BOOLEAN /\ crowded \in BOOLEAN /\ endedUnverified \in BOOLEAN

Init ==
  /\ q = [m \in Machines |-> [i \in Items |-> "none"]]
  /\ tree = {} /\ threads = [i \in Items |-> 0] /\ known = {}
  /\ held = {} /\ up = TRUE /\ pr = {} /\ stale = {} /\ merged = {}
  /\ status = [i \in Items |-> "none"]
  /\ by = [i \in Items |-> "none"] /\ okAtMerge = [i \in Items |-> FALSE]
  /\ startedHeld = FALSE /\ jumped = FALSE /\ crowded = FALSE /\ endedUnverified = FALSE

Set(m, i, s) == q' = [q EXCEPT ![m][i] = s]
launch == <<tree, threads, known>>
Busy(m) == Cardinality({j \in Items : q[m][j] = "running"})
work == <<up, pr, status, stale, merged, by, okAtMerge>>
history == <<startedHeld, jumped, crowded, endedUnverified>>

Ready(m, i) == q[m][i] = "none" /\ i \in Accepts[m] /\ i \notin held
First(m, i) == \A j \in Items : Ready(m, j) => Prio[j] <= Prio[i]
Claim(m, i) == q[m][i] = "none" /\ i \in Accepts[m] /\ i \notin held /\ First(m, i)
  /\ Busy(m) < Cap
  /\ Set(m, i, "running") /\ tree' = tree \cup {i} /\ UNCHANGED <<threads, known>>
  /\ jumped' = (jumped \/ ~First(m, i)) /\ startedHeld' = (startedHeld \/ i \in held) /\ crowded' = (crowded \/ Busy(m) >= Cap)
  /\ UNCHANGED <<held, work, endedUnverified>>
Unattached(m, i) == q[m][i] = "running" /\ i \notin known
Launch(m, i) == Unattached(m, i) /\ i \in tree /\ threads[i] = 0
  /\ threads' = [threads EXCEPT ![i] = @ + 1] /\ UNCHANGED <<q, tree, known, held, work, history>>
Attach(m, i) == Unattached(m, i) /\ threads[i] > 0
  /\ known' = known \cup {i} /\ UNCHANGED <<q, tree, threads, held, work, history>>

Takes(m, i) == q[m][i] \in {"running", "waiting"}
Push(m, i) == Takes(m, i) /\ threads[i] > 0 /\ i \notin NoTree /\ i \notin merged
  /\ pr' = pr \cup {i} /\ status' = [status EXCEPT ![i] = "none"] /\ stale' = stale \cup {i}
  /\ UNCHANGED <<q, launch, held, up, merged, by, okAtMerge, history>>
Verdict(m, i, v) == Takes(m, i) /\ i \in pr /\ i \notin merged
  /\ status' = [status EXCEPT ![i] = v] /\ stale' = stale \ {i}
  /\ UNCHANGED <<q, launch, held, up, pr, merged, by, okAtMerge, history>>

NeedsPerson(i) == (status[i] = "review" \/ i \in Review) /\ i \notin Pre
Open(i) == status[i] # "none" /\ ~NeedsPerson(i) /\ i \notin held
Merge(i, who) == i \in pr /\ i \notin merged /\ merged' = merged \cup {i} /\ by' = [by EXCEPT ![i] = who]
  /\ okAtMerge' = [okAtMerge EXCEPT ![i] = status[i] # "none" /\ i \notin stale /\ ~NeedsPerson(i) /\ i \notin held]
  /\ UNCHANGED <<q, launch, held, up, pr, status, stale, history>>
ThreadMerge(m, i) == Takes(m, i) /\ Open(i) /\ Merge(i, "thread")
HumanMerge(i) == (\E m \in Machines : Takes(m, i)) /\ Merge(i, "human")

Unverified(i) == i \in pr /\ i \notin merged /\ i \in stale
Finished(i) == i \in merged \/ i \notin pr
Leave(m, i) ==
  /\ \/ Finished(i) /\ Set(m, i, "done")
     \/ i \notin merged /\ Set(m, i, "waiting")
  /\ UNCHANGED <<launch, held, work, startedHeld, jumped, crowded>>
TurnEnd(m, i) == q[m][i] = "running" /\ i \in known /\ (up \/ i \in NoTree) /\ ~Unverified(i)
  /\ endedUnverified' = (endedUnverified \/ Unverified(i)) /\ Leave(m, i)
Freed(m, i) == q[m][i] = "running" /\ i \in known /\ UNCHANGED endedUnverified /\ Leave(m, i)
Settle(m, i) == q[m][i] = "waiting" /\ i \in merged /\ Set(m, i, "done") /\ UNCHANGED <<launch, held, work, history>>

Flip == up' = ~up /\ UNCHANGED <<q, launch, held, pr, status, stale, merged, by, okAtMerge, history>>
Hold(i) == i \notin held /\ held' = held \cup {i} /\ UNCHANGED <<q, launch, work, history>>
Unhold(i) == i \in held /\ held' = held \ {i} /\ UNCHANGED <<q, launch, work, history>>

Next ==
  \/ \E m \in Machines, i \in Items :
       Claim(m, i) \/ Launch(m, i) \/ Attach(m, i) \/ Push(m, i) \/ ThreadMerge(m, i) \/ TurnEnd(m, i) \/ Freed(m, i) \/ Settle(m, i)
       \/ Verdict(m, i, "auto") \/ Verdict(m, i, "review") \/ Verdict(m, i, "none")
  \/ \E i \in Items : HumanMerge(i) \/ Hold(i) \/ Unhold(i)
  \/ Flip

Fair == \A m \in Machines, i \in Items : WF_vars(Launch(m, i)) /\ WF_vars(Attach(m, i)) /\ WF_vars(Freed(m, i)) /\ WF_vars(Settle(m, i))
Spec == Init /\ [][Next]_vars /\ Fair

OneMachinePerItem == \A i \in Items : Cardinality({m \in Machines : q[m][i] # "none"}) <= 1
CapHolds == ~crowded
OneThreadPerItem == \A i \in Items : threads[i] <= 1
AThreadWorksInTheQueuesTree == \A i \in Items : threads[i] > 0 => i \in tree
HeldNeverStarted == ~startedHeld
PriorityOrder == ~jumped
ThreadMergesOnlyAVerifiedAllowedHead == \A i \in Items : by[i] = "thread" => okAtMerge[i]
ATurnEndsVerified == ~endedUnverified
AVerdictAlwaysLands == \A m \in Machines, i \in Items : q[m][i] = "waiting" => Takes(m, i)
NothingUnfinishedIsCleared == \A m \in Machines, i \in Items : q[m][i] = "done" => Finished(i)
RunningIsReleased == \A m \in Machines, i \in Items : (q[m][i] = "running") ~> (q[m][i] # "running")
AMergedWaitIsCleared == \A m \in Machines, i \in Items : (q[m][i] = "waiting" /\ i \in merged) ~> (q[m][i] = "done")

MCAccepts == ("mac" :> {"i1", "i3"} @@ "linux" :> {"i2"})
MCPrio == ("i1" :> 1 @@ "i2" :> 2 @@ "i3" :> 2)
====
