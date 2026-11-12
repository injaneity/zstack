---- MODULE Hourly ----
\* The hourly pickup (skills/onigiri-mode/playbooks/hourly.md, run by bin/hourly): one pass after another, with the
\* world changing around it. Checked in CI by tests/hourly-model.sh: TLC passes Hourly.cfg (the rules at the real caps)
\* and HourlyLive.cfg (every pass ends), model/Hourly.mmd is the chart HourlyChart.cfg draws, and every mutant in
\* model/hourly_mutants.py fails on HourlySmall.cfg with the rule it names (three of them put back zstack main 4e08648,
\* before the pickup fix, and two the shared 7-total/5-auto caps, before the slot split).
\* Runs start and end, and the chat queues, at any time inside a pass but the drain (see RunEnds). The peer can drop
\* between step 0 and the drain. The pass reads its slots twice, at step 0
\* and after the drain, and then counts its own picks, as bin/hourly's slots: and queue lines do.
\*
\* What it holds the pass to (Zane 12 Nov: "is the issue pickup working?", and the statechart brief):
\*  - No silent pass when it could pick: a fingerprint skip never happens while a free auto slot and an eligible Todo exist.
\*  - A pass with a free auto slot ends with every Todo launched, or skipped by its own row naming one of the five rules:
\*    held, owned by an open PR, overlaps a live run, waits on Zane, the launch host can't do it.
\*  - Two pools, counted apart (Zane 12 Nov 12:47 SGT: "we should have 3 slots for autopickup and up to 5 hand started
\*    work"; config/policy.toml [slots]): auto runs stay within Auto, and hand runs within Hand, but for the untagged runs
\*    already in flight when the pools came in (legacy, counted as hand): while those fill the pool, no hand run starts.
\*    A running run is never stopped to make room. The quick-check lane is in neither pool: it always starts, and a launch
\*    waits only when its own pool, counted without quick runs, is full, or a host's runs are unknown.
\*  - A held or Zane-waiting Todo is never picked.
\*  - No pick starts on a peer with an auto pause (Zane 12 Nov 19:01 SGT, "yeah pause it": no new auto runs on the VM
\*    while a bench runs there): bin/hourly paused(), from $ONIGIRI_ROOT/auto-pause. The pause is set or lifts (the run it
\*    waits on ends) at any time; a pick then launches here instead. Hand runs never pause.
\*  - Every pass writes a row, and every pass that starts ends.
\*
\* "Free" is what the pass can know: bin/hourly auto_free() over the runs it can read. A peer it cannot reach has unknown
\* runs, so nothing is known free while one is unreachable (the drain's rule, `runs unknown`).
\* A hand launch outside the pass (`pi-run <id>` on either host) counts under the mini's slots.lock, which the drain holds,
\* and is queued instead when its pool is full or a host is unknown; the VM cannot read the mini, so one there queues.
\* `bin/hourly launch` counts again under that lock before each pick starts. Each action reads the counts as they are,
\* which slots.lock makes true of the code: no launch counts or starts while another holds it.
\*
\* Left out: priority and age order (the pass may take Todos in any order, a superset of oldest first), the batch, the
\* merge job, filing and review, disk, 429s. Runs are counts per kind, not ids.
EXTENDS Naturals

CONSTANTS Issues, Auto, Hand, Legacy, MaxQuick, MaxQueued

Rules == {"held", "pr", "overlap", "zane", "host"}   \* playbook step 5: the only reasons a Todo is skipped
\* What bin/hourly's fingerprint() sees: run states, PRs, host facts. Not the holds file, not Zane's answers.
Seen == {"pr", "overlap", "host"}
Max(a, b) == IF a > b THEN a ELSE b
Sub(a, b) == IF a > b THEN a - b ELSE 0

VARIABLES
  todo,     \* the Linear Todo ids the chat passes with --linear
  blocker,  \* per issue: "none", or the one rule that holds for it now
  auto,     \* running auto-launches on the pass's host
  peer,     \* running auto-launches on the peer
  hand,     \* running hand runs (tagged hand: chat-queued and drained, or `pi-run <id>` started)
  legacy,   \* running runs with no pool tag, started before the pools: counted as hand
  quick,    \* running quick-check runs (`source: quick`, role research): in no pool
  blind,    \* the peer is unreachable: its runs are unknown this pass
  pause,    \* the peer has an auto pause: no new auto run may start there
  strayed,  \* ghost: an auto run started on the peer while it was paused
  qhand,    \* queued runs with a hand brief, waiting for the drain
  qauto,    \* queued runs with an auto brief (`new-brief ... auto`)
  ended,    \* a run exited and is not yet collected
  dirty,    \* something the fingerprint sees changed since the last `bin/hourly done`
  fair,     \* every launch that waited so far had cause: its own pool full, quick runs not counted, or a host unknown
  free,     \* the auto slots the pass counts as free: step 0's slots: line, then the queue's "slots now" line,
            \* less each pick. Runs that start or end later in the pass do not change it.
  pc,       \* "idle" between passes; "skipped"; "collect" "drain" "pick" "end" inside a pass
  rows      \* this pass's pickup.log rows: <<item, action or rule>>

vars == <<todo, blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, ended, dirty, fair, free, pc, rows>>
World == <<todo, blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, ended, fair>>

\* bin/hourly slots(): each pool counted apart over every run it can read; quick runs in none.
SeenAuto == auto + (IF blind THEN 0 ELSE peer)
HandN == hand + legacy
CodeFree == Sub(Auto, SeenAuto)
\* What the pass can know is free: nothing while a peer's runs are unknown.
KnownFree == IF blind THEN 0 ELSE CodeFree
\* bin/pi-run pool_wait(): a pool launch waits while its pool is full or a host is unknown (`unknown`); quick never waits.
HandWaits(unknown) == unknown \/ HandN >= Hand
AutoWaits(unknown) == unknown \/ auto + peer >= Auto
\* A launch that waited is fair only when its own pool, by the true count without quick runs, is full, or a host unknown.
WaitIsFair(unknown, full) == fair' = (fair /\ (unknown \/ full))
\* bin/hourly auto_free(): the auto count on the slots: line, which acts and step 5 go by.
Free == KnownFree
Eligible(i) == i \in todo /\ blocker[i] = "none"
CouldPick == Free > 0 /\ \E i \in Issues : Eligible(i)
Handled(i) == \E r \in Rules \cup {"launch"} : <<i, r>> \in rows

Init == todo \in SUBSET Issues /\ blocker \in [Issues -> Rules \cup {"none"}]
  /\ auto = 0 /\ peer = 0 /\ hand = 0 /\ legacy \in {0, Legacy} /\ quick = 0 /\ blind = FALSE /\ pause \in BOOLEAN
  /\ strayed = FALSE /\ qhand = 0 /\ qauto = 0
  /\ ended = FALSE /\ dirty = TRUE /\ fair = TRUE /\ free = 0 /\ pc = "idle" /\ rows = {}

\* ---------------- the world moves ----------------
\* Between passes: a hold row is added or retired, Zane answers or is asked, a PR opens or closes, an overlapping run
\* starts or ends, the launch host gains or loses what the issue needs. Only some of these change the fingerprint.
\* The pass reads them once, at step 0, so a change inside a pass is the next pass's.
Changes(i, b) == pc = "idle" /\ blocker[i] # b /\ blocker' = [blocker EXCEPT ![i] = b]
  /\ dirty' = (dirty \/ blocker[i] \in Seen \/ b \in Seen)
  /\ UNCHANGED <<todo, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, ended, fair, free, pc, rows>>
\* The chat files an issue into Todo, or one leaves Todo (Done, Canceled, moved by hand).
Files(i) == pc = "idle" /\ i \notin todo /\ todo' = todo \cup {i} /\ dirty' = TRUE
  /\ UNCHANGED <<blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, ended, fair, free, pc, rows>>
Moves(i) == pc = "idle" /\ i \in todo /\ todo' = todo \ {i} /\ dirty' = TRUE
  /\ UNCHANGED <<blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, ended, fair, free, pc, rows>>
\* bin/hourly queue reads every host's runs and the queue once, under slots.lock, then counts its own launches. No pool
\* launch starts while it holds the lock, and a run that ends or is queued meanwhile is the next pass's, as step 5 goes
\* by the drain's `queue:` line. So the world moves at any time except during the drain.
\* A running run exits: its slot frees and it waits for a collect. Nothing stops a running run to make room.
RunEnds == pc # "drain" /\ ended' = TRUE /\ dirty' = TRUE
  /\ \/ auto > 0 /\ auto' = auto - 1 /\ UNCHANGED <<peer, hand, legacy, quick>>
     \/ peer > 0 /\ peer' = peer - 1 /\ UNCHANGED <<auto, hand, legacy, quick>>
     \/ hand > 0 /\ hand' = hand - 1 /\ UNCHANGED <<auto, peer, legacy, quick>>
     \/ legacy > 0 /\ legacy' = legacy - 1 /\ UNCHANGED <<auto, peer, hand, quick>>
     \/ quick > 0 /\ quick' = quick - 1 /\ UNCHANGED <<auto, peer, hand, legacy>>
  /\ UNCHANGED <<todo, blocker, blind, pause, strayed, qhand, qauto, fair, free, pc, rows>>
\* The chat queues a run (bin/queue): a hand brief, or an auto one. MaxQueued bounds the queue for TLC.
ChatQueues == pc # "drain" /\ qhand + qauto < MaxQueued
  /\ \/ qhand' = qhand + 1 /\ qauto' = qauto
     \/ qauto' = qauto + 1 /\ qhand' = qhand
  /\ dirty' = TRUE
  /\ UNCHANGED <<todo, blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, ended, fair, free, pc, rows>>
\* The chat's `pi-run <id> <role> <brief>` for a hand brief, on the mini (`onVm` false) or the VM. It counts the pools
\* under slots.lock: it starts when the hand pool has room, else it is queued, and pi-run says why. The VM cannot read
\* the mini, so a launch there always queues. Untagged runs count, so while they fill the pool every hand launch queues.
HandStarts(onVm) == pc # "drain"
  /\ \/ ~HandWaits(onVm \/ blind) /\ hand' = hand + 1 /\ UNCHANGED <<qhand, fair>>
     \/ HandWaits(onVm \/ blind) /\ qhand + qauto < MaxQueued /\ qhand' = qhand + 1 /\ UNCHANGED hand
        /\ WaitIsFair(onVm \/ blind, HandN >= Hand)
  /\ dirty' = TRUE
  /\ UNCHANGED <<todo, blocker, auto, peer, legacy, quick, blind, pause, strayed, qauto, ended, free, pc, rows>>
\* The chat starts a quick-check run: in no pool, so it never waits.
ChatStartsNow == pc # "drain" /\ quick < MaxQuick /\ quick' = quick + 1 /\ dirty' = TRUE
  /\ UNCHANGED <<todo, blocker, auto, peer, hand, legacy, blind, pause, strayed, qhand, qauto, ended, fair, free, pc, rows>>
\* The peer stops or starts answering (`bin/hourly facts` over ssh): an error in the fingerprint. Step 0 and the drain
\* each read it, so it can also change between them.
PeerFlips == pc \in {"idle", "collect"} /\ blind' = ~blind /\ dirty' = TRUE
  /\ UNCHANGED <<pause, strayed, todo, blocker, auto, peer, hand, legacy, quick, qhand, qauto, ended, fair, free, pc, rows>>
\* An auto pause on the peer is set ($ONIGIRI_ROOT/auto-pause gains a row) or lifts (the row goes, or the run it waits
\* on ends). The picks and the drain read it: `bin/hourly launch` reads it again before each pick starts, and the drain
\* before each queued run. A row written while a launch is already deciding applies from the next launch decision, so
\* the model flips it between passes and between picks, and a drain step reads it atomically. Not in the fingerprint: a pause changes
\* where a pick goes, not whether there is one.
PauseFlips == pc \in {"idle", "pick"} /\ pause' = ~pause
  /\ UNCHANGED <<todo, blocker, auto, peer, hand, legacy, quick, blind, strayed, qhand, qauto, ended, dirty, fair, free, pc, rows>>

\* ---------------- the pass ----------------
\* Step 0, `bin/hourly --linear <ids>`: the fingerprint skip. A pass never skips with an ended or queued run, an
\* unreachable host (an error), or a free auto slot while the Todo list is not empty. Every pass writes a `pass` row.
Acts == ended \/ qhand + qauto > 0 \/ blind \/ (Free > 0 /\ todo # {})
PassStarts == pc = "idle"
  /\ pc' = (IF ~dirty /\ ~Acts THEN "skipped" ELSE "collect")
  /\ rows' = {<<"pass", "candidates">>} /\ free' = Free
  /\ UNCHANGED <<World, dirty>>
\* Step 1: collect every ended run, one `collect` row each.
Collects == pc = "collect" /\ pc' = "drain" /\ ended' = FALSE
  /\ rows' = (IF ended THEN rows \cup {<<"run", "collect">>} ELSE rows)
  /\ UNCHANGED <<todo, blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, dirty, fair, free>>
\* Step 4, `bin/hourly queue`: every queued run in turn, each started while its own pool has room and every host's runs
\* are known; one that cannot start waits, and the next is tried. A queued auto run may be in either host's queue
\* (`there`: the peer's), and one in a paused peer's queue waits.
CanDrainHand == qhand > 0 /\ ~HandWaits(blind)
CanDrainAuto(there) == qauto > 0 /\ ~AutoWaits(blind) /\ (there => ~pause)
Drains == pc = "drain" /\ rows' = rows \cup {<<"run", "launch">>}
  /\ \/ CanDrainHand /\ qhand' = qhand - 1 /\ hand' = hand + 1 /\ UNCHANGED <<qauto, auto, peer, strayed>>
     \/ \E there \in BOOLEAN : CanDrainAuto(there) /\ qauto' = qauto - 1 /\ UNCHANGED <<qhand, hand>>
        /\ peer' = (IF there THEN peer + 1 ELSE peer) /\ auto' = (IF there THEN auto ELSE auto + 1)
        /\ strayed' = (strayed \/ (there /\ pause))
  /\ UNCHANGED <<todo, blocker, legacy, quick, blind, pause, ended, dirty, fair, free, pc>>
\* The queue ends with its `queue: ... auto free <n>` line, which step 5 goes by. A run left queued waited fairly only
\* when its pool is full or a host is unknown.
DrainDone == pc = "drain" /\ ~CanDrainHand /\ ~CanDrainAuto(FALSE) /\ pc' = "pick" /\ free' = Free
  /\ rows' = (IF qhand + qauto > 0 THEN rows \cup {<<"run", "wait">>} ELSE rows)
  /\ fair' = (fair /\ (qhand > 0 => (blind \/ HandN >= Hand)) /\ (qauto > 0 => (blind \/ auto + peer >= Auto)))
  /\ UNCHANGED <<todo, blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, ended, dirty>>
\* Step 5: while auto slots are free, take each Todo: `bin/hourly launch` it here or on the peer, or skip it with the
\* one rule that holds for it, while the slots: line shows an auto slot free.
Picks(i, there) == pc = "pick" /\ Eligible(i) /\ free > 0 /\ (there => ~blind /\ ~pause)
  /\ strayed' = (strayed \/ (there /\ pause))
  /\ todo' = todo \ {i} /\ rows' = rows \cup {<<i, "launch">>} /\ free' = free - 1
  /\ peer' = (IF there THEN peer + 1 ELSE peer) /\ auto' = (IF there THEN auto ELSE auto + 1)
  /\ UNCHANGED <<blocker, hand, legacy, quick, blind, pause, qhand, qauto, ended, dirty, fair, pc>>
Skips(i, r) == pc = "pick" /\ i \in todo /\ ~Handled(i) /\ free > 0 /\ r \in Rules /\ blocker[i] = r
  /\ rows' = rows \cup {<<i, r>>}
  /\ UNCHANGED <<World, dirty, free, pc>>
PickDone == pc = "pick" /\ (free = 0 \/ \A i \in todo : Handled(i)) /\ pc' = "end"
  /\ UNCHANGED <<World, dirty, free, rows>>
\* `bin/hourly done` stores the fingerprint the pass ended on.
Done == pc = "end" /\ pc' = "idle" /\ dirty' = FALSE /\ rows' = {} /\ free' = 0
  /\ UNCHANGED World
StaysSilent == pc = "skipped" /\ pc' = "idle" /\ rows' = {} /\ free' = 0
  /\ UNCHANGED <<World, dirty>>

PassSteps == PassStarts \/ Collects \/ Drains \/ DrainDone \/ PickDone \/ Done \/ StaysSilent
  \/ (\E i \in Issues : (\E there \in BOOLEAN : Picks(i, there)) \/ \E r \in Rules : Skips(i, r))
Next == PassSteps
  \/ (\E i \in Issues, b \in Rules \cup {"none"} : Changes(i, b)) \/ (\E i \in Issues : Files(i) \/ Moves(i))
  \/ RunEnds \/ ChatQueues \/ (\E onVm \in BOOLEAN : HandStarts(onVm)) \/ ChatStartsNow \/ PeerFlips \/ PauseFlips

\* The hourly runs a pass, and a pass runs to its end. Nothing is fair about the world.
Spec == Init /\ [][Next]_vars /\ WF_vars(PassSteps)

\* ---------------- the rules ----------------
\* "No silent pass when it could pick": by the free count the pass read at step 0.
NoSilentPassWhenCouldPick == pc = "skipped" => ~(free > 0 /\ \E i \in Issues : Eligible(i))
\* "If auto slots are free and an eligible issue exists, the pass picks or logs a per-issue skip with an allowed rule":
\* a pass that ends with a free auto slot has launched or skipped every Todo, each skip its own row naming a rule that
\* holds, and no row for a group of Todos.
PicksOrSkipsEachTodo == pc = "end" /\ free > 0 =>
  /\ \A i \in todo : \E r \in Rules : <<i, r>> \in rows /\ blocker[i] = r
  /\ \A x \in rows : x[1] \in Issues \cup {"pass", "run"}
\* "Auto never exceeds 3": every auto run on either host.
AutoCap == auto + peer <= Auto
\* "Hand never exceeds 5": only the untagged runs already in flight may hold more, and no hand run starts beside them.
HandCap == HandN <= Max(Hand, legacy) /\ (legacy >= Hand => hand = 0)
\* "The quick-check lane isn't counted": a launch waits only when its own pool, quick runs not in it, is full, or a host
\* is unknown; a quick-check run never waits (ChatStartsNow has no slot check).
QuickNotCounted == fair
\* "A held or Zane-waiting issue is never picked." Picks is the only writer of <<i, "launch">>, and no blocker moves
\* inside a pass.
HeldOrZaneNeverPicked == \A i \in Issues : <<i, "launch">> \in rows => blocker[i] \notin {"held", "zane"}
\* A pass that starts ends, so the pass-end rules above are reached: no pass hangs between its steps.
EveryPassEnds == [](pc \notin {"idle"} => <>(pc = "idle"))
\* "Pause it": no auto run starts on the peer while it has an auto pause, picked or drained.
NoAutoOnPausedPeer == ~strayed
\* Every pass leaves its `pass` row.
EveryPassLogs == pc \in {"skipped", "end"} => <<"pass", "candidates">> \in rows

TypeOK == todo \subseteq Issues /\ free \in 0..Auto /\ auto \in Nat /\ peer \in Nat /\ hand \in Nat /\ legacy \in 0..Legacy
  /\ quick \in 0..MaxQuick /\ qhand + qauto \in 0..MaxQueued /\ fair \in BOOLEAN
  /\ pc \in {"idle", "skipped", "collect", "drain", "pick", "end"}

\* Hourly.cfg's bound: one queued run at a time. The hand pool still fills (Legacy runs, or Hand starts).
Bound == qhand + qauto <= 1

\* For the chart only (HourlyChart.cfg): what the reader sees of a state.
ChartView == <<pc, CouldPick, dirty, ended \/ qhand + qauto > 0 \/ blind, \E x \in rows : x[1] = "todo">>
====
