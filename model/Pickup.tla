---- MODULE Pickup ----
EXTENDS Naturals, TLC

CONSTANTS Items, Passes, NoPass, Auto, Hand, MaxChat, Rules, Foreign
Active == {"collect", "drain", "pick", "report", "wake"}
Min(a, b) == IF a < b THEN a ELSE b
Sub(a, b) == IF a > b THEN a - b ELSE 0

VARIABLES
  blocker,
  taken,
  auto,
  drained,
  chat,
  queued,
  blind,
  ended,
  standing,
  seen,
  unsent,
  pending,
  lock,
  pc,
  free,
  had,
  failed,
  woke,
  reads,
  rows

vars == <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending, lock, pc, free, had,
          failed, woke, reads, rows>>
PassVars == <<lock, pc, free, had, failed, woke, reads, rows>>

Free == IF blind THEN 0 ELSE Sub(Auto, auto)
Eligible(i) == i \notin taken /\ blocker[i] = "none"
Handled(i) == \E r \in Rules \cup {"launch"} : <<i, r>> \in rows
News == unsent \/ (standing /\ ~seen)

Init == blocker \in [Items -> Rules \cup {"none"}] /\ taken = Foreign
  /\ auto = Sub(Auto, 1) /\ drained = Sub(Hand, 1) /\ chat = 0 /\ queued = FALSE /\ blind = FALSE /\ ended = FALSE
  /\ standing = FALSE /\ seen = FALSE /\ unsent = FALSE /\ pending = FALSE
  /\ lock = NoPass /\ pc = [p \in Passes |-> "off"] /\ free = 0 /\ had = FALSE /\ failed = FALSE /\ woke = FALSE /\ reads = 0 /\ rows = {}

Changes(i, b) == lock = NoPass /\ blocker[i] # b /\ blocker' = [blocker EXCEPT ![i] = b]
  /\ UNCHANGED <<taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending, PassVars>>
RunEnds == ended' = TRUE
  /\ \/ auto > 0 /\ auto' = auto - 1 /\ UNCHANGED <<drained, chat>>
     \/ drained > 0 /\ drained' = drained - 1 /\ UNCHANGED <<auto, chat>>
     \/ chat > 0 /\ chat' = chat - 1 /\ UNCHANGED <<auto, drained>>
  /\ UNCHANGED <<blocker, taken, queued, blind, standing, seen, unsent, pending, PassVars>>
