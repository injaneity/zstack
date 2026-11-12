"""Mutation check for model/Hourly.tla: each mutant puts back one way the hourly pickup went wrong, and must make TLC fail
on HourlySmall.cfg (Hourly.cfg's rules at smaller caps) with the rule it names. The first three are zstack main
4e08648, before the pickup fix (#68). A mutant that still passes, or fails on another rule, means the model cannot see that bug.
usage: python3 -I model/hourly_mutants.py <tla2tools.jar> <java> <scratch dir>   (one line per mutant, exit 1 on a survivor)"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# The chat declining every Todo in one row of its own (pickup.log, 12 Nov 02:38 SGT: "backlog is unstick/review items
# already filed; no auto pick"), which step 5 allowed at main because it never said its rules were the only ones.
DECLINES = ("PickDone ==", "Declines == pc = \"pick\" /\\ free > 0 /\\ todo # {} /\\ pc' = \"end\" /\\ rows' = rows \\cup {<<\"todo\", \"filed\">>}\n"
            "  /\\ UNCHANGED <<World, dirty, free>>\nPickDone ==")
DECLINES_NEXT = ("PassSteps == PassStarts", "PassSteps == Declines \\/ PassStarts")
NO_FREE_IN_ACTS = ("Acts == ended \\/ qchat + qauto > 0 \\/ blind \\/ (Free > 0 /\\ todo # {})", "Acts == ended \\/ qchat + qauto > 0 \\/ blind")
M = [
    # name, the bug, [(old, new)], the rule that must catch it
    ("main-4e08648-silent-skip", "acts had no free auto slot, so an unchanged pass skipped with Todos to pick (before #68)",
     [NO_FREE_IN_ACTS], "NoSilentPassWhenCouldPick"),
    ("main-4e08648-group-decline", "step 5 named no closed set of rules, so the chat declined the list in one row (before #68)",
     [DECLINES, DECLINES_NEXT], "PicksOrSkipsEachTodo"),
    ("main-4e08648-no-pass-row", "a skipped pass wrote no pickup.log row (before #68)",
     [("/\\ rows' = {<<\"pass\", \"candidates\">>}", "/\\ rows' = (IF ~dirty /\\ ~Acts THEN {} ELSE {<<\"pass\", \"candidates\">>})")],
     "EveryPassLogs"),
    ("blind-peer-counts-as-idle", "auto_free() counted an unreachable host's runs as none (before this model)",
     [("Free == KnownFree", "Free == CodeFree")], "CapsHold"),
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
     "CapsHold"),
    ("drain-past-total", "the drain starts a queued run with the total full",
     [("CanDrainChat == qchat > 0 /\\ ~blind /\\ SeenRunning < Total", "CanDrainChat == qchat > 0 /\\ ~blind")],
     "CapsHold"),
    ("drain-ignores-auto-cap", "the drain starts a queued auto brief with the auto cap full",
     [("CanDrainAuto == qauto > 0 /\\ ~blind /\\ SeenRunning < Total /\\ SeenAuto < Auto", "CanDrainAuto == qauto > 0 /\\ ~blind /\\ SeenRunning < Total")], "CapsHold"),
    ("pick-on-step-0-count", "step 5 goes by step 0's slots: line, before the drain filled slots",
     [("/\\ ~CanDrainAuto /\\ pc' = \"pick\" /\\ free' = Free", "/\\ ~CanDrainAuto /\\ pc' = \"pick\" /\\ free' = free")],
     "CapsHold"),
    ("queue-line-ignores-blind-peer", "the queue's auto free line counts a peer that dropped after step 0 as idle",
     [("DrainDone == pc = \"drain\" /\\ ~CanDrainChat /\\ ~CanDrainAuto /\\ pc' = \"pick\" /\\ free' = Free",
       "DrainDone == pc = \"drain\" /\\ ~CanDrainChat /\\ ~CanDrainAuto /\\ pc' = \"pick\" /\\ free' = CodeFree")],
     "CapsHold"),
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
            out = subprocess.run([java, "-cp", jar, "tlc2.TLC", "-workers", "2", "-metadir", os.path.join(d, "meta"),
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
