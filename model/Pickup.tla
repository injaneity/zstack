---- MODULE Pickup ----
EXTENDS Naturals, TLC

CONSTANTS Items, Passes, NoPass, Auto, Hand, MaxChat, Rules, Foreign, Workers
Active == {"collect", "prune", "delete", "remove", "drain", "pick", "report", "wake"}
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
  rows,
  worker,
  user,
  listed,
  vmlock,
  checked

vars == <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending, lock, pc, free, had,
          failed, woke, rows, worker, user, listed, vmlock, checked>>
PassVars == <<lock, pc, free, had, failed, woke, rows>>

Free == IF blind THEN 0 ELSE Sub(Auto, auto)
Eligible(i) == i \notin taken /\ blocker[i] = "none"
Handled(i) == \E r \in Rules \cup {"launch"} : <<i, r>> \in rows
News == unsent \/ (standing /\ ~seen)

Init == blocker \in [Items -> Rules \cup {"none"}] /\ taken = Foreign
  /\ auto = Sub(Auto, 1) /\ drained = Sub(Hand, 1) /\ chat = 0 /\ queued = FALSE /\ blind = FALSE /\ ended = FALSE
  /\ standing = FALSE /\ seen = FALSE /\ unsent = FALSE /\ pending = FALSE
  /\ lock = NoPass /\ pc = [p \in Passes |-> "off"] /\ free = 0 /\ had = FALSE /\ failed = FALSE /\ woke = FALSE /\ rows = {}
  /\ worker \in Workers /\ user \in (IF worker = "gone" THEN {"none"} ELSE {"none", "running"}) /\ listed = FALSE /\ vmlock = "none" /\ checked = FALSE

