---- MODULE Plan ----
EXTENDS Naturals, FiniteSets

CONSTANTS Items, Passes, None, Cap, MaxFix

VARIABLES q, threads, live, held, pc, offer, cur, stage, offeredHeld, attempts, fixLive, red

vars == <<q, threads, live, held, pc, offer, cur, stage, offeredHeld, attempts, fixLive, red>>
queue == <<q, threads, live, pc, offer, cur, stage, offeredHeld>>
fix == <<attempts, fixLive, red>>

TypeOK ==
  /\ q \in [Items -> {"none", "queued", "starting", "claimed", "done"}]
  /\ threads \in [Items -> 0..3]
  /\ live \subseteq Items
  /\ held \subseteq Items
  /\ pc \in [Passes -> {"idle", "launch"}]
  /\ offer \in [Passes -> SUBSET Items]
  /\ cur \in [Passes -> Items \cup {None}]
  /\ stage \in [Passes -> {"none", "started", "launched"}]
  /\ offeredHeld \in BOOLEAN
  /\ attempts \in 0..(MaxFix + 1)
  /\ fixLive \in BOOLEAN
  /\ red \in BOOLEAN

Init ==
  /\ q = [i \in Items |-> "none"] /\ threads = [i \in Items |-> 0] /\ live = {} /\ held = {}
  /\ pc = [p \in Passes |-> "idle"] /\ offer = [p \in Passes |-> {}] /\ cur = [p \in Passes |-> None]
  /\ stage = [p \in Passes |-> "none"] /\ offeredHeld = FALSE /\ attempts = 0 /\ fixLive = FALSE /\ red = FALSE

Ready(i) == q[i] \in {"none", "queued"} /\ i \notin held
Plan(p) == pc[p] = "idle"
  /\ offer' = [offer EXCEPT ![p] = {i \in Items : Ready(i)}]
  /\ q' = [i \in Items |-> IF Ready(i) THEN "queued" ELSE q[i]]
  /\ offeredHeld' = (offeredHeld \/ offer'[p] \cap held # {})
  /\ pc' = [pc EXCEPT ![p] = "launch"]
  /\ UNCHANGED <<threads, live, held, cur, stage, fix>>

Start(p, i) == pc[p] = "launch" /\ stage[p] = "none" /\ i \in offer[p]
  /\ q[i] = "queued"
  /\ q' = [q EXCEPT ![i] = "starting"] /\ cur' = [cur EXCEPT ![p] = i] /\ stage' = [stage EXCEPT ![p] = "started"]
  /\ UNCHANGED <<threads, live, held, pc, offer, offeredHeld, fix>>

Refused(p, i) == pc[p] = "launch" /\ stage[p] = "none" /\ i \in offer[p] /\ q[i] # "queued"
  /\ offer' = [offer EXCEPT ![p] = @ \ {i}]
  /\ UNCHANGED <<q, threads, live, held, pc, cur, stage, offeredHeld, fix>>

Launch(p) == stage[p] = "started"
  /\ Cardinality(live) < Cap
  /\ threads' = [threads EXCEPT ![cur[p]] = @ + 1] /\ live' = live \cup {cur[p]}
  /\ stage' = [stage EXCEPT ![p] = "launched"]
  /\ UNCHANGED <<q, held, pc, offer, cur, offeredHeld, fix>>

Requeue(p) == stage[p] = "started"
  /\ q' = [q EXCEPT ![cur[p]] = "queued"] /\ offer' = [offer EXCEPT ![p] = {}]
  /\ cur' = [cur EXCEPT ![p] = None] /\ stage' = [stage EXCEPT ![p] = "none"]
  /\ UNCHANGED <<threads, live, held, pc, offeredHeld, fix>>

Claim(p) == stage[p] = "launched"
  /\ q' = [q EXCEPT ![cur[p]] = "claimed"] /\ offer' = [offer EXCEPT ![p] = @ \ {cur[p]}]
  /\ cur' = [cur EXCEPT ![p] = None] /\ stage' = [stage EXCEPT ![p] = "none"]
  /\ UNCHANGED <<threads, live, held, pc, offeredHeld, fix>>

End(p) == pc[p] = "launch" /\ stage[p] = "none"
  /\ pc' = [pc EXCEPT ![p] = "idle"] /\ offer' = [offer EXCEPT ![p] = {}]
  /\ UNCHANGED <<q, threads, live, held, cur, stage, offeredHeld, fix>>

Crash(p) == pc[p] = "launch"
  /\ pc' = [pc EXCEPT ![p] = "idle"] /\ offer' = [offer EXCEPT ![p] = {}]
  /\ cur' = [cur EXCEPT ![p] = None] /\ stage' = [stage EXCEPT ![p] = "none"]
  /\ UNCHANGED <<q, threads, live, held, offeredHeld, fix>>

Stale(i) == q[i] = "starting" /\ \A p \in Passes : cur[p] # i
Recover(i) == Stale(i)
  /\ q' = [q EXCEPT ![i] = IF threads[i] > 0 THEN "claimed" ELSE "queued"]
  /\ UNCHANGED <<threads, live, held, pc, offer, cur, stage, offeredHeld, fix>>

ThreadEnds(i) == i \in live /\ live' = live \ {i}
  /\ UNCHANGED <<q, threads, held, pc, offer, cur, stage, offeredHeld, fix>>

Collect(i) == q[i] = "claimed" /\ i \notin live
  /\ q' = [q EXCEPT ![i] = "done"]
  /\ UNCHANGED <<threads, live, held, pc, offer, cur, stage, offeredHeld, fix>>

Hold(i) == i \notin held /\ held' = held \cup {i} /\ UNCHANGED <<queue, fix>>
Unhold(i) == i \in held /\ held' = held \ {i} /\ UNCHANGED <<queue, fix>>

TurnsRed == ~red /\ ~fixLive /\ red' = TRUE /\ UNCHANGED <<queue, held, attempts, fixLive>>
FixOffer == red /\ ~fixLive /\ attempts < MaxFix
  /\ attempts' = attempts + 1 /\ fixLive' = TRUE /\ UNCHANGED <<queue, held, red>>
FixEnds == fixLive /\ fixLive' = FALSE /\ red' \in BOOLEAN /\ UNCHANGED <<queue, held, attempts>>

Next ==
  \/ \E p \in Passes : Plan(p) \/ Launch(p) \/ Requeue(p) \/ Claim(p) \/ End(p) \/ Crash(p)
  \/ \E p \in Passes, i \in Items : Start(p, i) \/ Refused(p, i)
  \/ \E i \in Items : Recover(i) \/ ThreadEnds(i) \/ Collect(i) \/ Hold(i) \/ Unhold(i)
  \/ TurnsRed \/ FixOffer \/ FixEnds

Fair ==
  /\ \A i \in Items : WF_vars(Recover(i))
  /\ \A p \in Passes : WF_vars(Claim(p)) /\ WF_vars(Requeue(p))

Spec == Init /\ [][Next]_vars /\ Fair

OneThreadPerItem == \A i \in Items : threads[i] <= 1
CapHolds == Cardinality(live) <= Cap
HeldNeverOffered == ~offeredHeld
ClaimedHasThread == \A i \in Items : q[i] \in {"claimed", "done"} => threads[i] = 1
FixCap == attempts <= MaxFix
StartingResolves == \A i \in Items : (q[i] = "starting") ~> (q[i] # "starting")
====
