---- MODULE Hourly ----
EXTENDS Naturals

CONSTANTS Issues, Auto, Hand, Legacy, MaxQuick, MaxQueued

Rules == {"held", "pr", "overlap", "zane", "host"}
Seen == {"pr", "overlap", "host"}
Max(a, b) == IF a > b THEN a ELSE b
Sub(a, b) == IF a > b THEN a - b ELSE 0

VARIABLES
  todo,
  blocker,
  auto,
  peer,
  hand,
  legacy,
  quick,
  blind,
  pause,
  strayed,
  qhand,
  qauto,
  ended,
  dirty,
  fair,
  free,
  pc,
  rows

vars == <<todo, blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, ended, dirty, fair, free, pc, rows>>
World == <<todo, blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, ended, fair>>

SeenAuto == auto + (IF blind THEN 0 ELSE peer)
HandN == hand + legacy
CodeFree == Sub(Auto, SeenAuto)
KnownFree == IF blind THEN 0 ELSE CodeFree
HandWaits(unknown) == unknown \/ HandN >= Hand
AutoWaits(unknown) == unknown \/ auto + peer >= Auto
WaitIsFair(unknown, full) == fair' = (fair /\ (unknown \/ full))
Free == KnownFree
Eligible(i) == i \in todo /\ blocker[i] = "none"
CouldPick == Free > 0 /\ \E i \in Issues : Eligible(i)
Handled(i) == \E r \in Rules \cup {"launch"} : <<i, r>> \in rows

Init == todo \in SUBSET Issues /\ blocker \in [Issues -> Rules \cup {"none"}]
  /\ auto = 0 /\ peer = 0 /\ hand = 0 /\ legacy \in {0, Legacy} /\ quick = 0 /\ blind = FALSE /\ pause \in BOOLEAN
  /\ strayed = FALSE /\ qhand = 0 /\ qauto = 0
  /\ ended = FALSE /\ dirty = TRUE /\ fair = TRUE /\ free = 0 /\ pc = "idle" /\ rows = {}

Changes(i, b) == pc = "idle" /\ blocker[i] # b /\ blocker' = [blocker EXCEPT ![i] = b]
  /\ dirty' = (dirty \/ blocker[i] \in Seen \/ b \in Seen)
  /\ UNCHANGED <<todo, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, ended, fair, free, pc, rows>>
Files(i) == pc = "idle" /\ i \notin todo /\ todo' = todo \cup {i} /\ dirty' = TRUE
  /\ UNCHANGED <<blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, ended, fair, free, pc, rows>>
Moves(i) == pc = "idle" /\ i \in todo /\ todo' = todo \ {i} /\ dirty' = TRUE
  /\ UNCHANGED <<blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, ended, fair, free, pc, rows>>
RunEnds == pc # "drain" /\ ended' = TRUE /\ dirty' = TRUE
  /\ \/ auto > 0 /\ auto' = auto - 1 /\ UNCHANGED <<peer, hand, legacy, quick>>
     \/ peer > 0 /\ peer' = peer - 1 /\ UNCHANGED <<auto, hand, legacy, quick>>
     \/ hand > 0 /\ hand' = hand - 1 /\ UNCHANGED <<auto, peer, legacy, quick>>
     \/ legacy > 0 /\ legacy' = legacy - 1 /\ UNCHANGED <<auto, peer, hand, quick>>
     \/ quick > 0 /\ quick' = quick - 1 /\ UNCHANGED <<auto, peer, hand, legacy>>
  /\ UNCHANGED <<todo, blocker, blind, pause, strayed, qhand, qauto, fair, free, pc, rows>>
ChatQueues == pc # "drain" /\ qhand + qauto < MaxQueued
  /\ \/ qhand' = qhand + 1 /\ qauto' = qauto
     \/ qauto' = qauto + 1 /\ qhand' = qhand
  /\ dirty' = TRUE
  /\ UNCHANGED <<todo, blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, ended, fair, free, pc, rows>>
HandStarts(onVm) == pc # "drain"
  /\ \/ ~HandWaits(onVm \/ blind) /\ hand' = hand + 1 /\ UNCHANGED <<qhand, fair>>
     \/ HandWaits(onVm \/ blind) /\ qhand + qauto < MaxQueued /\ qhand' = qhand + 1 /\ UNCHANGED hand
        /\ WaitIsFair(onVm \/ blind, HandN >= Hand)
  /\ dirty' = TRUE
  /\ UNCHANGED <<todo, blocker, auto, peer, legacy, quick, blind, pause, strayed, qauto, ended, free, pc, rows>>
ChatStartsNow == pc # "drain" /\ quick < MaxQuick /\ quick' = quick + 1 /\ dirty' = TRUE
  /\ UNCHANGED <<todo, blocker, auto, peer, hand, legacy, blind, pause, strayed, qhand, qauto, ended, fair, free, pc, rows>>
PeerFlips == pc \in {"idle", "collect"} /\ blind' = ~blind /\ dirty' = TRUE
  /\ UNCHANGED <<pause, strayed, todo, blocker, auto, peer, hand, legacy, quick, qhand, qauto, ended, fair, free, pc, rows>>
