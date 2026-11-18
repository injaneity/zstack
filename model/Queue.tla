---- MODULE Queue ----
EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Machines, Items, None, Cap, Accepts, Review, Pre, Prio

VARIABLES q, threads, held, launching, fired, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale

vars == <<q, threads, held, launching, fired, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale>>
States == {"none", "queued", "starting", "running", "released"}

TypeOK ==
  /\ q \in [Machines -> [Items -> States]]
  /\ threads \in [Items -> 0..3]
  /\ held \subseteq Items
  /\ launching \in [Machines -> Items \cup {None}] /\ fired \in [Machines -> BOOLEAN]
  /\ status \in [Items -> {"none", "auto", "review"}]
  /\ merged \subseteq Items
  /\ by \in [Items -> {"none", "thread", "human"}] /\ okAtMerge \in [Items -> BOOLEAN]
  /\ startedHeld \in BOOLEAN /\ jumped \in BOOLEAN /\ crowded \in BOOLEAN /\ stale \subseteq Items

Init ==
  /\ q = [m \in Machines |-> [i \in Items |-> "none"]]
  /\ threads = [i \in Items |-> 0] /\ held = {}
  /\ launching = [m \in Machines |-> None] /\ fired = [m \in Machines |-> FALSE] /\ status = [i \in Items |-> "none"]
  /\ merged = {} /\ by = [i \in Items |-> "none"] /\ okAtMerge = [i \in Items |-> FALSE]
  /\ startedHeld = FALSE /\ jumped = FALSE /\ crowded = FALSE /\ stale = {}

Set(m, i, s) == q' = [q EXCEPT ![m][i] = s]
Busy(m) == Cardinality({j \in Items : q[m][j] \in {"starting", "running"}})
rest == <<threads, held, launching, fired, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale>>

Enter(m, i) == q[m][i] = "none" /\ i \in Accepts[m] /\ i \notin held
  /\ Set(m, i, "queued") /\ UNCHANGED rest
Pickup(m, i) == Enter(m, i)
Add(m, i) == Enter(m, i)

First(m, i) == \A j \in Items : q[m][j] = "queued" /\ j \notin held => Prio[j] <= Prio[i]
Start(m, i) == q[m][i] = "queued" /\ launching[m] = None /\ i \notin held /\ First(m, i)
  /\ Busy(m) < Cap
  /\ Set(m, i, "starting") /\ launching' = [launching EXCEPT ![m] = i]
  /\ jumped' = (jumped \/ ~First(m, i)) /\ startedHeld' = (startedHeld \/ i \in held) /\ crowded' = (crowded \/ Busy(m) >= Cap)
  /\ UNCHANGED <<threads, held, fired, status, merged, by, okAtMerge, stale>>

Launch(m) == launching[m] # None /\ ~fired[m]
  /\ threads' = [threads EXCEPT ![launching[m]] = @ + 1] /\ fired' = [fired EXCEPT ![m] = TRUE]
  /\ UNCHANGED <<q, held, launching, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale>>

Claim(m) == launching[m] # None /\ fired[m]
  /\ Set(m, launching[m], "running") /\ launching' = [launching EXCEPT ![m] = None] /\ fired' = [fired EXCEPT ![m] = FALSE]
  /\ UNCHANGED <<threads, held, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale>>

Requeue(m) == launching[m] # None /\ ~fired[m]
  /\ Set(m, launching[m], "queued") /\ launching' = [launching EXCEPT ![m] = None]
  /\ UNCHANGED <<threads, held, fired, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale>>

Crash(m) == launching[m] # None /\ launching' = [launching EXCEPT ![m] = None] /\ fired' = [fired EXCEPT ![m] = FALSE]
  /\ UNCHANGED <<q, threads, held, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale>>

Recover(m, i) == q[m][i] = "starting" /\ launching[m] # i
  /\ Set(m, i, IF threads[i] > 0 THEN "running" ELSE "queued")
  /\ UNCHANGED rest