Changes(i, b) == lock = NoPass /\ blocker[i] # b /\ blocker' = [blocker EXCEPT ![i] = b]
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending, PassVars>>
RunEnds == ended' = TRUE
  /\ \/ auto > 0 /\ auto' = auto - 1 /\ UNCHANGED <<worker, user, listed, vmlock, checked, drained, chat>>
     \/ drained > 0 /\ drained' = drained - 1 /\ UNCHANGED <<worker, user, listed, vmlock, checked, auto, chat>>
     \/ chat > 0 /\ chat' = chat - 1 /\ UNCHANGED <<worker, user, listed, vmlock, checked, auto, drained>>
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, queued, blind, standing, seen, unsent, pending, PassVars>>
ChatQueues == ~queued /\ ~(lock # NoPass /\ pc[lock] = "drain") /\ queued' = TRUE
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, blind, ended, standing, seen, unsent, pending, PassVars>>
ChatStarts == chat < MaxChat /\ chat' = chat + 1
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, queued, blind, ended, standing, seen, unsent, pending, PassVars>>
HostFlips == lock = NoPass /\ blind' = ~blind
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, ended, standing, seen, unsent, pending, PassVars>>
StandingFlips == lock = NoPass /\ standing' = ~standing /\ seen' = (seen /\ standing')
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, blind, ended, unsent, pending, PassVars>>

Fire(p) == pc[p] = "off" /\ pc' = [pc EXCEPT ![p] = "trying"]
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, had, failed, woke, rows>>
Lock(p) == pc[p] = "trying" /\ lock = NoPass
  /\ lock' = p /\ pc' = [pc EXCEPT ![p] = "collect"] /\ rows' = {<<"pass", "start">>}
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending, free, had,
                 failed, woke>>
Locked(p) == pc[p] = "trying" /\ lock # NoPass /\ lock # p /\ pc' = [pc EXCEPT ![p] = "off"]
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, had, failed, woke, rows>>
Collect(p) == pc[p] = "collect" /\ lock = p /\ pc' = [pc EXCEPT ![p] = "prune"]
  /\ ended' = FALSE /\ unsent' = (unsent \/ ended) /\ pending' = (pending \/ ended)
  /\ rows' = (IF ended THEN rows \cup {<<"run", "collect">>} ELSE rows)
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, blind, standing, seen, lock, free, had, failed, woke>>
Others == <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending, lock, free, had,
            failed, woke, rows>>
Prunable == worker = "old" /\ user = "collected"
PruneList(p) == pc[p] = "prune" /\ lock = p /\ pc' = [pc EXCEPT ![p] = "delete"] /\ listed' = Prunable
  /\ UNCHANGED <<worker, user, vmlock, checked>> /\ UNCHANGED Others
PruneLocked(p) == pc[p] = "delete" /\ lock = p /\ (~listed \/ vmlock # "none") /\ pc' = [pc EXCEPT ![p] = "drain"]
  /\ listed' = FALSE /\ UNCHANGED <<worker, user, vmlock, checked>> /\ UNCHANGED Others
PruneCheck(p) == pc[p] = "delete" /\ lock = p /\ listed /\ vmlock = "none" /\ pc' = [pc EXCEPT ![p] = "remove"]
  /\ vmlock' = "prune" /\ checked' = (worker = "old") /\ UNCHANGED <<worker, user, listed>> /\ UNCHANGED Others
PruneDelete(p) == pc[p] = "remove" /\ lock = p /\ pc' = [pc EXCEPT ![p] = "drain"] /\ listed' = FALSE /\ checked' = FALSE
  /\ vmlock' = "none" /\ worker' = (IF checked THEN "gone" ELSE worker) /\ UNCHANGED user /\ UNCHANGED Others
E2eTakes == vmlock = "none" /\ vmlock' = "e2e" /\ UNCHANGED <<worker, user, listed, checked, pc>> /\ UNCHANGED Others
E2eEnds == vmlock = "e2e" /\ vmlock' = "none" /\ worker' = (IF worker = "running" THEN "old" ELSE worker)
  /\ UNCHANGED <<user, listed, checked, pc>> /\ UNCHANGED Others
WorkerBoots == worker = "old" /\ vmlock = "e2e" /\ worker' = "running" /\ UNCHANGED <<user, listed, vmlock, checked, pc>> /\ UNCHANGED Others
UserCollected == worker # "gone" /\ user = "running" /\ user' = "collected" /\ UNCHANGED <<worker, listed, vmlock, checked, pc>>
  /\ UNCHANGED Others
CanDrain == queued /\ ~blind /\ drained < Hand
Drain(p) == pc[p] = "drain" /\ lock = p /\ CanDrain /\ queued' = FALSE /\ drained' = drained + 1
  /\ rows' = rows \cup {<<"run", "launch">>}
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, chat, blind, ended, standing, seen, unsent, pending, lock, pc, free, had, failed, woke>>
DrainDone(p) == pc[p] = "drain" /\ lock = p /\ ~CanDrain
  /\ pc' = [pc EXCEPT ![p] = "pick"] /\ free' = Free
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, had, failed, woke, rows>>
Pick(p, i) == pc[p] = "pick" /\ lock = p /\ free > 0 /\ Eligible(i)
  /\ taken' = taken \cup {i} /\ auto' = auto + 1 /\ free' = free - 1
  /\ rows' = rows \cup {<<i, "launch">>}
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, drained, chat, queued, blind, ended, standing, seen, unsent, pending, lock, pc, had, failed, woke>>
Skip(p, i, r) == pc[p] = "pick" /\ lock = p /\ free > 0 /\ i \notin taken /\ ~Handled(i) /\ blocker[i] = r
  /\ rows' = rows \cup {<<i, r>>}
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, pc, free, had, failed, woke>>
PickDone(p) == pc[p] = "pick" /\ lock = p /\ (free = 0 \/ \A i \in Items \ taken : Handled(i))
  /\ pc' = [pc EXCEPT ![p] = "report"]
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, had, failed, woke, rows>>
Report(p) == pc[p] = "report" /\ lock = p /\ pc' = [pc EXCEPT ![p] = "wake"] /\ had' = News
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, failed, woke, rows>>
Kill(p) == pc[p] \in {"collect", "prune", "delete", "remove", "drain", "pick", "report"} /\ lock = p /\ listed' = FALSE
  /\ checked' = FALSE /\ vmlock' = (IF vmlock = "prune" THEN "none" ELSE vmlock)
  /\ pc' = [pc EXCEPT ![p] = "wake"] /\ failed' = TRUE /\ had' = News
  /\ UNCHANGED <<worker, user, blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, woke, rows>>
WakeOk(p) == pc[p] = "wake" /\ lock = p /\ (had \/ failed) /\ ~woke
  /\ woke' = TRUE /\ unsent' = FALSE /\ pending' = FALSE /\ seen' = (seen \/ (~failed /\ standing))
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, blind, ended, standing, lock, pc, free, had, failed, rows>>
WakeFails(p) == pc[p] = "wake" /\ lock = p /\ (had \/ failed) /\ ~woke
  /\ woke' = TRUE /\ unsent' = (unsent \/ failed)
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, pending, lock, pc, free, had,
                 failed, rows>>
End(p) == pc[p] = "wake" /\ lock = p /\ (woke \/ ~(had \/ failed))
  /\ pc' = [pc EXCEPT ![p] = "off"] /\ lock' = NoPass /\ rows' = {} /\ free' = 0 /\ had' = FALSE /\ failed' = FALSE /\ woke' = FALSE
  /\ UNCHANGED <<worker, user, listed, vmlock, checked, blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending>>

PassStep(p) == Lock(p) \/ Locked(p) \/ Collect(p) \/ PruneList(p) \/ PruneLocked(p) \/ PruneCheck(p) \/ PruneDelete(p) \/ Drain(p) \/ DrainDone(p) \/ PickDone(p) \/ Report(p) \/ End(p)
  \/ (\E i \in Items : Pick(p, i) \/ \E r \in Rules : Skip(p, i, r))
World == (\E i \in Items, b \in Rules \cup {"none"} : Changes(i, b))
  \/ RunEnds \/ ChatQueues \/ ChatStarts \/ HostFlips \/ StandingFlips
  \/ E2eTakes \/ E2eEnds \/ WorkerBoots \/ UserCollected
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
PruneIsSafe == [][worker # "gone" /\ worker' = "gone" => worker = "old" /\ user = "collected"]_vars
OldCollectedWorkerPruned == <>[](vmlock # "e2e") /\ <>[][\A p \in Passes : ~Kill(p)]_vars => (Prunable ~> worker = "gone")
EveryPassEnds == \A p \in Passes : pc[p] \in Active ~> pc[p] = "off"

ChartSteps == worker' = worker /\ user' = user /\ vmlock' = vmlock /\ blocker' = blocker /\ blind' = blind /\ queued' = queued /\ chat' = chat /\ standing' = standing
PassSym == Permutations(Passes)
TypeOK == worker \in {"old", "running", "gone"} /\ user \in {"none", "running", "collected"} /\ listed \in BOOLEAN
  /\ vmlock \in {"none", "e2e", "prune"} /\ checked \in BOOLEAN
  /\ taken \subseteq Items /\ lock \in Passes \cup {NoPass} /\ auto \in Nat /\ chat \in 0..MaxChat
  /\ pc \in [Passes -> {"off", "trying"} \cup Active] /\ free \in Nat
====
