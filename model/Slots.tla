---- MODULE Slots ----
\* The slot pools (Zane 12 Nov 12:47 SGT: "we should have 3 slots for autopickup and up to 5 hand started work"):
\* config/policy.toml [slots] auto and hand, counted apart, across both hosts. Checked in CI by tests/slots-model.sh: TLC passes
\* Slots.cfg, and every mutant in model/slots_mutants.py fails on the rule it names (two put back the 7-total/5-auto caps).
\*
\* What it holds the code to:
\*  - A run is tagged with its pool when it is queued or starts (run.json pool, from the brief's `source:` line): auto for
\*    an hourly pick, quick for the quick-check lane, hand for the rest. A running run with no tag, one started before
\*    the pools, counts as hand (legacy here). Nothing stops a running run to make room.
\*  - Auto runs never exceed Auto. Hand runs never exceed Hand, except by the untagged runs already in flight: while
\*    those alone fill the hand pool, no hand run starts.
\*  - The quick-check lane is in neither pool: a quick launch always starts, and nothing it holds makes another wait.
\*  - A pool launch counts under the mini's slots.lock (bin/pi-run, bin/hourly launch, and the drain for its whole pass)
\*    over every host's runs. A launcher that cannot read every host (the VM cannot read the mini; the mini while the VM
\*    is unreachable) does not know the count, so the run waits in its queue, as the drain already did.
\*  - The hourly pass: no silent pass while an auto slot is free and a Todo is eligible; a pass with a free auto slot
\*    gives every Todo a row (launched, waited, or skipped by one of the five rules); a held Todo is never picked.
\*
\* Runs are counts per pool. The mini counts every host or none, so where a run sits changes no count it reads, and a
\* blind drain or pick (model/Hourly.tla's case) is not modelled here: a blind pass waits, by the code and by Acts.
\* Left out: the run lifecycle (bin/PiRun.tla), 429s and collects, queue order, the peer's floor and parity.
EXTENDS Naturals

CONSTANTS Issues, Blockers, Auto, Hand, Legacy, MaxQuick

Hosts == {"mini", "vm"}
Pools == {"auto", "hand", "quick"}
None == "none"
Rules == {"held", "pr", "overlap", "zane", "host"}   \* playbook step 5: the only reasons a Todo is skipped
Seen == {"pr", "overlap", "host"}                    \* what bin/hourly's fingerprint sees of them
Max(a, b) == IF a > b THEN a ELSE b
Sub(a, b) == IF a > b THEN a - b ELSE 0

VARIABLES
  run,      \* run[p]: running runs tagged with pool p, on either host
  legacy,   \* running runs with no pool tag (started before the pools): hand
  queue,    \* the pool of the one run waiting in a queue, or None (one at a time keeps TLC small)
  blind,    \* the mini cannot reach the VM
  lock,     \* the mini's slots.lock: "free", or held by the "drain" for its whole pass
  seen,     \* the drain's own count of each pool, read when it takes the lock and raised with each start
  fair,     \* every launch so far that waited had cause: its own pool full by the true count, or a host unknown
  todo,     \* the Linear Todo ids the chat passes in
  blocker,  \* per issue: "none", or the one skip rule that holds for it
  dirty,    \* something the fingerprint sees changed since the last `bin/hourly done`
  free,     \* the free auto slots the pass read (its slots: line), less each pick
  pc,       \* the pass: "idle" "skipped" "drain" "pick" "end"
  rows      \* this pass's pickup.log rows: <<item, action or rule>>

vars == <<run, legacy, queue, blind, lock, seen, fair, todo, blocker, dirty, free, pc, rows>>
Slots == <<run, legacy, queue, blind, lock, seen, fair>>

Count(p) == IF p = "hand" THEN run.hand + legacy ELSE run[p]
Cap(p) == IF p = "auto" THEN Auto ELSE IF p = "hand" THEN Hand ELSE MaxQuick
\* A launcher on h knows every host's runs only on the mini while it reaches the VM.
Unknown(h) == h = "vm" \/ blind
\* bin/hourly wait_reason(): a quick run never waits; a pool run waits while a host's runs are unknown or its pool is full.
Waits(h, p) == p # "quick" /\ (Unknown(h) \/ Count(p) >= Cap(p))
\* A launch that waited: fair only when it is a pool run whose pool, quick runs not in it, is full, or a host is unknown.
WaitIsFair(h, p) == fair' = (fair /\ p # "quick" /\ (Unknown(h) \/ Count(p) >= Cap(p)))
Start(p) == run' = [run EXCEPT ![p] = @ + 1]
AutoFree == IF blind THEN 0 ELSE Sub(Auto, run.auto)
Eligible(i) == i \in todo /\ blocker[i] = "none"
Handled(i) == \E r \in Rules \cup {"launch", "wait"} : <<i, r>> \in rows

Init ==
  /\ run = [p \in Pools |-> 0]
  /\ legacy \in {0, Legacy}   \* today: hand runs already in flight with no tag
  /\ queue = None /\ blind = FALSE /\ lock = "free" /\ seen = [p \in Pools |-> 0] /\ fair = TRUE
  /\ todo \in SUBSET Issues /\ blocker \in [Issues -> Blockers \cup {"none"}]
  /\ dirty = TRUE /\ free = 0 /\ pc = "idle" /\ rows = {}

\* ---------------- launches outside the pass ----------------
\* The chat's `pi-run <id> <role> <brief>` on host h. A quick brief starts now (only the cap on TLC's states bounds it).
\* A pool brief counts under the mini's slots.lock: it starts when its pool has room, else it is queued and pi-run says
\* why. A pool launch on the VM always queues: the VM cannot read the mini. The drain reads its list once, so a run
\* queued while it drains is the next pass's.
DirectStart(h, p) ==
  /\ (p # "quick" /\ h = "mini") => lock = "free"
  /\ \/ Waits(h, p) /\ pc # "drain" /\ queue = None /\ queue' = p /\ WaitIsFair(h, p) /\ UNCHANGED run
     \/ ~Waits(h, p) /\ Count(p) < Cap(p) /\ Start(p) /\ UNCHANGED <<queue, fair>>
  /\ dirty' = TRUE
  /\ UNCHANGED <<legacy, blind, lock, seen, todo, blocker, free, pc, rows>>
\* bin/queue: a run waits for the drain.
ChatQueues(p) == pc # "drain" /\ queue = None /\ queue' = p /\ dirty' = TRUE
  /\ UNCHANGED <<run, legacy, blind, lock, seen, fair, todo, blocker, free, pc, rows>>
\* A running run ends, at any time, and its slot frees. Nothing stops one to make room.
RunEnds(p) == run[p] > 0 /\ run' = [run EXCEPT ![p] = @ - 1] /\ dirty' = TRUE
  /\ UNCHANGED <<legacy, queue, blind, lock, seen, fair, todo, blocker, free, pc, rows>>
LegacyEnds == legacy > 0 /\ legacy' = legacy - 1 /\ dirty' = TRUE
  /\ UNCHANGED <<run, queue, blind, lock, seen, fair, todo, blocker, free, pc, rows>>
PeerFlips == pc = "idle" /\ blind' = ~blind /\ dirty' = TRUE
  /\ UNCHANGED <<run, legacy, queue, lock, seen, fair, todo, blocker, free, pc, rows>>
\* Between passes a blocker comes or goes, and Todos are filed or leave.
Changes(i, b) == pc = "idle" /\ blocker[i] # b /\ blocker' = [blocker EXCEPT ![i] = b]
  /\ dirty' = (dirty \/ blocker[i] \in Seen \/ b \in Seen)
  /\ UNCHANGED <<Slots, todo, free, pc, rows>>
Files(i) == pc = "idle" /\ i \notin todo /\ todo' = todo \cup {i} /\ dirty' = TRUE
  /\ UNCHANGED <<Slots, blocker, free, pc, rows>>
Moves(i) == pc = "idle" /\ i \in todo /\ todo' = todo \ {i} /\ dirty' = TRUE
  /\ UNCHANGED <<Slots, blocker, free, pc, rows>>

\* ---------------- the hourly pass ----------------
\* Step 0: the fingerprint skip. Never with a host unknown, a queued run, or a free auto slot while Todos wait.
Acts == blind \/ queue # None \/ (AutoFree > 0 /\ todo # {})
PassStarts == pc = "idle" /\ free' = AutoFree /\ rows' = {<<"pass", "candidates">>}
  /\ \/ ~dirty /\ ~Acts /\ pc' = "skipped" /\ UNCHANGED <<lock, seen>>
     \/ (dirty \/ Acts) /\ pc' = "drain" /\ lock = "free" /\ lock' = "drain" /\ seen' = [p \in Pools |-> Count(p)]
  /\ UNCHANGED <<run, legacy, queue, blind, fair, todo, blocker, dirty>>
\* Step 4, `bin/hourly queue`, holding slots.lock: the queued run starts while its pool has room by the drain's count.
\* Every host's runs must be known.
CanDrain == queue # None /\ ~blind /\ seen[queue] < Cap(queue)
Drains == pc = "drain" /\ CanDrain
  /\ queue' = None /\ Start(queue) /\ seen' = [seen EXCEPT ![queue] = @ + 1]
  /\ rows' = rows \cup {<<"run", "launch">>}
  /\ UNCHANGED <<legacy, blind, lock, fair, todo, blocker, dirty, free, pc>>
DrainDone == pc = "drain" /\ ~CanDrain
  /\ pc' = "pick" /\ lock' = "free" /\ free' = AutoFree /\ seen' = [p \in Pools |-> 0]
  /\ rows' = (IF queue # None THEN rows \cup {<<"run", "wait">>} ELSE rows)
  /\ UNCHANGED <<run, legacy, queue, blind, fair, todo, blocker, dirty>>
\* Step 5, while the slots: line shows an auto slot: `bin/hourly launch <id> <host>` counts again under slots.lock and
\* starts the pick when the auto pool still has room, else logs a wait.
Picks(i) == pc = "pick" /\ Eligible(i) /\ ~Handled(i) /\ free > 0 /\ lock = "free"
  /\ free' = free - 1
  /\ \/ Waits("mini", "auto") /\ rows' = rows \cup {<<i, "wait">>} /\ WaitIsFair("mini", "auto") /\ UNCHANGED <<run, todo>>
     \/ ~Waits("mini", "auto") /\ rows' = rows \cup {<<i, "launch">>} /\ Start("auto") /\ todo' = todo \ {i}
        /\ UNCHANGED fair
  /\ UNCHANGED <<legacy, queue, blind, lock, seen, blocker, dirty, pc>>
Skips(i, r) == pc = "pick" /\ i \in todo /\ ~Handled(i) /\ free > 0 /\ r \in Rules /\ blocker[i] = r
  /\ rows' = rows \cup {<<i, r>>}
  /\ UNCHANGED <<Slots, todo, blocker, dirty, free, pc>>
PickDone == pc = "pick" /\ (free = 0 \/ \A i \in todo : Handled(i)) /\ pc' = "end"
  /\ UNCHANGED <<Slots, todo, blocker, dirty, free, rows>>
Done == pc = "end" /\ pc' = "idle" /\ dirty' = FALSE /\ rows' = {} /\ free' = 0
  /\ UNCHANGED <<Slots, todo, blocker>>
StaysSilent == pc = "skipped" /\ pc' = "idle" /\ rows' = {} /\ free' = 0
  /\ UNCHANGED <<Slots, todo, blocker, dirty>>

PassSteps == PassStarts \/ Drains \/ DrainDone \/ PickDone \/ Done \/ StaysSilent
  \/ (\E i \in Issues : Picks(i) \/ \E r \in Rules : Skips(i, r))
Next == PassSteps
  \/ (\E h \in Hosts, p \in Pools : DirectStart(h, p)) \/ (\E p \in Pools : ChatQueues(p) \/ RunEnds(p))
  \/ LegacyEnds \/ PeerFlips
  \/ (\E i \in Issues, b \in Blockers \cup {"none"} : Changes(i, b)) \/ (\E i \in Issues : Files(i) \/ Moves(i))
Spec == Init /\ [][Next]_vars /\ WF_vars(PassSteps)

\* ---------------- the rules ----------------
\* Auto never exceeds 3.
AutoCap == run.auto <= Auto
\* Hand never exceeds 5. The untagged runs already in flight may hold more, and then no hand run starts beside them.
HandCap == run.hand + legacy <= Max(Hand, legacy) /\ (legacy >= Hand => run.hand = 0)
\* The quick-check lane is not counted: a quick launch never waits, and a launch waits only when its own pool, counted
\* without quick runs, is full, or a host's runs are unknown.
QuickNotCounted == fair
\* With an auto slot free and an eligible Todo, a pass never ends silent.
NoSilentPassWhenCouldPick == pc = "skipped" => ~(free > 0 /\ \E i \in Issues : Eligible(i))
\* A pass that ends with an auto slot free gave every Todo its own row, and a skip names a rule that holds for it.
EveryTodoGetsARow == pc = "end" /\ free > 0 => \A i \in todo : Handled(i) /\ \A r \in Rules : <<i, r>> \in rows => blocker[i] = r
\* A held (or Zane-waiting) Todo is never picked.
HeldNeverPicked == \A i \in Issues : <<i, "launch">> \in rows => blocker[i] \notin {"held", "zane"}
\* A pass that starts ends, and the drain gives slots.lock back.
EveryPassEnds == [](pc # "idle" => <>(pc = "idle" /\ lock = "free"))

TypeOK == run \in [Pools -> Nat] /\ queue \in Pools \cup {None} /\ fair \in BOOLEAN /\ lock \in {"free", "drain"}
  /\ pc \in {"idle", "skipped", "drain", "pick", "end"} /\ free \in 0..Auto
====
