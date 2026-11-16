import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PKG = Path(__file__).resolve().parent.parent
_l = importlib.machinery.SourceFileLoader("plan_under_test", str(PKG / "orchestrator" / "plan"))
plan = importlib.util.module_from_spec(importlib.util.spec_from_loader(_l.name, _l))
_l.exec_module(plan)
POL = plan.policy.load()
ME, REPO = POL["people"]["me"], POL["merge"]["repo"]
H = plan.holds("hold | #7 | all | x; Robin 11 Nov 14:56 SGT\nskip-author | spammer | no | x\nreview-only | mate | no | x\n")
TEMPLATE = "# {id}\n{task}\nrepo: {clone}\n"


def cand(n, kind="issue", author="someone", head=None, labels=(), linked=(), created="2026-01-01"):
    head = head or ("a" * 40 if kind != "issue" else None)
    ident = f"app-{n}" if kind == "issue" else f"app-{n}-{kind}-{head[:7]}"
    return dict(id=ident, kind=kind, n=n, head=head, author={"login": author}, title=f"t{n}", url=f"u{n}", created=created,
                labels=list(labels), linked=list(linked))


def decide(cands, items=None, prs=(), h=H):
    return plan.decide(cands, h, set(prs), items or {}, POL, TEMPLATE)


class Decide(unittest.TestCase):
    def test_the_same_inputs_give_the_same_plan(self):
        cands = [cand(1), cand(2, "review"), cand(3, "fix", author=ME)]
        self.assertEqual(decide(cands)[:2], decide(cands)[:2])
        launch, skipped, items = decide(cands)
        self.assertEqual([x["id"] for x in launch], ["app-1", "app-2-review-aaaaaaa", "app-3-fix-aaaaaaa"])
        self.assertEqual(skipped, [])
        self.assertEqual({v["state"] for v in items.values()}, {"queued"})
        self.assertIn(f"{REPO}#1: t1", launch[0]["brief"])
        self.assertIn(f"repo: {POL['host']['clone']}", launch[0]["brief"])

    def test_each_reason_to_skip(self):
        cases = [(cand(7, "review"), "held"), (cand(1, labels=["hold"]), "held"), (cand(1, linked=[7]), "held"),
                 (cand(1, author="spammer"), "held"), (cand(1, author="x[bot]"), "held"), (cand(1, linked=[9]), "pr")]
        for c, why in cases:
            with self.subTest(c=c["id"], why=why):
                self.assertEqual(decide([c], prs=[9])[1], [{"id": c["id"], "why": why}])
        self.assertEqual(decide([cand(1)], h=plan.holds(None))[1], [{"id": "app-1", "why": "holds-unreadable"}])

    def test_one_thread_per_item_and_a_queued_item_is_offered_again(self):
        _, _, items = decide([cand(1)])
        again, skipped, same = decide([cand(1)], items)
        self.assertEqual(([x["id"] for x in again], skipped, same), (["app-1"], [], items))
        items["app-1"].update(state="claimed", thread="t-1")
        self.assertEqual(decide([cand(1)], items)[1], [{"id": "app-1", "why": "claimed"}])
        self.assertEqual(decide([cand(1, "review")], items)[1], [{"id": "app-1-review-aaaaaaa", "why": "overlap"}])
        items["app-1"].update(state="done", prs=[40])
        self.assertEqual(decide([cand(1)], items)[1], [{"id": "app-1", "why": "done"}])
        self.assertEqual(decide([cand(1, "review")], items)[1], [])

    def test_two_candidates_on_one_number_launch_one_thread(self):
        launch, skipped, _ = decide([cand(5, "review"), cand(5, "fix", author=ME, created="2026-02-01")])
        self.assertEqual(len(launch), 1)
        self.assertEqual(skipped[0]["why"], "overlap")

    def test_a_red_pr_goes_back_to_the_thread_that_opened_it_until_the_attempts_are_spent(self):
        items = {"app-1": {"state": "done", "kind": "issue", "n": 1, "thread": "t-1", "prs": [40], "at": "x"}}
        heads = [str(i) * 40 for i in range(1, 6)]
        for i, head in enumerate(heads):
            launch, skipped, items = decide([cand(40, "fix", author=ME, head=head)], items)
            if i < POL["ci"]["fix_attempts"]:
                self.assertEqual([(x["kind"], x["thread"]) for x in launch], [("fix", "t-1")], i)
                items[launch[0]["id"]]["state"] = "done"
            else:
                self.assertEqual((launch, skipped[0]["why"]), ([], "owner"), i)

    def test_urgent_then_oldest_first(self):
        cands = sorted([cand(1, created="2026-03-01"), cand(2, created="2026-01-01"), cand(3, created="2026-05-01", labels=["urgent"])],
                       key=lambda c: ("urgent" not in c["labels"], c["created"], c["id"]))
        self.assertEqual([c["n"] for c in cands], [3, 2, 1])

    def test_a_red_gate_check_is_the_only_red(self):
        gate = POL["merge"]["gate_check"]
        self.assertTrue(plan.red({"statusCheckRollup": [{"name": gate, "conclusion": "FAILURE"}]}, gate))
        self.assertFalse(plan.red({"statusCheckRollup": [{"name": "other", "conclusion": "FAILURE"}, {"name": gate, "conclusion": None}]}, gate))
        self.assertFalse(plan.red({"statusCheckRollup": None}, gate))