Takes(m, i) == q[m][i] \in {"running", "released"}
Verdict(m, i, v) == Takes(m, i) /\ i \notin merged /\ status' = [status EXCEPT ![i] = v] /\ stale' = stale \ {i}
  /\ UNCHANGED <<q, threads, held, launching, fired, merged, by, okAtMerge, startedHeld, jumped, crowded>>
Push(m, i) == Takes(m, i) /\ i \notin merged /\ status[i] # "none" /\ status' = [status EXCEPT ![i] = "none"] /\ stale' = stale \cup {i}
  /\ UNCHANGED <<q, threads, held, launching, fired, merged, by, okAtMerge, startedHeld, jumped, crowded>>

NeedsPerson(i) == (status[i] = "review" \/ i \in Review) /\ i \notin Pre
Open(i) == status[i] # "none" /\ ~NeedsPerson(i) /\ i \notin held
Merge(i, who) == i \notin merged /\ merged' = merged \cup {i} /\ by' = [by EXCEPT ![i] = who]
  /\ okAtMerge' = [okAtMerge EXCEPT ![i] = status[i] # "none" /\ i \notin stale /\ ~NeedsPerson(i) /\ i \notin held]
  /\ UNCHANGED <<q, threads, held, launching, fired, status, startedHeld, jumped, crowded, stale>>
ThreadMerge(m, i) == Takes(m, i) /\ Open(i) /\ Merge(i, "thread")
HumanMerge(i) == (\E m \in Machines : Takes(m, i)) /\ Merge(i, "human")

Release(m, i) == q[m][i] = "running" /\ Set(m, i, "released") /\ UNCHANGED rest

Hold(i) == i \notin held /\ held' = held \cup {i}
  /\ UNCHANGED <<q, threads, launching, fired, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale>>
Unhold(i) == i \in held /\ held' = held \ {i}
  /\ UNCHANGED <<q, threads, launching, fired, status, merged, by, okAtMerge, startedHeld, jumped, crowded, stale>>

Next ==
  \/ \E m \in Machines : Launch(m) \/ Claim(m) \/ Requeue(m) \/ Crash(m)
  \/ \E m \in Machines, i \in Items :
       Pickup(m, i) \/ Add(m, i) \/ Start(m, i) \/ Recover(m, i) \/ Push(m, i) \/ ThreadMerge(m, i) \/ Release(m, i)
       \/ Verdict(m, i, "auto") \/ Verdict(m, i, "review") \/ Verdict(m, i, "none")
  \/ \E i \in Items : HumanMerge(i) \/ Hold(i) \/ Unhold(i)

Fair == \A m \in Machines : WF_vars(Claim(m)) /\ WF_vars(Requeue(m)) /\ \A i \in Items : WF_vars(Recover(m, i)) /\ WF_vars(Release(m, i))
Spec == Init /\ [][Next]_vars /\ Fair

OneMachinePerItem == \A i \in Items : Cardinality({m \in Machines : q[m][i] # "none"}) <= 1
OneThreadPerItem == \A i \in Items : threads[i] <= 1
CapHolds == ~crowded
HeldNeverStarted == ~startedHeld
PriorityOrder == ~jumped
ThreadMergesOnlyAVerifiedAllowedHead == \A i \in Items : by[i] = "thread" => okAtMerge[i]
RunningHasThread == \A m \in Machines, i \in Items : q[m][i] \in {"running", "released"} => threads[i] = 1
AVerdictAlwaysLands == \A m \in Machines, i \in Items : q[m][i] = "released" => Takes(m, i)
StartingResolves == \A m \in Machines, i \in Items : (q[m][i] = "starting") ~> (q[m][i] # "starting")
RunningIsReleased == \A m \in Machines, i \in Items : (q[m][i] = "running") ~> (q[m][i] # "running")

MCAccepts == ("mac" :> {"i1", "i3"} @@ "linux" :> {"i2"})
MCPrio == ("i1" :> 1 @@ "i2" :> 2 @@ "i3" :> 2)
====
