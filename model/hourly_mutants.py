import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DECLINES = ("PickDone ==", "Declines == pc = \"pick\" /\\ free > 0 /\\ todo # {} /\\ pc' = \"end\" /\\ rows' = rows \\cup {<<\"todo\", \"filed\">>}\n"
            "  /\\ UNCHANGED <<World, dirty, free>>\nPickDone ==")
DECLINES_NEXT = ("PassSteps == PassStarts", "PassSteps == Declines \\/ PassStarts")
NO_FREE_IN_ACTS = ("Acts == ended \\/ qhand + qauto > 0 \\/ blind \\/ (Free > 0 /\\ todo # {})", "Acts == ended \\/ qhand + qauto > 0 \\/ blind")
M = [
    ("main-4e08648-silent-skip", "acts had no free auto slot, so an unchanged pass skipped with Todos to pick (before #68)",
     [NO_FREE_IN_ACTS], "NoSilentPassWhenCouldPick"),
    ("main-4e08648-group-decline", "step 5 named no closed set of rules, so the chat declined the list in one row (before #68)",
     [DECLINES, DECLINES_NEXT], "PicksOrSkipsEachTodo"),
    ("main-4e08648-no-pass-row", "a skipped pass wrote no pickup.log row (before #68)",
     [("/\\ rows' = {<<\"pass\", \"candidates\">>}", "/\\ rows' = (IF ~dirty /\\ ~Acts THEN {} ELSE {<<\"pass\", \"candidates\">>})")],
     "EveryPassLogs"),
    ("blind-peer-counts-as-idle", "auto_free() counted an unreachable host's runs as none (before this model)",
     [("Free == KnownFree", "Free == CodeFree")], "AutoCap"),
    ("held-picked", "a pick that ignores a hold row",
     [("Picks(i, there) == pc = \"pick\" /\\ Eligible(i)", "Picks(i, there) == pc = \"pick\" /\\ i \\in todo /\\ blocker[i] # \"overlap\"")],
     "HeldOrZaneNeverPicked"),
    ("invented-rule", "a skip with a reason outside the five",
     [("Rules == {\"held\", \"pr\", \"overlap\", \"zane\", \"host\"}", "Rules == {\"held\", \"pr\", \"overlap\", \"zane\", \"host\", \"filed\"}"),
      ("Init == todo \\in SUBSET Issues /\\ blocker \\in [Issues -> Rules \\cup {\"none\"}]",
       "Init == todo \\in SUBSET Issues /\\ blocker \\in [Issues -> (Rules \\ {\"filed\"}) \\cup {\"none\"}]"),
      ("/\\ ~Handled(i) /\\ free > 0 /\\ r \\in Rules /\\ blocker[i] = r", "/\\ ~Handled(i) /\\ free > 0 /\\ r = \"filed\"")],
     "PicksOrSkipsEachTodo"),
    ("pick-past-auto-cap", "step 5 goes by its first slots: line and does not count its own picks",
     [("/\\ todo' = todo \\ {i} /\\ rows' = rows \\cup {<<i, \"launch\">>} /\\ free' = free - 1",
       "/\\ todo' = todo \\ {i} /\\ rows' = rows \\cup {<<i, \"launch\">>} /\\ free' = free")],
     "AutoCap"),
    ("drain-past-hand-pool", "the drain starts a queued hand run with the hand pool full",
     [("CanDrainHand == qhand > 0 /\\ ~HandWaits(blind)", "CanDrainHand == qhand > 0 /\\ ~blind")], "HandCap"),
    ("drain-ignores-auto-cap", "the drain starts a queued auto brief with the auto pool full",
     [("CanDrainAuto(there) == qauto > 0 /\\ ~AutoWaits(blind)", "CanDrainAuto(there) == qauto > 0 /\\ ~blind")], "AutoCap"),
    ("pick-on-step-0-count", "step 5 goes by step 0's slots: line, before the drain filled slots",
     [("/\\ ~CanDrainAuto(FALSE) /\\ pc' = \"pick\" /\\ free' = Free", "/\\ ~CanDrainAuto(FALSE) /\\ pc' = \"pick\" /\\ free' = free")],
     "AutoCap"),
    ("queue-line-ignores-blind-peer", "the queue's auto free line counts a peer that dropped after step 0 as idle",
     [("DrainDone == pc = \"drain\" /\\ ~CanDrainHand /\\ ~CanDrainAuto(FALSE) /\\ pc' = \"pick\" /\\ free' = Free",
       "DrainDone == pc = \"drain\" /\\ ~CanDrainHand /\\ ~CanDrainAuto(FALSE) /\\ pc' = \"pick\" /\\ free' = CodeFree")],
     "AutoCap"),
    ("shared-total", "the 7-total cap before the slot split: a hand launch waits while auto and quick runs fill a total",
     [("HandWaits(unknown) == unknown \\/ hand >= Hand",
       "HandWaits(unknown) == unknown \\/ hand >= Hand \\/ hand + auto + peer + quick >= Hand + 1")],
     "QuickNotCounted"),
    ("auto-cap-5", "the 5-auto cap before the slot split",
     [("CodeFree == Sub(Auto, SeenAuto)", "CodeFree == Sub(Auto + 1, SeenAuto)"),
      ("todo \\subseteq Issues /\\ free \\in 0..Auto", "todo \\subseteq Issues /\\ free \\in 0..Auto + 1")], "AutoCap"),
    ("vm-counts-itself", "a hand launch on the VM counts only what it can see",
     [("HandStarts(onVm) == pc # \"drain\"\n  /\\ \\/ ~HandWaits(onVm \\/ blind)",
       "HandStarts(onVm) == pc # \"drain\"\n  /\\ \\/ ~(blind \\/ (~onVm /\\ hand >= Hand))")],
     "HandCap"),
    ("pick-ignores-pause", "a pick goes to the peer at main with the floor free though it has an auto pause",
     [("/\\ free > 0 /\\ (there => ~blind /\\ ~pause)", "/\\ free > 0 /\\ (there => ~blind)")], "NoAutoOnPausedPeer"),
    ("drain-ignores-pause", "the drain starts a queued auto run in a paused peer's queue",
     [("CanDrainAuto(there) == qauto > 0 /\\ ~AutoWaits(blind) /\\ (there => ~pause)",
       "CanDrainAuto(there) == qauto > 0 /\\ ~AutoWaits(blind)")], "NoAutoOnPausedPeer"),
]


def main(jar, java, scratch):
    src = open(os.path.join(HERE, "Hourly.tla")).read()
    cfg = open(os.path.join(HERE, "HourlySmall.cfg")).read()
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
            open(os.path.join(d, "Hourly.tla"), "w").write(s)
            open(os.path.join(d, "Hourly.cfg"), "w").write(cfg)
            out = subprocess.run([java, "-XX:+UseParallelGC", "-jar", jar, "-workers", "2", "-metadir", os.path.join(d, "meta"),
                                  "-config", "Hourly.cfg", "Hourly.tla"], cwd=d, capture_output=True, text=True, timeout=600).stdout
            m = re.search(r"Invariant (\w+) is violated|(not completely specified|Deadlock|Error: (?!Invariant).*)", out)
            got = m and m.group(1)
            if got == rule:
                print(f"killed   {name}: {why} -> {rule} breaks")
            else:
                print(f"SURVIVED {name}: {why} -> {got or (m and 'model error: ' + m.group(2)) or 'no error'} (expected {rule})")
                bad = 1
    return bad


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
