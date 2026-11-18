import importlib.machinery
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

for _k in ("ZSTACK_CONFIG", "ZSTACK_MACHINE", "ZSTACK_REPO"):
    os.environ.pop(_k, None)

PKG = Path(__file__).resolve().parent.parent
_l = importlib.machinery.SourceFileLoader("queue_under_test", str(PKG / "bin" / "queue"))
queue = importlib.util.module_from_spec(importlib.util.spec_from_loader(_l.name, _l))
_l.exec_module(queue)
BASE = queue.policy.load()
REPO = BASE["merge"]["repo"]
H1, H2 = "a" * 40, "b" * 40


def cand(n, kind="issue", author="someone", labels=(), linked=(), created="2026-01-01"):
    ident = f"app-{n}" if kind == "issue" else f"app-{n}-review-{H1[:7]}"
    return dict(id=ident, kind=kind, n=n, head=None if kind == "issue" else H1, author={"login": author}, title=f"t{n}", body="b",
                url=f"u{n}", created=created, labels=list(labels), linked=list(linked))


class Forge:
    def __init__(self):
        self.prs, self.calls, self.merges = {}, [], []

    def gh(self, *a, stdin=None):
        self.calls.append(a)
        if a[:2] == ("pr", "view"):
            p = self.prs[int(a[2])]
            return 0, json.dumps({"number": int(a[2]), "state": p.get("state", "OPEN"), "headRefOid": p["head"], "isDraft": p.get("draft", False),
                                  "labels": [{"name": x} for x in p.get("labels", [])],
                                  "statusCheckRollup": [{"name": BASE["merge"]["gate_check"], "conclusion": p.get("ci", "SUCCESS")}]}), ""
        if a[:2] == ("pr", "merge"):
            self.merges.append((int(a[2]), a[a.index("--match-head-commit") + 1]))
            return 0, "", ""
        raise AssertionError(a)


