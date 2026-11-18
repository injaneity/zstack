---- MODULE Queue ----
EXTENDS Naturals, FiniteSets, TLC

CONSTANTS Machines, Items, None, Cap, MaxFix, Accepts, Review, Pre, Prio

VARIABLES q, threads, live, held, launching, fired, raised, status, fixes, merged, by, verifiedAtMerge, fresh, armed, reviewAtMerge,
          startedHeld, jumped, pr

vars == <<q, threads, live, held, launching, fired, raised, status, fixes, merged, by, verifiedAtMerge, fresh, armed, reviewAtMerge, startedHeld, jumped, pr>>
States == {"none", "queued", "starting", "working", "passed", "owner", "done"}

TypeOK ==
  /\ q \in [Machines -> [Items -> States]]
  /\ threads \in [Items -> 0..3]
  /\ live \in [Machines -> SUBSET Items]
  /\ held \subseteq Items
  /\ launching \in [Machines -> Items \cup {None}] /\ fired \in [Machines -> BOOLEAN]
  /\ raised \subseteq Items
  /\ status \in [Items -> {"none", "pass"}]
  /\ fixes \in [Items -> 0..(MaxFix + 1)]
  /\ merged \subseteq Items
  /\ by \in [Items -> {"none", "auto", "human"}]
  /\ verifiedAtMerge \in [Items -> BOOLEAN] /\ reviewAtMerge \in [Items -> BOOLEAN] /\ armed \subseteq Items /\ fresh \in [Items -> BOOLEAN]
  /\ startedHeld \in BOOLEAN /\ jumped \in BOOLEAN /\ pr \subseteq Items

Init ==
  /\ q = [m \in Machines |-> [i \in Items |-> "none"]]
  /\ threads = [i \in Items |-> 0] /\ live = [m \in Machines |-> {}] /\ held = {}
  /\ launching = [m \in Machines |-> None] /\ fired = [m \in Machines |-> FALSE] /\ raised = {} /\ status = [i \in Items |-> "none"]
  /\ fixes = [i \in Items |-> 0] /\ merged = {} /\ by = [i \in Items |-> "none"]
  /\ verifiedAtMerge = [i \in Items |-> FALSE] /\ reviewAtMerge = [i \in Items |-> FALSE] /\ armed = {} /\ fresh = [i \in Items |-> FALSE]
  /\ startedHeld = FALSE /\ jumped = FALSE /\ pr = {}

Set(m, i, s) == q' = [q EXCEPT ![m][i] = s]
NeedsReview(i) == (i \in Review \/ i \in raised) /\ i \notin Pre
rest == <<threads, live, held, launching, fired, raised, status, fixes, merged, by, verifiedAtMerge, fresh, armed, reviewAtMerge, startedHeld, jumped, pr>>

Enter(m, i) == q[m][i] = "none" /\ i \in Accepts[m] /\ i \notin held
  /\ Set(m, i, "queued") /\ UNCHANGED rest
Pickup(m, i) == Enter(m, i)
Add(m, i) == Enter(m, i)

First(m, i) == \A j \in Items : q[m][j] = "queued" /\ j \notin held => Prio[j] <= Prio[i]
Start(m, i) == q[m][i] = "queued" /\ launching[m] = None /\ i \notin held /\ First(m, i)
  /\ Cardinality(live[m]) < Cap
  /\ Set(m, i, "starting") /\ launching' = [launching EXCEPT ![m] = i]
  /\ jumped' = (jumped \/ ~First(m, i)) /\ startedHeld' = (startedHeld \/ i \in held)
  /\ UNCHANGED <<threads, live, held, raised, status, fixes, merged, by, verifiedAtMerge, fresh, armed, reviewAtMerge, fired, pr>>

Launch(m) == launching[m] # None /\ ~fired[m]
  /\ threads' = [threads EXCEPT ![launching[m]] = @ + 1] /\ live' = [live EXCEPT ![m] = @ \cup {launching[m]}]
  /\ fired' = [fired EXCEPT ![m] = TRUE]
  /\ UNCHANGED <<q, held, launching, raised, status, fixes, merged, by, verifiedAtMerge, fresh, armed, reviewAtMerge, startedHeld, jumped, pr>>