class Queue(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.env = {**os.environ, "ONIGIRI_ROOT": str(self.root)}

    def run_(self, *args):
        return subprocess.run([sys.executable, str(PKG / "orchestrator" / "plan"), *args], capture_output=True, text=True, env=self.env)

    def test_a_pass_queues_what_it_prints_and_claim_and_done_move_it_forward_only(self):
        with patch.dict(os.environ, {"ONIGIRI_ROOT": str(self.root)}), patch.object(plan, "candidates", return_value=[cand(1)]), \
                patch.object(plan, "open_prs", return_value=set()), patch.object(plan, "read_text", side_effect=lambda p: (
                    TEMPLATE if str(p).endswith("brief.md") else "" if str(p).endswith("hold-prs.txt") else Path(p).read_text()
                    if Path(p).exists() else None)):
            self.assertEqual(plan.run_pass(dry=True)["launch"][0]["id"], "app-1")
            self.assertFalse((self.root / "queue.json").exists())
            self.assertEqual(plan.run_pass(dry=False)["launch"][0]["id"], "app-1")
        self.assertEqual(json.loads((self.root / "queue.json").read_text())["items"]["app-1"]["state"], "queued")
        self.assertEqual(json.loads(self.run_("claim", "app-1", "t-9").stdout)["thread"], "t-9")
        done = json.loads(self.run_("done", "app-1", "40", "41").stdout)
        self.assertEqual((done["state"], done["prs"], done["thread"]), ("done", [40, 41], "t-9"))
        back = self.run_("claim", "app-1", "t-10")
        self.assertEqual((back.returncode, back.stderr), (1, "plan: app-1 is already done\n"))
        self.assertEqual(self.run_("claim", "nope", "t").stderr, "plan: no queued item nope\n")
        self.assertEqual(list(json.loads(self.run_("list").stdout)), ["app-1"])
        self.assertEqual(self.run_("done", "app-1", "x").returncode, 2)

    def test_a_failed_read_launches_nothing(self):
        with patch.dict(os.environ, {"ONIGIRI_ROOT": str(self.root)}), patch.object(plan, "gh", side_effect=lambda e, *a: e.append("gh down") or []):
            out = plan.run_pass(dry=False)
        self.assertEqual((out["launch"], bool(out["errors"])), ([], True))
        self.assertFalse((self.root / "queue.json").exists())


if __name__ == "__main__":
    unittest.main()