class Case(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.holds = self.root / "hold-prs.txt"
        self.holds.write_text("# none\n")
        self.pol = json.loads(json.dumps(BASE))
        self.pol["host"].update(root=str(self.root), holds=str(self.holds))
        self.pol["run"]["threads"] = 2
        self.forge = Forge()
        for p in (patch.object(queue, "gh", self.forge.gh), patch.dict(os.environ, {"ONIGIRI_ROOT": str(self.root)})):
            p.start()
            self.addCleanup(p.stop)

    def items(self):
        return json.loads((self.root / "queue.json").read_text())["items"]

    def pick(self, cands, prs=(), dry=False):
        with patch.object(queue, "candidates", return_value=cands), patch.object(queue, "open_prs", return_value=set(prs)):
            return queue.pickup(self.pol, dry)

    def add(self, ident, text="do the thing", priority=0, preapproved=False):
        return queue.add(self.pol, ident, text, priority, preapproved)

    def to_working(self, ident, thread="t-1"):
        queue.step(self.pol, "start", ident)
        return queue.step(self.pol, "claim", ident, thread=thread)

    def pr_open(self, ident, pr, head=H1, labels=()):
        self.forge.prs[pr] = {"head": head, "labels": list(labels)}


class Entry(Case):
    def test_pickup_queues_what_the_repo_has_and_says_why_it_skipped_the_rest(self):
        self.holds.write_text("hold | #7 | all | x\nskip-author | spammer | no | x\n")
        cands = [cand(1), cand(7, "review"), cand(2, labels=["hold"]), cand(3, linked=[7]), cand(4, author="spammer"),
                 cand(5, author="x[bot]"), cand(6, linked=[9])]
        out = self.pick(cands, prs=[9])
        self.assertEqual([x["id"] for x in out["queued"]], ["app-1"])
        self.assertEqual({x["id"]: x["why"] for x in out["skipped"]},
                         {"app-7-review-aaaaaaa": "held", "app-2": "held", "app-3": "held", "app-4": "held", "app-5": "held", "app-6": "pr"})
        self.assertEqual(self.pick(cands, prs=[9])["skipped"][0], {"id": "app-1", "why": "queued"})

    def test_a_dry_pickup_writes_nothing(self):
        self.assertEqual(len(self.pick([cand(1)], dry=True)["queued"]), 1)
        self.assertFalse((self.root / "queue.json").exists())

    def test_an_unreadable_holds_file_or_a_failed_read_queues_nothing(self):
        self.holds.unlink()
        self.assertEqual(self.pick([cand(1)])["skipped"], [{"id": "app-1", "why": "holds-unreadable"}])
        self.holds.write_text("")
        with patch.object(queue, "gh", lambda *a, **k: (1, "", "gh down")):
            out = queue.pickup(self.pol, False)
        self.assertEqual((out["queued"], bool(out["errors"])), ([], True))

    def test_a_machine_takes_its_labels_one_takes_the_unlabelled_and_an_item_with_two_goes_to_one(self):
        mac = {"labels": ["macOS"], "unlabelled": False, "all_labels": ["Linux", "macOS"]}
        linux = {"labels": ["Linux"], "unlabelled": True, "all_labels": ["Linux", "macOS"]}
        for labels, on_mac, on_linux in ((["macOS"], True, False), (["Linux"], False, True), (["bug"], False, True), ([], False, True),
                                         (["macOS", "Linux"], False, True)):
            self.assertEqual((queue.takes(labels, mac), queue.takes(labels, linux)), (on_mac, on_linux), labels)
        self.assertTrue(queue.takes(["Linux"], {"labels": [], "unlabelled": False, "all_labels": []}))
        self.pol["host"].update(mac)
        self.assertEqual(self.pick([cand(1, labels=["Linux"])])["skipped"], [{"id": "app-1", "why": "other-machine"}])

    def test_the_orchestrator_adds_a_task_with_a_priority(self):
        got = self.add("ship-docs", "Write the docs\nmore", priority=5)
        self.assertEqual((got["state"], got["source"], got["priority"], got["title"]), ("queued", "orchestrator", 5, "Write the docs"))
        for bad in (("ship-docs", "again"), ("bad id", "x"), ("empty", "  ")):
            with self.subTest(bad=bad), self.assertRaises(queue.Refused):
                self.add(*bad)

    def test_a_review_brief_follows_the_repos_preference_and_a_teammate_is_never_salvaged(self):
        self.holds.write_text("review-only | mate | no | x\n")
        h = queue.holds(self.holds.read_text())
        outside, mate = queue.task(cand(5, "review", author="guest"), h, self.pol), queue.task(cand(6, "review", author="Mate"), h, self.pol)
        for want in ("salvage a fixable PR", "`Salvaged from #5`", "keeps guest's commits as authored", "Never re-author"):
            self.assertIn(want, outside)
        self.assertIn("review-only: a first review and nothing else", mate)
        self.assertNotIn("alvage", mate)
        self.pol["review"]["outside"] = "request-changes"
        sent_back = queue.task(cand(5, "review", author="guest"), h, self.pol)
        self.assertIn("request changes on a fixable PR", sent_back)
        self.assertNotIn("Salvaged from", sent_back)

    def test_an_urgent_label_goes_ahead_of_the_rest(self):
        self.pick([cand(1), cand(2, labels=["urgent"])])
        self.assertEqual(queue.next_up(self.pol)["launch"]["id"], "app-2")


class Launch(Case):
    def test_the_highest_priority_goes_first_then_the_oldest(self):
        self.add("low", priority=1)
        self.add("high", priority=9)
        self.add("high-later", priority=9)
        self.assertEqual(queue.next_up(self.pol)["launch"]["id"], "high")
        with self.assertRaisesRegex(queue.Refused, "low is not next: high is"):
            queue.step(self.pol, "start", "low")
        self.to_working("high")
        self.assertEqual(queue.next_up(self.pol)["launch"]["id"], "high-later")

    def test_one_launch_at_a_time_and_never_past_the_thread_cap(self):
        for x in ("a", "b", "c"):
            self.add(x)
        queue.step(self.pol, "start", "a")
        self.assertEqual(queue.next_up(self.pol)["waiting"], "a launch is under way")
        with self.assertRaisesRegex(queue.Refused, "a launch is under way"):
            queue.step(self.pol, "start", "b")
        queue.step(self.pol, "claim", "a", thread="t-a")
        self.to_working("b", "t-b")
        got = queue.next_up(self.pol)
        self.assertEqual((got["launch"], got["waiting"]), (None, "the machine is at its thread cap"))

    def test_a_started_item_whose_thread_did_not_start_goes_back_and_a_stale_one_is_listed(self):
        self.add("a")
        queue.step(self.pol, "start", "a")
        self.assertEqual(queue.next_up(self.pol)["recover"], [])
        with patch.object(queue, "STALE_S", 0):
            self.assertEqual(queue.next_up(self.pol)["recover"], ["a"])
        self.assertEqual(queue.step(self.pol, "requeue", "a")["state"], "queued")
        with self.assertRaisesRegex(queue.Refused, "a is queued, not starting"):
            queue.step(self.pol, "claim", "a", thread="t")

    def test_a_held_item_is_passed_over_at_launch_too(self):
        self.pick([cand(1, "review"), cand(2)])
        self.holds.write_text("hold | #1 | all | x\n")
        self.assertEqual(queue.next_up(self.pol)["launch"]["id"], "app-2")

    def test_the_brief_tells_the_thread_how_to_report(self):
        self.add("a", "Fix the parser")
        text = queue.next_up(self.pol)["launch"]["brief"]
        for want in ("# a", "Fix the parser", f"{PKG}/bin/queue verified a <pr number> <head sha> pass auto", f"{PKG}/bin/queue raise a",
                     "Never merge", "ZSTACK_REPO=acme/app "):
            self.assertIn(want, text)


class Merge(Case):
    def setUp(self):
        super().setUp()
        self.pol["run"]["threads"] = 9

    def passed(self, said="auto", labels=(), ident="a", pr=40, preapproved=False):
        self.add(ident, preapproved=preapproved)
        self.to_working(ident)
        self.pr_open(ident, pr, labels=labels)
        return queue.verified(self.pol, ident, pr, H1, "pass", said)

    def test_a_pass_as_auto_merges_that_head_once_the_gate_check_is_green(self):
        got = self.passed()
        self.assertEqual((got["state"], got["auto"], got["say"]), ("passed", True, "merged, or in the merge queue"))
        self.assertEqual(self.forge.merges, [(40, H1)])

    def test_a_pass_with_ci_still_running_waits_and_the_sweep_merges_it_when_it_is_green(self):
        self.add("a")
        self.to_working("a")
        self.pr_open("a", 40)
        self.forge.prs[40]["ci"] = None
        got = queue.verified(self.pol, "a", 40, H1, "pass", "auto")
        self.assertEqual((got["say"], self.forge.merges), (f"it waits for {BASE['merge']['gate_check']} to pass", []))
        self.assertEqual(queue.sweep(self.pol)[0]["why"], f"it waits for {BASE['merge']['gate_check']} to pass")
        self.forge.prs[40]["ci"] = "FAILURE"
        queue.sweep(self.pol)
        self.assertEqual(self.forge.merges, [])
        self.forge.prs[40]["ci"] = "SUCCESS"
        self.assertEqual(queue.sweep(self.pol)[0]["why"], "merged, or in the merge queue")
        self.assertEqual(self.forge.merges, [(40, H1)])

    def test_a_pass_as_review_the_review_label_a_hold_or_a_draft_is_not_merged(self):
        self.assertEqual(self.passed(said="review")["say"], "the owner merges it: it needs a person's review")
        label = [BASE["merge"]["review_label"]]
        self.assertEqual(self.passed(labels=label, ident="b", pr=41)["say"], "the owner merges it: it needs a person's review")
        self.holds.write_text("hold | #42 | all | x\n")
        self.assertEqual(self.passed(ident="c", pr=42)["say"], "it waits: it has a hold row")
        self.add("d")
        self.to_working("d")
        self.pr_open("d", 43)
        self.forge.prs[43]["draft"] = True
        self.assertEqual(queue.verified(self.pol, "d", 43, H1, "pass", "auto")["say"], "it waits: the PR is a draft")
        self.assertEqual(self.forge.merges, [])

    def test_a_preapproved_item_merges_whatever_the_verifier_or_its_thread_says_unless_it_is_held(self):
        self.assertTrue(self.passed(said="review", preapproved=True)["auto"])
        self.assertTrue(queue.raise_review(self.pol, "a")["auto"])
        self.holds.write_text(f"preapprove | #41 | no | {H1} | the owner\npreapprove | #42 | no | {H2} | the owner\n")
        self.assertTrue(self.passed(said="review", ident="b", pr=41)["auto"])
        self.assertFalse(self.passed(said="review", ident="c", pr=42)["auto"])
        self.holds.write_text("hold | #43 | all | x\n")
        self.assertEqual(self.passed(ident="d", pr=43, preapproved=True)["say"], "it waits: it has a hold row")
        self.assertEqual(self.forge.merges, [(40, H1), (41, H1)])

    def test_an_issue_with_a_preapproved_label_enters_preapproved(self):
        self.pick([cand(1, labels=BASE["merge"]["preapproved_labels"]), cand(2), cand(3, "review", labels=BASE["merge"]["preapproved_labels"])])
        self.assertEqual({k: v["preapproved"] for k, v in self.items().items()}, {"app-1": True, "app-2": False, f"app-3-review-{H1[:7]}": False})

    def test_a_verdict_on_a_head_the_pr_has_left_is_refused(self):
        self.add("a")
        self.to_working("a")
        self.pr_open("a", 40, head=H2)
        for verdict in (("pass", "auto"), ("fail",)):
            with self.assertRaisesRegex(queue.Refused, "the head moved"):
                queue.verified(self.pol, "a", 40, H1, *verdict)
        self.assertEqual((self.items()["a"]["state"], self.items()["a"]["fixes"], self.forge.merges), ("working", 0, []))

    def test_a_new_head_after_a_pass_is_verified_again_without_waiting_for_the_sweep(self):
        self.passed(said="review")
        self.forge.prs[40]["head"] = H2
        got = queue.verified(self.pol, "a", 40, H2, "pass", "auto")
        self.assertEqual((got["state"], got["head"], self.forge.merges), ("passed", H2, [(40, H2)]))

    def test_a_failed_verification_goes_back_to_the_thread_until_the_attempts_are_spent(self):
        self.add("a")
        self.to_working("a")
        cap = self.pol["ci"]["fix_attempts"]
        self.pr_open("a", 40)
        for n in range(1, cap + 1):
            got = queue.verified(self.pol, "a", 40, H1, "fail")
            self.assertEqual((got["fixes"], got["state"]), (n, "working" if n < cap else "owner"))
        with self.assertRaises(queue.Refused):
            queue.verified(self.pol, "a", 40, H1, "pass", "auto")

    def test_asking_for_review_before_the_pass_leaves_the_merge_to_the_owner(self):
        self.add("b")
        self.to_working("b", "t-b")
        queue.raise_review(self.pol, "b")
        self.pr_open("b", 41)
        got = queue.verified(self.pol, "b", 41, H1, "pass", "auto")
        self.assertEqual((got["auto"], got["review"], self.forge.merges), (False, True, []))

    def test_sweep_closes_merged_items_sends_a_new_head_back_to_work_and_does_not_merge_a_held_one(self):
        for ident, pr in (("a", 40), ("b", 41), ("c", 42)):
            self.add(ident)
            self.to_working(ident)
            self.pr_open(ident, pr)
            self.forge.prs[pr]["ci"] = None
            queue.verified(self.pol, ident, pr, H1, "pass", "auto")
        self.forge.prs[40]["state"] = "MERGED"
        self.forge.prs[41]["head"] = H2
        self.forge.prs[42]["ci"] = "SUCCESS"
        self.holds.write_text("hold | #42 | all | x\n")
        moved = {x["id"]: (x["now"], x["why"]) for x in queue.sweep(self.pol)}
        self.assertEqual(moved, {"a": ("done", "merged"), "b": ("working", "a new head after it passed"),
                                 "c": ("passed", "it waits: it has a hold row")})
        self.assertEqual(self.forge.merges, [])

    def test_a_passed_item_frees_its_slot_and_a_new_head_takes_it_back(self):
        self.pol["run"]["threads"] = 1
        self.passed("review")
        self.add("b")
        self.assertEqual(queue.next_up(self.pol)["launch"]["id"], "b")
        self.forge.prs[40]["head"] = H2
        self.assertEqual(queue.sweep(self.pol)[0]["now"], "working")
        self.assertEqual(queue.next_up(self.pol)["waiting"], "the machine is at its thread cap")

    def test_an_ended_thread_frees_its_slot_and_its_open_pr_goes_to_the_owner(self):
        self.pol["run"]["threads"] = 3
        for x in ("a", "b", "c"):
            self.add(x)
            self.to_working(x, thread=f"t-{x}")
        self.assertEqual(queue.next_up(self.pol)["working"], {"a": "t-a", "b": "t-b", "c": "t-c"})
        self.pr_open("b", 41)
        self.pr_open("c", 42)
        queue.verified(self.pol, "c", 42, H1, "fail")
        got = [queue.ended(self.pol, "a"), queue.ended(self.pol, "b", 41), queue.ended(self.pol, "c")]
        self.assertEqual([(x["state"], x["pr"], x["why"]) for x in got], [
            ("done", None, "its thread ended with no PR"),
            ("owner", 41, "its thread ended before the verifier passed its PR"),
            ("owner", 42, "its thread ended before the verifier passed its PR")])
        self.assertEqual(queue.next_up(self.pol)["working"], {})
        with self.assertRaisesRegex(queue.Refused, "b is owner, not working"):
            queue.ended(self.pol, "b")
        self.forge.prs[41]["state"] = "MERGED"
        self.assertEqual([(x["id"], x["now"]) for x in queue.sweep(self.pol)], [("b", "done")])

    def test_an_items_own_thread_never_records_its_verdict(self):
        self.add("a")
        self.to_working("a", thread="t-a")
        self.pr_open("a", 40)
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-a"}), self.assertRaisesRegex(queue.Refused, "a's own thread"):
            queue.verified(self.pol, "a", 40, H1, "pass", "auto")
        self.assertEqual(self.items()["a"]["state"], "working")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-verifier"}):
            self.assertEqual(queue.verified(self.pol, "a", 40, H1, "pass", "auto")["state"], "passed")

    def test_an_item_left_verifying_by_an_older_queue_is_the_owners_and_the_sweep_closes_it(self):
        legacy = {**queue.item("task", None, "orchestrator", 0, "t", "t"), "state": "verifying", "pr": 40, "head": H1}
        (self.root / "queue.json").write_text(json.dumps({"items": {"a": legacy}}))
        self.pr_open("a", 40)
        self.assertEqual((queue.sweep(self.pol), queue.next_up(self.pol)["waiting"]), ([], "nothing is queued"))
        self.forge.prs[40]["state"] = "MERGED"
        self.assertEqual(queue.sweep(self.pol), [{"id": "a", "now": "done", "why": "merged"}])

    def test_the_steps_are_the_models(self):
        spec = (PKG / "model" / "Queue.tla").read_text()
        self.assertEqual(queue.STEPS, {"start": ("queued", "starting"), "claim": ("starting", "working"),
                                       "requeue": ("starting", "queued")})
        for name, to in (("Start(m, i)", "starting"), ("Claim(m)", "working"), ("Requeue(m)", "queued"), ("Pass(m, i)", "passed"),
                         ("End(m, i)", "done"), ("End(m, i)", "owner")):
            self.assertIn(f'"{to}")', spec[spec.index(f"\n{name} =="):].split("\n\n")[0], name)
        states = set(spec[spec.index("States == {") + 11:spec.index("}", spec.index("States == {"))].replace('"', "").split(", "))
        self.assertEqual(states - {"none"}, {*queue.ACTIVE, "queued", "passed", "owner", "done"})


class Cli(Case):
    def test_the_commands(self):
        out = io.StringIO()
        with patch.object(queue.policy, "load", return_value=self.pol), \
                patch("sys.stdout", out):
            self.assertEqual(queue.main(["add", "a", "--priority", "3"], stdin=io.StringIO("task")), 0)
            self.assertEqual(queue.main(["add", "b", "--priority"], stdin=io.StringIO("task")), 2)
            self.assertEqual(queue.main(["add", "c", "--preapproved", "--priority", "3"], stdin=io.StringIO("task")), 0)
            self.assertEqual(queue.main(["add", "d", "--preapproved", "--preapproved"], stdin=io.StringIO("task")), 2)
            self.assertEqual(queue.main(["start", "a"]), 0)
            self.assertEqual(queue.main(["claim", "a", "t-1"]), 0)
            self.forge.prs[40] = {"head": H1}
            self.assertEqual(queue.main(["ended", "a", "x"]), 2)
            self.assertEqual(queue.main(["verified", "a", "pass", "auto"]), 2)
            self.assertEqual(queue.main(["verified", "a", "40", H1, "pass"]), 2)
            self.assertEqual(queue.main(["verified", "a", "40", H1, "pass", "extreme"]), 2)
            self.assertEqual(queue.main(["verified", "a", "40", H1[:7], "pass", "auto"]), 2)
            self.assertEqual(queue.main(["verified", "a", "40", H1, "pass", "auto"]), 0)
            self.assertEqual(queue.main(["sweep"]), 0)
            self.assertEqual(queue.main(["list"]), 0)
            self.assertEqual(queue.main(["next"]), 0)
            self.assertEqual(queue.main(["nope"]), 2)
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["start", "zzz"]), 1)
            self.assertEqual(err.getvalue(), "queue: no item zzz\n")
        self.assertEqual(self.items()["a"]["state"], "passed")


if __name__ == "__main__":
    unittest.main()