Claim(m) == launching[m] # None /\ fired[m]
  /\ Set(m, launching[m], "working") /\ launching' = [launching EXCEPT ![m] = None] /\ fired' = [fired EXCEPT ![m] = FALSE]
  /\ UNCHANGED <<threads, live, held, raised, status, fixes, merged, by, verifiedAtMerge, fresh, armed, reviewAtMerge, startedHeld, jumped, pr>>

Requeue(m) == launching[m] # None /\ ~fired[m]
  /\ Set(m, launching[m], "queued") /\ launching' = [launching EXCEPT ![m] = None]
  /\ UNCHANGED <<threads, live, held, raised, status, fixes, merged, by, verifiedAtMerge, fresh, armed, reviewAtMerge, fired, startedHeld, jumped, pr>>

Crash(m) == launching[m] # None /\ launching' = [launching EXCEPT ![m] = None] /\ fired' = [fired EXCEPT ![m] = FALSE]
  /\ UNCHANGED <<q, threads, live, held, raised, status, fixes, merged, by, verifiedAtMerge, fresh, armed, reviewAtMerge, startedHeld, jumped, pr>>

Recover(m, i) == q[m][i] = "starting" /\ launching[m] # i
  /\ Set(m, i, IF threads[i] > 0 THEN "working" ELSE "queued")
  /\ UNCHANGED rest

Raise(m, i) == q[m][i] \in {"working", "passed"} /\ i \notin merged
  /\ raised' = raised \cup {i} /\ armed' = (IF i \in Pre THEN armed ELSE armed \ {i})
  /\ UNCHANGED <<q, threads, live, held, launching, fired, status, fixes, merged, by, verifiedAtMerge, fresh, reviewAtMerge, startedHeld, jumped, pr>>

Pass(m, i) == q[m][i] \in {"working", "passed"} /\ Set(m, i, "passed") /\ status' = [status EXCEPT ![i] = "pass"]
  /\ armed' = (IF NeedsReview(i) THEN armed \ {i} ELSE armed \cup {i})
  /\ fresh' = [fresh EXCEPT ![i] = TRUE] /\ pr' = pr \cup {i}
  /\ UNCHANGED <<threads, live, held, launching, fired, raised, fixes, merged, by, verifiedAtMerge, reviewAtMerge, startedHeld, jumped>>

Fail(m, i) == q[m][i] \in {"working", "passed"} /\ fixes' = [fixes EXCEPT ![i] = @ + 1]
  /\ Set(m, i, IF fixes[i] + 1 < MaxFix THEN "working" ELSE "owner")
  /\ fresh' = [fresh EXCEPT ![i] = FALSE] /\ armed' = armed \ {i} /\ status' = [status EXCEPT ![i] = "none"] /\ pr' = pr \cup {i}
  /\ UNCHANGED <<threads, live, held, launching, fired, raised, merged, by, verifiedAtMerge, reviewAtMerge, startedHeld, jumped>>

Push(m, i) == q[m][i] = "passed" /\ i \notin merged
  /\ status' = [status EXCEPT ![i] = "none"] /\ Set(m, i, "working") /\ fresh' = [fresh EXCEPT ![i] = FALSE]
  /\ UNCHANGED <<threads, live, held, launching, fired, raised, fixes, merged, by, verifiedAtMerge, armed, reviewAtMerge, startedHeld, jumped, pr>>

Merge(i, who) == i \notin merged /\ merged' = merged \cup {i} /\ by' = [by EXCEPT ![i] = who]
  /\ verifiedAtMerge' = [verifiedAtMerge EXCEPT ![i] = fresh[i]]
  /\ reviewAtMerge' = [reviewAtMerge EXCEPT ![i] = NeedsReview(i) \/ (who = "auto" /\ i \in held)]
  /\ UNCHANGED <<q, threads, live, held, launching, fired, raised, status, fixes, armed, fresh, startedHeld, jumped, pr>>
