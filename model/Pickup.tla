---- MODULE Pickup ----
\* The scheduled pickup pass, bin/pickup, run by a launchd StartInterval job on the mini (hosts/mini/install-pickup.sh)
\* or by hand. It takes its work from GitHub (trycua/cua issues assigned to injaneity, PRs requesting its review, our PRs
\* the merge job leaves to a session), and wakes the chat through hosts/vm/wake.py only when it has something to say.
\* Checked in CI by tests/pickup-model.sh: TLC passes Pickup.cfg, model/Pickup.mmd is the chart PickupChart.cfg draws, and every mutant in
\* model/pickup_mutants.py fails on the rule it names.
\*
\* The rules (Zane 12 Nov, the pickup-timer-onhost brief):
\*  - No overlapping passes: one pass at a time holds the lock; a pass that finds it held exits and does nothing.
\*  - No silent pass when it could pick: a pass that ends with an auto slot free has launched or skipped (one row each,
\*    naming one of the five rules) every candidate.
\*  - The pools are never exceeded (config/policy.toml [slots], Zane 12 Nov): the pass's launches stay within the auto pool,
\*    and the runs it drains from the queue within the hand pool. Quick runs the chat starts itself are in no pool.
\*  - A held or Zane-waiting item is never picked.
\*  - A pass wakes the chat exactly when it has output (a collected run's result, a needs-Zane item not woken before, a
\*    result an earlier wake failed to deliver) or it failed (its deadline killed it). A silent pass never wakes.
\*  - A collected result is never lost: it stays in the outbox until a wake delivered it.
\*
\* The pass reads the world once (GitHub, the holds file, both hosts' runs), so the world's blockers do not change while
\* a pass holds the lock; a change then is the next pass's. Runs end and the chat starts runs at any time.
\* Left out: the order of candidates (the pass may take them in any order, a superset of its real order), the launch
\* host, disk, the merge job, 429s, red-PR reopens (bin/hourly queue's, modelled in bin/PiRun.tla). Runs are counts.
EXTENDS Naturals, TLC

CONSTANTS Items, Passes, NoPass, Auto, Hand, MaxChat, Rules, Foreign
\* Foreign: items whose id names a review dir that holds another launcher's brief and no run (a Linear-era CUA-<n> brief
\* is review/cua-<n>, the id issue #<n> gets). bin/hourly foreign_briefs lists them with the host's ids, so they start taken.
\* Rules: playbook step 5's reasons a candidate is skipped, and no others ("held", "pr", "overlap", "zane", "host").
\* A config may check a subset that keeps "held" and "zane": the others act alike.
Active == {"collect", "drain", "pick", "report", "wake"}   \* a pass past the lock
Min(a, b) == IF a < b THEN a ELSE b
Sub(a, b) == IF a > b THEN a - b ELSE 0

VARIABLES
  blocker,   \* per item: "none", or the one rule that holds for it now
  taken,     \* items a run was launched for (a review dir or a launch row on a host), or Foreign: no longer candidates
  auto,      \* running auto-pool runs, on either host
  drained,   \* running hand-pool runs (chat-queued ones the drain started, or the chat's own)
  chat,      \* running quick runs the chat started itself (in no pool)
  queued,    \* a chat-queued run waits for the drain
  blind,     \* a host is unreachable: its runs are unknown
  ended,     \* a run ended and is not collected
  standing,  \* a needs-Zane item exists now (a deep PR green at its head, a host not at main)
  seen,      \* the standing item was delivered by an earlier wake (pickup/state.json seen)
  unsent,    \* the outbox (pickup/state.json unsent): output collected or found that no wake delivered yet
  pending,   \* ghost: a collected result the chat has not received
  lock,      \* the pass holding pickup.lock, or NoPass
  pc,        \* per pass: "off", "trying" (launchd or a hand run started it), then Active, then "off"
  free,      \* the lock holder's pass: the auto slots it counts as free, less its picks
  had,       \* the lock holder's pass: it had output when it reported
  failed,    \* the lock holder's pass: its deadline killed it
  woke,      \* the lock holder's pass: it sent a wake
  rows       \* this pass's pickup.log rows: <<item, "launch" or rule>>

vars == <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending, lock, pc, free, had,
          failed, woke, rows>>
PassVars == <<lock, pc, free, had, failed, woke, rows>>

\* bin/hourly auto_free(): the auto pool's room by the runs the pass can read, and none while a host's runs are unknown.
Free == IF blind THEN 0 ELSE Sub(Auto, auto)
Eligible(i) == i \notin taken /\ blocker[i] = "none"
Handled(i) == \E r \in Rules \cup {"launch"} : <<i, r>> \in rows
News == unsent \/ (standing /\ ~seen)

\* Runs from earlier passes are running at the start, one short of each pool, so the drain and the picks meet both edges.
Init == blocker \in [Items -> Rules \cup {"none"}] /\ taken = Foreign
  /\ auto = Sub(Auto, 1) /\ drained = Sub(Hand, 1) /\ chat = 0 /\ queued = FALSE /\ blind = FALSE /\ ended = FALSE
  /\ standing = FALSE /\ seen = FALSE /\ unsent = FALSE /\ pending = FALSE
  /\ lock = NoPass /\ pc = [p \in Passes |-> "off"] /\ free = 0 /\ had = FALSE /\ failed = FALSE /\ woke = FALSE /\ rows = {}

\* ---------------- the world ----------------
\* A hold row is added or retired, a PR opens or closes, Zane answers, a host gains or loses what an item needs.
Changes(i, b) == lock = NoPass /\ blocker[i] # b /\ blocker' = [blocker EXCEPT ![i] = b]
  /\ UNCHANGED <<taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending, PassVars>>
\* A run exits at any time: its slot frees and its result waits for a collect.
RunEnds == ended' = TRUE
  /\ \/ auto > 0 /\ auto' = auto - 1 /\ UNCHANGED <<drained, chat>>
     \/ drained > 0 /\ drained' = drained - 1 /\ UNCHANGED <<auto, chat>>
     \/ chat > 0 /\ chat' = chat - 1 /\ UNCHANGED <<auto, drained>>
  /\ UNCHANGED <<blocker, taken, queued, blind, standing, seen, unsent, pending, PassVars>>
\* The chat queues a run (bin/queue), or starts one itself now with no slot check.
\* The drain reads its list once, so a run queued while it drains is the next pass's.
ChatQueues == ~queued /\ ~(lock # NoPass /\ pc[lock] = "drain") /\ queued' = TRUE
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, blind, ended, standing, seen, unsent, pending, PassVars>>
ChatStarts == chat < MaxChat /\ chat' = chat + 1
  /\ UNCHANGED <<blocker, taken, auto, drained, queued, blind, ended, standing, seen, unsent, pending, PassVars>>
\* The VM stops or starts answering ssh.
HostFlips == lock = NoPass /\ blind' = ~blind
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, ended, standing, seen, unsent, pending, PassVars>>
\* A needs-Zane item appears, or goes (Zane answered). One that goes is dropped from seen, so its return is news.
StandingFlips == lock = NoPass /\ standing' = ~standing /\ seen' = (seen /\ standing')
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, unsent, pending, PassVars>>

\* ---------------- a pass ----------------
\* launchd's StartInterval fires, or someone runs bin/pickup by hand.
Fire(p) == pc[p] = "off" /\ pc' = [pc EXCEPT ![p] = "trying"]
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, had, failed, woke, rows>>
\* flock -n on pickup.lock: the one holder goes on; another pass logs `pass-locked` and exits 0 with no wake.
Lock(p) == pc[p] = "trying" /\ lock = NoPass
  /\ lock' = p /\ pc' = [pc EXCEPT ![p] = "collect"] /\ rows' = {<<"pass", "start">>}
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending, free, had,
                 failed, woke>>
Locked(p) == pc[p] = "trying" /\ lock # NoPass /\ lock # p /\ pc' = [pc EXCEPT ![p] = "off"]
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, had, failed, woke, rows>>
\* Step 1: every ended run's report goes into the outbox, and only then is the run collected.
Collect(p) == pc[p] = "collect" /\ lock = p /\ pc' = [pc EXCEPT ![p] = "drain"]
  /\ ended' = FALSE /\ unsent' = (unsent \/ ended) /\ pending' = (pending \/ ended)
  /\ rows' = (IF ended THEN rows \cup {<<"run", "collect">>} ELSE rows)
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, standing, seen, lock, free, had, failed, woke>>
\* Step 4, `bin/hourly queue`: a queued hand run starts while the hand pool has room and every host's runs are known.
CanDrain == queued /\ ~blind /\ drained < Hand
Drain(p) == pc[p] = "drain" /\ lock = p /\ CanDrain /\ queued' = FALSE /\ drained' = drained + 1
  /\ rows' = rows \cup {<<"run", "launch">>}
  /\ UNCHANGED <<blocker, taken, auto, chat, blind, ended, standing, seen, unsent, pending, lock, pc, free, had, failed, woke>>
\* The queue is done: the pass counts the free auto slots once.
DrainDone(p) == pc[p] = "drain" /\ lock = p /\ ~CanDrain
  /\ pc' = [pc EXCEPT ![p] = "pick"] /\ free' = Free
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, had, failed, woke, rows>>
\* Step 5: while an auto slot is free, each candidate is launched, or skipped with the one rule that holds for it.
Pick(p, i) == pc[p] = "pick" /\ lock = p /\ free > 0 /\ Eligible(i)
  /\ taken' = taken \cup {i} /\ auto' = auto + 1 /\ free' = free - 1
  /\ rows' = rows \cup {<<i, "launch">>}
  /\ UNCHANGED <<blocker, drained, chat, queued, blind, ended, standing, seen, unsent, pending, lock, pc, had, failed, woke>>
Skip(p, i, r) == pc[p] = "pick" /\ lock = p /\ free > 0 /\ i \notin taken /\ ~Handled(i) /\ blocker[i] = r
  /\ rows' = rows \cup {<<i, r>>}
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, pc, free, had, failed, woke>>
PickDone(p) == pc[p] = "pick" /\ lock = p /\ (free = 0 \/ \A i \in Items \ taken : Handled(i))
  /\ pc' = [pc EXCEPT ![p] = "report"]
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, had, failed, woke, rows>>
\* The needs-Zane items not delivered before join the outbox's results.
Report(p) == pc[p] = "report" /\ lock = p /\ pc' = [pc EXCEPT ![p] = "wake"] /\ had' = News
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, failed, woke, rows>>
\* The deadline (config/policy.toml [pickup] deadline_s): the parent kills the pass's process group and goes to the wake with the
\* outbox as it stands. Launched pi runs are in their own sessions and live on.
Kill(p) == pc[p] \in {"collect", "drain", "pick", "report"} /\ lock = p
  /\ pc' = [pc EXCEPT ![p] = "wake"] /\ failed' = TRUE /\ had' = News
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending,
                 lock, free, woke, rows>>
\* hosts/vm/wake.py with the outbox: `done` for output, `failed` for a killed pass. The POST reaches the chat or not.
\* Only a POST that reached it empties the outbox and marks the needs-Zane item seen. A failed POST keeps the outbox, and
\* a killed pass's failure joins it, so the next pass says it again.
\* The outbox is a file the child writes before each collect, so a killed pass's parent sends what it holds too. Only a
\* pass that reached its report knows the needs-Zane items, so only its wake marks them seen.
WakeOk(p) == pc[p] = "wake" /\ lock = p /\ (had \/ failed) /\ ~woke
  /\ woke' = TRUE /\ unsent' = FALSE /\ pending' = FALSE /\ seen' = (seen \/ (~failed /\ standing))
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, lock, pc, free, had, failed, rows>>
WakeFails(p) == pc[p] = "wake" /\ lock = p /\ (had \/ failed) /\ ~woke
  /\ woke' = TRUE /\ unsent' = (unsent \/ failed)
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, pending, lock, pc, free, had,
                 failed, rows>>
\* The pass ends and drops the lock: after its wake, or with nothing to say and no wake.
End(p) == pc[p] = "wake" /\ lock = p /\ (woke \/ ~(had \/ failed))
  /\ pc' = [pc EXCEPT ![p] = "off"] /\ lock' = NoPass /\ rows' = {} /\ free' = 0 /\ had' = FALSE /\ failed' = FALSE /\ woke' = FALSE
  /\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, ended, standing, seen, unsent, pending>>

PassStep(p) == Lock(p) \/ Locked(p) \/ Collect(p) \/ Drain(p) \/ DrainDone(p) \/ PickDone(p) \/ Report(p) \/ End(p)
  \/ (\E i \in Items : Pick(p, i) \/ \E r \in Rules : Skip(p, i, r))
World == (\E i \in Items, b \in Rules \cup {"none"} : Changes(i, b))
  \/ RunEnds \/ ChatQueues \/ ChatStarts \/ HostFlips \/ StandingFlips
Next == World \/ \E p \in Passes : Fire(p) \/ PassStep(p) \/ Kill(p) \/ WakeOk(p) \/ WakeFails(p)
\* launchd keeps firing, a pass runs to its end, and a webhook that keeps being tried reaches the chat at some point.
Spec == Init /\ [][Next]_vars /\ \A p \in Passes : WF_vars(Fire(p)) /\ WF_vars(PassStep(p)) /\ WF_vars(WakeOk(p) \/ WakeFails(p))
  /\ SF_vars(WakeOk(p))

\* ---------------- the rules ----------------
\* "No overlapping passes."
NoOverlap == \A p, q \in Passes : pc[p] \in Active /\ pc[q] \in Active => p = q
\* "No silent pass when it could pick": a pass that reports with an auto slot free launched or skipped every candidate,
\* each skip its own row naming the rule that holds for it.
NoSilentPassWhenCouldPick == \A p \in Passes : pc[p] = "report" /\ free > 0 =>
  \A i \in Items \ taken : <<i, blocker[i]>> \in rows /\ blocker[i] \in Rules
\* "Caps never exceeded": each pool within its cap.
CapsHold == auto <= Auto /\ drained <= Hand
\* "Held issues never picked" (and none waiting on Zane). The blockers do not move while a pass holds the lock.
HeldNeverPicked == \A i \in Items : <<i, "launch">> \in rows => blocker[i] \notin {"held", "zane"}
\* The output a pass reports is what the chat has not had: results no wake delivered, or a needs-Zane item no delivered
\* wake carried. An item that stands is said once, not every pass.
OutputIsNew == [][\A p \in Passes : pc[p] # "wake" /\ pc'[p] = "wake" => (had' <=> (unsent \/ (standing /\ ~seen)))]_vars
\* "A wake only when there's output or a failure", and always then: every pass that ends woke iff it had either.
WakeIffOutputOrFailure == [][\A p \in Passes : pc[p] \in Active /\ pc'[p] = "off" => (woke <=> (had \/ failed))]_vars
\* A collected result stays in the outbox until a wake delivered it, and one is delivered at some point.
NoResultLost == pending => unsent
ResultReachesChat == pending ~> ~pending
\* "Never launch another launcher's brief as written" (cua-3627, 13 Nov: a Linear-era unstick brief of a PR merged two
\* days before ran when issue #3627 mapped to its id).
ForeignNeverLaunched == \A i \in Foreign : <<i, "launch">> \notin rows
EveryPassEnds == \A p \in Passes : pc[p] \in Active ~> pc[p] = "off"

\* For the chart only (PickupChart.cfg ACTION_CONSTRAINT): the pass's own steps, with runs ending around it; blockers, hosts,
\* the queue and needs-Zane items stay as they start.
ChartSteps == blocker' = blocker /\ blind' = blind /\ queued' = queued /\ chat' = chat /\ standing' = standing
\* The passes are alike (launchd's and a hand run), so TLC checks one order of them (Pickup.cfg SYMMETRY).
PassSym == Permutations(Passes)
TypeOK == taken \subseteq Items /\ lock \in Passes \cup {NoPass} /\ auto \in Nat /\ chat \in 0..MaxChat
  /\ pc \in [Passes -> {"off", "trying"} \cup Active] /\ free \in Nat
====
