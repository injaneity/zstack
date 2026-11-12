---- MODULE Hourly ----
\* The hourly pickup (skills/onigiri-mode/playbooks/hourly.md, run by bin/hourly): one pass after another, with the
\* world changing around it. Checked in CI by tests/hourly-model.sh: TLC passes Hourly.cfg (the rules at the real caps)
\* and HourlyLive.cfg (every pass ends), model/Hourly.mmd is the chart HourlyChart.cfg draws, every mutant in
\* model/hourly_mutants.py fails on HourlySmall.cfg with the rule it names (three of them put back zstack main 4e08648,
\* before the pickup fix), and QuickLane.cfg (a probe for an open question) still breaks.
\* Runs start and end, and the chat queues, at any time inside a pass but the drain (see RunEnds). The pass reads its slots twice, at step 0
\* and after the drain, and then counts its own picks, as bin/hourly's slots: and queue lines do.
\*
\* What it holds the pass to (Zane 12 Nov: "is the issue pickup working?", and the statechart brief):
\*  - No silent pass when it could pick: a fingerprint skip never happens while a free auto slot and an eligible Todo exist.
\*  - A pass with a free auto slot ends with every Todo launched, or skipped by its own row naming one of the five rules:
\*    held, owned by an open PR, overlaps a live run, waits on Zane, the launch host can't do it.
\*  - The caps are never exceeded: auto-launches stay within auto 5, auto-launches and drained runs within total 7
\*    (config/slots.json). Runs the chat starts itself with `pi-run <id>` (the quick-check lane) are outside the cap.
\*  - A held or Zane-waiting Todo is never picked.
\*  - Every pass writes a row, and every pass that starts ends.
\*
\* "Free" is what the pass can know: bin/hourly auto_free() over the runs it can read. A peer it cannot reach has unknown
\* runs, so nothing is known free while one is unreachable (the drain's rule, `runs unknown`).
\* bin/hourly slots() counts every running run toward the total, quick-check runs too. QuickLane.cfg shows what that
\* costs if they are outside the cap (an open question for Zane, not a rule here).
\*
\* Left out: priority and age order (the pass may take Todos in any order, a superset of oldest first), the batch, the
\* merge job, filing and review, disk, 429s. Runs are counts per kind, not ids.
EXTENDS Naturals

CONSTANTS Issues, Total, Auto, MaxQuick

Rules == {"held", "pr", "overlap", "zane", "host"}   \* playbook step 5: the only reasons a Todo is skipped
\* What bin/hourly's fingerprint() sees: run states, PRs, host facts. Not the holds file, not Zane's answers.
Seen == {"pr", "overlap", "host"}
Min(a, b) == IF a < b THEN a ELSE b
Sub(a, b) == IF a > b THEN a - b ELSE 0

VARIABLES
  todo,     \* the Linear Todo ids the chat passes with --linear
  blocker,  \* per issue: "none", or the one rule that holds for it now
  auto,     \* running auto-launches on the pass's host
  peer,     \* running auto-launches on the peer
  chat,     \* running chat-queued runs, started by the drain
  quick,    \* running runs the chat started itself (the quick-check lane)
  blind,    \* the peer is unreachable: its runs are unknown this pass
  queued,   \* the queued run waiting for the drain: "none", or its brief's source "chat" or "auto"
  ended,    \* a run exited and is not yet collected
  dirty,    \* something the fingerprint sees changed since the last `bin/hourly done`
  free,     \* the auto slots the pass counts as free: step 0's slots: line, then the queue's "slots now" line,
            \* less each pick. Runs that start or end later in the pass do not change it.
  pc,       \* "idle" between passes; "skipped"; "collect" "drain" "pick" "end" inside a pass
  rows      \* this pass's pickup.log rows: <<item, action or rule>>

vars == <<todo, blocker, auto, peer, chat, quick, blind, queued, ended, dirty, free, pc, rows>>
World == <<todo, blocker, auto, peer, chat, quick, blind, queued, ended>>

\* bin/hourly slots() and auto_free(): every run it can read counts toward the total, quick-check runs too.
SeenAuto == auto + (IF blind THEN 0 ELSE peer)
SeenRunning == SeenAuto + chat + quick
CodeFree == Min(Sub(Total, SeenRunning), Sub(Auto, SeenAuto))
\* What the pass can know is free: nothing while a peer's runs are unknown.
KnownFree == IF blind THEN 0 ELSE CodeFree
\* bin/hourly auto_free(): the auto count on the slots: line, which acts and step 5 go by.
Free == KnownFree
Eligible(i) == i \in todo /\ blocker[i] = "none"
CouldPick == Free > 0 /\ \E i \in Issues : Eligible(i)
Handled(i) == \E r \in Rules \cup {"launch"} : <<i, r>> \in rows

Init == todo \in SUBSET Issues /\ blocker \in [Issues -> Rules \cup {"none"}]
  /\ auto = 0 /\ peer = 0 /\ chat = 0 /\ quick = 0 /\ blind = FALSE /\ queued = "none" /\ ended = FALSE
  /\ dirty = TRUE /\ free = 0 /\ pc = "idle" /\ rows = {}

\* ---------------- the world moves ----------------
\* Between passes: a hold row is added or retired, Zane answers or is asked, a PR opens or closes, an overlapping run
\* starts or ends, the launch host gains or loses what the issue needs. Only some of these change the fingerprint.
\* The pass reads them once, at step 0, so a change inside a pass is the next pass's.
Changes(i, b) == pc = "idle" /\ blocker[i] # b /\ blocker' = [blocker EXCEPT ![i] = b]
  /\ dirty' = (dirty \/ blocker[i] \in Seen \/ b \in Seen)
  /\ UNCHANGED <<todo, auto, peer, chat, quick, blind, queued, ended, free, pc, rows>>
\* The chat files an issue into Todo, or one leaves Todo (Done, Canceled, moved by hand).
Files(i) == pc = "idle" /\ i \notin todo /\ todo' = todo \cup {i} /\ dirty' = TRUE
  /\ UNCHANGED <<blocker, auto, peer, chat, quick, blind, queued, ended, free, pc, rows>>
Moves(i) == pc = "idle" /\ i \in todo /\ todo' = todo \ {i} /\ dirty' = TRUE
  /\ UNCHANGED <<blocker, auto, peer, chat, quick, blind, queued, ended, free, pc, rows>>
\* bin/hourly queue reads every host's runs and the queue once, then counts its own launches. A run that starts, ends or is
\* queued while it drains is invisible to it and to step 5, which goes by the drain's "slots now" line: the same as that
\* step coming right after the drain. So the world moves at any time except during the drain.
\* A running run exits: its slot frees and it waits for a collect.
RunEnds == pc # "drain" /\ ended' = TRUE /\ dirty' = TRUE
  /\ \/ auto > 0 /\ auto' = auto - 1 /\ UNCHANGED <<peer, chat, quick>>
     \/ peer > 0 /\ peer' = peer - 1 /\ UNCHANGED <<auto, chat, quick>>
     \/ chat > 0 /\ chat' = chat - 1 /\ UNCHANGED <<auto, peer, quick>>
     \/ quick > 0 /\ quick' = quick - 1 /\ UNCHANGED <<auto, peer, chat>>
  /\ UNCHANGED <<todo, blocker, blind, queued, free, pc, rows>>
\* The chat queues a run (bin/queue): a chat brief, or an auto one (`new-brief ... auto`).
ChatQueues == pc # "drain" /\ queued = "none" /\ queued' \in {"chat", "auto"} /\ dirty' = TRUE
  /\ UNCHANGED <<todo, blocker, auto, peer, chat, quick, blind, ended, free, pc, rows>>
\* The chat starts a run itself, now (the quick-check lane): no slot check.
ChatStartsNow == pc # "drain" /\ quick < MaxQuick /\ quick' = quick + 1 /\ dirty' = TRUE
  /\ UNCHANGED <<todo, blocker, auto, peer, chat, blind, queued, ended, free, pc, rows>>
\* The peer stops or starts answering (`bin/hourly facts` over ssh): an error in the fingerprint.
PeerFlips == pc = "idle" /\ blind' = ~blind /\ dirty' = TRUE
  /\ UNCHANGED <<todo, blocker, auto, peer, chat, quick, queued, ended, free, pc, rows>>

\* ---------------- the pass ----------------
\* Step 0, `bin/hourly --linear <ids>`: the fingerprint skip. A pass never skips with an ended or queued run, an
\* unreachable host (an error), or a free auto slot while the Todo list is not empty. Every pass writes a `pass` row.
Acts == ended \/ queued # "none" \/ blind \/ (Free > 0 /\ todo # {})
PassStarts == pc = "idle"
  /\ pc' = (IF ~dirty /\ ~Acts THEN "skipped" ELSE "collect")
  /\ rows' = {<<"pass", "candidates">>} /\ free' = Free
  /\ UNCHANGED <<World, dirty>>
\* Step 1: collect every ended run, one `collect` row each.
Collects == pc = "collect" /\ pc' = "drain" /\ ended' = FALSE
  /\ rows' = (IF ended THEN rows \cup {<<"run", "collect">>} ELSE rows)
  /\ UNCHANGED <<todo, blocker, auto, peer, chat, quick, blind, queued, dirty, free>>
\* Step 4, `bin/hourly queue`: queued runs first, while the total allows (and the auto cap, for an auto brief) and every
\* host's runs are known.
CanDrain == queued # "none" /\ ~blind /\ SeenRunning < Total /\ (queued = "auto" => SeenAuto < Auto)
Drains == pc = "drain" /\ CanDrain
  /\ queued' = "none" /\ rows' = rows \cup {<<"run", "launch">>}
  /\ chat' = (IF queued = "chat" THEN chat + 1 ELSE chat) /\ auto' = (IF queued = "auto" THEN auto + 1 ELSE auto)
  /\ UNCHANGED <<todo, blocker, peer, quick, blind, ended, dirty, free, pc>>
\* The queue ends with its "slots now" line, which step 5 goes by.
DrainDone == pc = "drain" /\ ~CanDrain /\ pc' = "pick" /\ free' = Free
  /\ rows' = (IF queued # "none" THEN rows \cup {<<"run", "wait">>} ELSE rows)
  /\ UNCHANGED <<World, dirty>>
\* Step 5: while auto slots are free, take each Todo: launch it here or on the peer, or skip it with the one rule that
\* holds for it, while the slots: line shows an auto slot free.
Picks(i, there) == pc = "pick" /\ Eligible(i) /\ free > 0 /\ (there => ~blind)
  /\ todo' = todo \ {i} /\ rows' = rows \cup {<<i, "launch">>} /\ free' = free - 1
  /\ peer' = (IF there THEN peer + 1 ELSE peer) /\ auto' = (IF there THEN auto ELSE auto + 1)
  /\ UNCHANGED <<blocker, chat, quick, blind, queued, ended, dirty, pc>>
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
  \/ RunEnds \/ ChatQueues \/ ChatStartsNow \/ PeerFlips

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
\* "Caps are never exceeded": every auto-launch on either host within auto, and with the drained runs within total.
CapsHold == auto + peer <= Auto /\ auto + peer + chat <= Total
\* "A held or Zane-waiting issue is never picked." Picks is the only writer of <<i, "launch">>, and no blocker moves
\* inside a pass.
HeldOrZaneNeverPicked == \A i \in Issues : <<i, "launch">> \in rows => blocker[i] \notin {"held", "zane"}
\* A pass that starts ends, so the pass-end rules above are reached: no pass hangs between its steps.
EveryPassEnds == [](pc \notin {"idle"} => <>(pc = "idle"))
\* Every pass leaves its `pass` row.
EveryPassLogs == pc \in {"skipped", "end"} => <<"pass", "candidates">> \in rows

\* A probe (QuickLane.cfg), expected to break: with the quick-check lane outside the cap, a skip while chat-started
\* runs fill the total would be a pass that could pick.
FreeWithoutQuick == IF blind THEN 0 ELSE Min(Sub(Total, SeenAuto + chat), Sub(Auto, SeenAuto))
QuickOutsideCap == pc = "skipped" /\ quick > 0 => ~(FreeWithoutQuick > 0 /\ \E i \in Issues : Eligible(i))

TypeOK == todo \subseteq Issues /\ free \in 0..Auto /\ auto \in Nat /\ peer \in Nat /\ chat \in Nat /\ quick \in 0..MaxQuick
  /\ queued \in {"none", "chat", "auto"} /\ pc \in {"idle", "skipped", "collect", "drain", "pick", "end"}

\* Hourly.cfg's bound: two drained runs at once. With five auto-launches and two quick-check runs they still fill the
\* total, so a full total with auto slots left is reached.
Bound == chat <= 2

\* For the chart only (HourlyChart.cfg): what the reader sees of a state.
ChartView == <<pc, CouldPick, dirty, ended \/ queued # "none" \/ blind, \E x \in rows : x[1] = "todo">>
====