AutoMerge(i) == i \in armed /\ status[i] = "pass" /\ i \notin held /\ Merge(i, "auto")
HumanMerge(i) == (\E m \in Machines : q[m][i] \in {"working", "passed", "owner"}) /\ Merge(i, "human")

Done(m, i) == q[m][i] \in {"working", "passed", "owner"} /\ i \in merged
  /\ Set(m, i, "done") /\ live' = [live EXCEPT ![m] = @ \ {i}]
  /\ UNCHANGED <<threads, held, launching, fired, raised, status, fixes, merged, by, verifiedAtMerge, fresh, armed, reviewAtMerge, startedHeld, jumped, pr>>

End(m, i) == q[m][i] = "working" /\ live' = [live EXCEPT ![m] = @ \ {i}]
  /\ \/ i \notin pr /\ Set(m, i, "done") /\ pr' = pr
     \/ Set(m, i, "owner") /\ pr' = pr \cup {i}
  /\ UNCHANGED <<threads, held, launching, fired, raised, status, fixes, merged, by, verifiedAtMerge, fresh, armed, reviewAtMerge, startedHeld, jumped>>

Hold(i) == i \notin held /\ held' = held \cup {i}
  /\ UNCHANGED <<q, threads, live, launching, fired, raised, status, fixes, merged, by, verifiedAtMerge, fresh, armed, reviewAtMerge, startedHeld, jumped, pr>>
Unhold(i) == i \in held /\ held' = held \ {i}
  /\ UNCHANGED <<q, threads, live, launching, fired, raised, status, fixes, merged, by, verifiedAtMerge, fresh, armed, reviewAtMerge, startedHeld, jumped, pr>>

Next ==
  \/ \E m \in Machines : Launch(m) \/ Claim(m) \/ Requeue(m) \/ Crash(m)
  \/ \E m \in Machines, i \in Items :
       Pickup(m, i) \/ Add(m, i) \/ Start(m, i) \/ Recover(m, i) \/ Raise(m, i)
       \/ Pass(m, i) \/ Fail(m, i) \/ Push(m, i) \/ Done(m, i) \/ End(m, i)
  \/ \E i \in Items : AutoMerge(i) \/ HumanMerge(i) \/ Hold(i) \/ Unhold(i)

Fair == WF_vars(End("mac", "i1")) /\ \A m \in Machines : WF_vars(Claim(m)) /\ WF_vars(Requeue(m)) /\ \A i \in Items : WF_vars(Recover(m, i))
Spec == Init /\ [][Next]_vars /\ Fair

OneMachinePerItem == \A i \in Items : Cardinality({m \in Machines : q[m][i] # "none"}) <= 1
OneThreadPerItem == \A i \in Items : threads[i] <= 1
CapHolds == \A m \in Machines : Cardinality(live[m]) <= Cap
HeldNeverStarted == ~startedHeld
PriorityOrder == ~jumped
FixCap == \A i \in Items : fixes[i] <= MaxFix
AutoMergeIsVerifiedAndAllowed == \A i \in Items : by[i] = "auto" => verifiedAtMerge[i] /\ ~reviewAtMerge[i]
WorkingHasThread == \A m \in Machines, i \in Items : q[m][i] \in {"working", "passed"} => threads[i] = 1
PrDoneOnlyWhenMerged == \A m \in Machines, i \in Items : q[m][i] = "done" /\ i \in pr => i \in merged
NoPrFreesItsSlot == (q["mac"]["i1"] = "working" /\ "i1" \notin pr) ~> (q["mac"]["i1"] # "working" \/ "i1" \in pr)
StartingResolves == \A m \in Machines, i \in Items : (q[m][i] = "starting") ~> (q[m][i] # "starting")

MCAccepts == ("mac" :> {"i1", "i3"} @@ "linux" :> {"i2"})
MCPrio == ("i1" :> 1 @@ "i2" :> 2 @@ "i3" :> 2)
====