PauseFlips == pc \in {"idle", "pick"} /\ pause' = ~pause
  /\ UNCHANGED <<todo, blocker, auto, peer, hand, legacy, quick, blind, strayed, qhand, qauto, ended, dirty, fair, free, pc, rows>>

Acts == ended \/ qhand + qauto > 0 \/ blind \/ (Free > 0 /\ todo # {})
PassStarts == pc = "idle"
  /\ pc' = (IF ~dirty /\ ~Acts THEN "skipped" ELSE "collect")
  /\ rows' = {<<"pass", "candidates">>} /\ free' = Free
  /\ UNCHANGED <<World, dirty>>
Collects == pc = "collect" /\ pc' = "drain" /\ ended' = FALSE
  /\ rows' = (IF ended THEN rows \cup {<<"run", "collect">>} ELSE rows)
  /\ UNCHANGED <<todo, blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, dirty, fair, free>>
CanDrainHand == qhand > 0 /\ ~HandWaits(blind)
CanDrainAuto(there) == qauto > 0 /\ ~AutoWaits(blind) /\ (there => ~pause)
Drains == pc = "drain" /\ rows' = rows \cup {<<"run", "launch">>}
  /\ \/ CanDrainHand /\ qhand' = qhand - 1 /\ hand' = hand + 1 /\ UNCHANGED <<qauto, auto, peer, strayed>>
     \/ \E there \in BOOLEAN : CanDrainAuto(there) /\ qauto' = qauto - 1 /\ UNCHANGED <<qhand, hand>>
        /\ peer' = (IF there THEN peer + 1 ELSE peer) /\ auto' = (IF there THEN auto ELSE auto + 1)
        /\ strayed' = (strayed \/ (there /\ pause))
  /\ UNCHANGED <<todo, blocker, legacy, quick, blind, pause, ended, dirty, fair, free, pc>>
DrainDone == pc = "drain" /\ ~CanDrainHand /\ ~CanDrainAuto(FALSE) /\ pc' = "pick" /\ free' = Free
  /\ rows' = (IF qhand + qauto > 0 THEN rows \cup {<<"run", "wait">>} ELSE rows)
  /\ fair' = (fair /\ (qhand > 0 => (blind \/ HandN >= Hand)) /\ (qauto > 0 => (blind \/ auto + peer >= Auto)))
  /\ UNCHANGED <<todo, blocker, auto, peer, hand, legacy, quick, blind, pause, strayed, qhand, qauto, ended, dirty>>
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
Done == pc = "end" /\ pc' = "idle" /\ dirty' = FALSE /\ rows' = {} /\ free' = 0
  /\ UNCHANGED World
StaysSilent == pc = "skipped" /\ pc' = "idle" /\ rows' = {} /\ free' = 0
  /\ UNCHANGED <<World, dirty>>

PassSteps == PassStarts \/ Collects \/ Drains \/ DrainDone \/ PickDone \/ Done \/ StaysSilent
  \/ (\E i \in Issues : (\E there \in BOOLEAN : Picks(i, there)) \/ \E r \in Rules : Skips(i, r))
Next == PassSteps
  \/ (\E i \in Issues, b \in Rules \cup {"none"} : Changes(i, b)) \/ (\E i \in Issues : Files(i) \/ Moves(i))
  \/ RunEnds \/ ChatQueues \/ (\E onVm \in BOOLEAN : HandStarts(onVm)) \/ ChatStartsNow \/ PeerFlips \/ PauseFlips

Spec == Init /\ [][Next]_vars /\ WF_vars(PassSteps)

NoSilentPassWhenCouldPick == pc = "skipped" => ~(free > 0 /\ \E i \in Issues : Eligible(i))
PicksOrSkipsEachTodo == pc = "end" /\ free > 0 =>
  /\ \A i \in todo : \E r \in Rules : <<i, r>> \in rows /\ blocker[i] = r
  /\ \A x \in rows : x[1] \in Issues \cup {"pass", "run"}
AutoCap == auto + peer <= Auto
HandCap == HandN <= Max(Hand, legacy) /\ (legacy >= Hand => hand = 0)
QuickNotCounted == fair
HeldOrZaneNeverPicked == \A i \in Issues : <<i, "launch">> \in rows => blocker[i] \notin {"held", "zane"}
EveryPassEnds == [](pc \notin {"idle"} => <>(pc = "idle"))
NoAutoOnPausedPeer == ~strayed
EveryPassLogs == pc \in {"skipped", "end"} => <<"pass", "candidates">> \in rows

TypeOK == todo \subseteq Issues /\ free \in 0..Auto /\ auto \in Nat /\ peer \in Nat /\ hand \in Nat /\ legacy \in 0..Legacy
  /\ quick \in 0..MaxQuick /\ qhand + qauto \in 0..MaxQueued /\ fair \in BOOLEAN
  /\ pc \in {"idle", "skipped", "collect", "drain", "pick", "end"}

Bound == qhand + qauto <= 1

ChartView == <<pc, CouldPick, dirty, ended \/ qhand + qauto > 0 \/ blind, \E x \in rows : x[1] = "todo">>
====