ChatQueues == ~queued /\ ~(lock # NoPass /\ pc[lock] = "drain") /\ queued' = TRUE
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, blind, ended, standing, seen, unsent, pending, PassVars>>
ChatStarts == chat < MaxChat /\ chat' = chat + 1
  /\ UNCHANGED <<blocker, taken, auto, drained, queued, blind, ended, standing, seen, unsent, pending, PassVars>>
HostFlips == lock = NoPass /\ blind' = ~blind
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, ended, standing, seen, unsent, pending, PassVars>>
StandingFlips == lock = NoPass /\ standing' = ~standing /\ seen' = (seen /\ standing')
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, unsent, pending, PassVars>>

Fire(p) == pc[p] = "off" /\ pc' = [pc EXCEPT ![p] = "trying"]
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, had, failed, woke, rows, reads>>
Lock(p) == pc[p] = "trying" /\ lock = NoPass
  /\ lock' = p /\ pc' = [pc EXCEPT ![p] = "collect"] /\ rows' = {<<"pass", "start">>}
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending, free, had,
                 failed, woke, reads>>
Locked(p) == pc[p] = "trying" /\ lock # NoPass /\ lock # p /\ pc' = [pc EXCEPT ![p] = "off"]
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, had, failed, woke, rows, reads>>
Collect(p) == pc[p] = "collect" /\ lock = p /\ pc' = [pc EXCEPT ![p] = "drain"] /\ reads' = reads + 1
  /\ ended' = FALSE /\ unsent' = (unsent \/ ended) /\ pending' = (pending \/ ended)
  /\ rows' = (IF ended THEN rows \cup {<<"run", "collect">>} ELSE rows)
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, standing, seen, lock, free, had, failed, woke>>
CanDrain == queued /\ ~blind /\ drained < Hand
Drain(p) == pc[p] = "drain" /\ lock = p /\ CanDrain /\ queued' = FALSE /\ drained' = drained + 1
  /\ rows' = rows \cup {<<"run", "launch">>}
  /\ UNCHANGED <<blocker, taken, auto, chat, blind, ended, standing, seen, unsent, pending, lock, pc, free, had, failed, woke, reads>>
DrainDone(p) == pc[p] = "drain" /\ lock = p /\ ~CanDrain
  /\ pc' = [pc EXCEPT ![p] = "pick"] /\ free' = Free
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, had, failed, woke, rows, reads>>
Pick(p, i) == pc[p] = "pick" /\ lock = p /\ free > 0 /\ Eligible(i)
  /\ taken' = taken \cup {i} /\ auto' = auto + 1 /\ free' = free - 1
  /\ rows' = rows \cup {<<i, "launch">>}
  /\ UNCHANGED <<blocker, drained, chat, queued, blind, ended, standing, seen, unsent, pending, lock, pc, had, failed, woke, reads>>
Skip(p, i, r) == pc[p] = "pick" /\ lock = p /\ free > 0 /\ i \notin taken /\ ~Handled(i) /\ blocker[i] = r
  /\ rows' = rows \cup {<<i, r>>}
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, pc, free, had, failed, woke, reads>>
PickDone(p) == pc[p] = "pick" /\ lock = p /\ (free = 0 \/ \A i \in Items \ taken : Handled(i))
  /\ pc' = [pc EXCEPT ![p] = "report"]
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, had, failed, woke, rows, reads>>
Report(p) == pc[p] = "report" /\ lock = p /\ pc' = [pc EXCEPT ![p] = "wake"] /\ had' = News
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, failed, woke, rows, reads>>
Kill(p) == pc[p] \in {"collect", "drain", "pick", "report"} /\ lock = p
  /\ pc' = [pc EXCEPT ![p] = "wake"] /\ failed' = TRUE /\ had' = News
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, woke, rows, reads>>
WakeOk(p) == pc[p] = "wake" /\ lock = p /\ (had \/ failed) /\ ~woke
  /\ woke' = TRUE /\ unsent' = FALSE /\ pending' = FALSE /\ seen' = (seen \/ (~failed /\ standing))
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, lock, pc, free, had, failed, rows, reads>>
WakeFails(p) == pc[p] = "wake" /\ lock = p /\ (had \/ failed) /\ ~woke
  /\ woke' = TRUE /\ unsent' = (unsent \/ failed)
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, pending, lock, pc, free, had,
                 failed, rows, reads>>
End(p) == pc[p] = "wake" /\ lock = p /\ (woke \/ ~(had \/ failed))
  /\ pc' = [pc EXCEPT ![p] = "off"] /\ lock' = NoPass /\ rows' = {} /\ free' = 0 /\ had' = FALSE /\ failed' = FALSE /\ woke' = FALSE /\ reads' = 0
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending>>

PassStep(p) == Lock(p) \/ Locked(p) \/ Collect(p) \/ Drain(p) \/ DrainDone(p) \/ PickDone(p) \/ Report(p) \/ End(p)
  \/ (\E i \in Items : Pick(p, i) \/ \E r \in Rules : Skip(p, i, r))
World == (\E i \in Items, b \in Rules \cup {"none"} : Changes(i, b))
  \/ RunEnds \/ ChatQueues \/ ChatStarts \/ HostFlips \/ StandingFlips
Next == World \/ \E p \in Passes : Fire(p) \/ PassStep(p) \/ Kill(p) \/ WakeOk(p) \/ WakeFails(p)
Spec == Init /\ [][Next]_vars /\ \A p \in Passes : WF_vars(Fire(p)) /\ WF_vars(PassStep(p)) /\ WF_vars(WakeOk(p) \/ WakeFails(p))
  /\ SF_vars(WakeOk(p))

NoOverlap == \A p, q \in Passes : pc[p] \in Active /\ pc[q] \in Active => p = q
NoSilentPassWhenCouldPick == \A p \in Passes : pc[p] = "report" /\ free > 0 =>
  \A i \in Items \ taken : <<i, blocker[i]>> \in rows /\ blocker[i] \in Rules
CapsHold == auto <= Auto /\ drained <= Hand
HeldNeverPicked == \A i \in Items : <<i, "launch">> \in rows => blocker[i] \notin {"held", "zane"}
OutputIsNew == [][\A p \in Passes : pc[p] # "wake" /\ pc'[p] = "wake" => (had' <=> (unsent \/ (standing /\ ~seen)))]_vars
WakeIffOutputOrFailure == [][\A p \in Passes : pc[p] \in Active /\ pc'[p] = "off" => (woke <=> (had \/ failed))]_vars
NoResultLost == pending => unsent
ResultReachesChat == pending ~> ~pending
ForeignNeverLaunched == \A i \in Foreign : <<i, "launch">> \notin rows
MergeJobReadOnce == reads <= 1
EveryPassEnds == \A p \in Passes : pc[p] \in Active ~> pc[p] = "off"

ChartSteps == blocker' = blocker /\ blind' = blind /\ queued' = queued /\ chat' = chat /\ standing' = standing
PassSym == Permutations(Passes)
TypeOK == taken \subseteq Items /\ lock \in Passes \cup {NoPass} /\ auto \in Nat /\ chat \in 0..MaxChat
  /\ pc \in [Passes -> {"off", "trying"} \cup Active] /\ free \in Nat /\ reads \in Nat
====
