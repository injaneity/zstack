import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
M = [
    ("no-lock", "two passes run at once (no flock on pickup.lock)",
     [("Lock(p) == pc[p] = \"trying\" /\\ lock = NoPass\n", "Lock(p) == pc[p] = \"trying\"\n")], "NoOverlap"),
    ("fingerprint-skip", "a pass with nothing changed skips its picks (bin/hourly's skip: before #68)",
     [("PickDone(p) == pc[p] = \"pick\" /\\ lock = p /\\ (free = 0 \\/ \\A i \\in Items \\ taken : Handled(i))",
       "PickDone(p) == pc[p] = \"pick\" /\\ lock = p")], "NoSilentPassWhenCouldPick"),
    ("group-skip", "a skip row for no rule that holds (\"backlog is already filed\", 11-12 Nov)",
     [("/\\ ~Handled(i) /\\ blocker[i] = r\n", "/\\ ~Handled(i)\n")], "NoSilentPassWhenCouldPick"),
    ("blind-counts-free", "an unreachable host's runs count as none, so the whole auto pool looks free",
     [("Free == IF blind THEN 0 ELSE Sub(Auto, auto)", "Free == IF blind THEN Auto ELSE Sub(Auto, auto)")], "CapsHold"),
    ("auto-cap-off-by-one", "a pick takes the slot past the auto pool",
     [("Free == IF blind THEN 0 ELSE Sub(Auto, auto)", "Free == IF blind THEN 0 ELSE Sub(Auto + 1, auto)")], "CapsHold"),
    ("drain-past-hand", "the drain starts a queued run with the hand pool full",
     [("CanDrain == queued /\\ ~blind /\\ drained < Hand", "CanDrain == queued /\\ ~blind /\\ drained <= Hand")], "CapsHold"),
    ("held-picked", "a pick that does not read the holds",
     [("Eligible(i) == i \\notin taken /\\ blocker[i] = \"none\"", "Eligible(i) == i \\notin taken /\\ blocker[i] # \"zane\"")],
     "HeldNeverPicked"),
    ("silent-pass-wakes", "every pass wakes the chat (a status report every pass)",
     [("WakeOk(p) == pc[p] = \"wake\" /\\ lock = p /\\ (had \\/ failed) /\\ ~woke", "WakeOk(p) == pc[p] = \"wake\" /\\ lock = p /\\ ~woke"),
      ("End(p) == pc[p] = \"wake\" /\\ lock = p /\\ (woke \\/ ~(had \\/ failed))", "End(p) == pc[p] = \"wake\" /\\ lock = p /\\ woke")],
     "WakeIffOutputOrFailure"),
    ("standing-item-every-pass", "a needs-Zane item that stands wakes every pass (no seen keys)",
     [("News == unsent \\/ (standing /\\ ~seen)", "News == unsent \\/ standing")], "OutputIsNew"),
    ("kill-is-silent", "a pass killed at its deadline ends with no wake (the parent dies with its child)",
     [("/\\ pc' = [pc EXCEPT ![p] = \"wake\"] /\\ failed' = TRUE /\\ had' = News",
       "/\\ pc' = [pc EXCEPT ![p] = \"off\"] /\\ failed' = TRUE /\\ had' = News /\\ lock' = NoPass"),
      ("                 lock, free, woke, rows, reads>>\nWakeOk(p)", "                 free, woke, rows, reads>>\nWakeOk(p)")],
     "WakeIffOutputOrFailure"),
    ("collect-before-outbox", "a run collected before its report reached the outbox",
     [("/\\ ended' = FALSE /\\ unsent' = (unsent \\/ ended) /\\ pending' = (pending \\/ ended)",
       "/\\ ended' = FALSE /\\ pending' = (pending \\/ ended)"),
      ("  /\\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, standing, seen, lock, free, had, failed, woke>>",
       "  /\\ UNCHANGED <<blocker, taken, auto, drained, chat, queued, blind, standing, seen, unsent, lock, free, had, failed, woke>>")],
     "NoResultLost"),
    ("foreign-brief-not-taken", "a review dir with another launcher's brief and no run is not taken, so its brief runs as written",
     [("/\\ taken = Foreign\n", "/\\ taken = {}\n")], "ForeignNeverLaunched"),
    ("failed-post-clears-outbox", "a wake whose POST failed empties the outbox",
     [("/\\ woke' = TRUE /\\ unsent' = (unsent \\/ failed)\n", "/\\ woke' = TRUE /\\ unsent' = FALSE\n")], "NoResultLost"),
    ("merge-job-read-twice", "the queue and the picks each run the merge job's dry run (13 Nov pickup pass)",
     [("DrainDone(p) == pc[p] = \"drain\" /\\ lock = p /\\ ~CanDrain\n  /\\ pc' = [pc EXCEPT ![p] = \"pick\"] /\\ free' = Free\n",
       "DrainDone(p) == pc[p] = \"drain\" /\\ lock = p /\\ ~CanDrain\n"
       "  /\\ pc' = [pc EXCEPT ![p] = \"pick\"] /\\ free' = Free /\\ reads' = reads + 1\n"),
      ("                 lock, had, failed, woke, rows, reads>>\nPick(p, i)", "                 lock, had, failed, woke, rows>>\nPick(p, i)")],
     "MergeJobReadOnce"),
]


def main(jar, java, scratch):
    src = open(os.path.join(HERE, "Pickup.tla")).read()
    cfg = open(os.path.join(HERE, "Small.cfg")).read()
    bad = 0
    for name, why, edits, rule in M:
        s = src
        for old, new in edits:
            if s.count(old) != 1:
                print(f"BROKEN   {name}: edit not found once: {old[:60]!r}")
                bad = 1
                break
            s = s.replace(old, new)
        else:
            d = os.path.join(scratch, name)
            os.makedirs(d, exist_ok=True)
            open(os.path.join(d, "Pickup.tla"), "w").write(s)
            open(os.path.join(d, "Small.cfg"), "w").write(cfg)
            out = subprocess.run([java, "-XX:+UseParallelGC", "-cp", jar, "tlc2.TLC", "-workers", "2", "-metadir",
                                  os.path.join(d, "meta"), "-config", "Small.cfg", "Pickup.tla"],
                                 cwd=d, capture_output=True, text=True, timeout=900).stdout
            m = re.search(r"Invariant (\w+) is violated|Action property (\w+) is violated|(Temporal properties were violated)"
                          r"|(not completely specified|Deadlock)", out)
            got = m and (m.group(1) or m.group(2) or (m.group(3) and "a temporal property"))
            if got == rule:
                print(f"killed   {name}: {why} -> {got} breaks")
            else:
                print(f"SURVIVED {name}: {why} -> {got or (m and 'model error: ' + m.group(4)) or 'no error'} (expected {rule})")
                bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
