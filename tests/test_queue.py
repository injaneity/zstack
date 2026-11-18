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
                                  "statusCheckRollup": [{"name": BASE["merge"]["gate_check"], "conclusion": p.get("ci", "SUCCESS")},
                                                        *p.get("more", [])]}), ""
        if a[:2] == ("pr", "list"):
            return 0, json.dumps([{"number": n} for n, p in self.prs.items() if p.get("branch") == a[a.index("--head") + 1]][:1]), ""
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
        for want in ("# a", "Fix the parser", f"{PKG}/bin/queue verified a <pr number> <head sha> pass auto",
                     "gh pr merge <pr number> -R acme/app --squash --match-head-commit <head sha>", "ZSTACK_REPO=acme/app "):
            self.assertIn(want, text)


class Gate(Case):
    def setUp(self):
        super().setUp()
        self.pol["run"]["threads"] = 9

    def passed(self, said="auto", labels=(), ident="a", pr=40, preapproved=False):
        self.add(ident, preapproved=preapproved)
        self.to_working(ident, thread=f"t-{ident}")
        self.pr_open(ident, pr, labels=labels)
        return queue.verified(self.pol, ident, pr, H1, "pass", said)

    def shut(self, pr=40, head=H1):
        try:
            queue.gate(self.pol, pr, head)
        except queue.Refused as e:
            return str(e)

    def test_the_gate_opens_for_the_head_the_verifier_passed_as_auto_with_the_check_green(self):
        got = self.passed()
        self.assertEqual((got["state"], got["passed"]), ("running", "auto"))
        self.assertIn(f"gh pr merge 40 -R {REPO} --squash --match-head-commit {H1}", got["say"])
        self.assertEqual(queue.gate(self.pol, 40, H1), {"merge": 40, "head": H1})
        self.assertFalse((self.root / "queue.json").read_text().count("merge"))

    def test_the_gate_stays_shut_without_a_pass_and_for_a_pr_the_queue_never_ran(self):
        self.add("a")
        self.to_working("a")
        self.pr_open("a", 40)
        self.pr_open("x", 41)
        self.assertEqual(self.shut(), f"PR 40 does not merge: the verifier has not passed head {H1}")
        self.assertEqual(self.shut(41), f"PR 41 does not merge: the verifier has not passed head {H1}")
        queue.verified(self.pol, "a", 40, H1, "pass", "auto")
        self.assertIsNone(self.shut())
        self.assertIn("its thread fixes the findings", queue.verified(self.pol, "a", 40, H1, "fail")["say"])
        self.assertEqual(self.shut(), f"PR 40 does not merge: the verifier has not passed head {H1}")

    def test_a_new_push_shuts_the_gate_until_the_verifier_passes_the_new_head(self):
        self.passed()
        self.forge.prs[40]["head"] = H2
        self.assertEqual(self.shut(), f"PR 40 is at {H2}, not {H1}")
        self.assertEqual(self.shut(head=H2), f"PR 40 does not merge: the verifier has not passed head {H2}")
        with self.assertRaisesRegex(queue.Refused, "the head moved"):
            queue.verified(self.pol, "a", 40, H1, "pass", "auto")
        queue.verified(self.pol, "a", 40, H2, "pass", "auto")
        self.assertIsNone(self.shut(head=H2))

    def test_a_review_pass_the_review_label_a_hold_a_draft_or_a_red_check_keeps_it_shut(self):
        self.assertEqual(self.passed(said="review")["say"], "its thread does not merge it: it needs a person's review")
        self.assertEqual(self.shut(), "PR 40 does not merge: it needs a person's review")
        self.passed(ident="b", pr=41, labels=[BASE["merge"]["review_label"]])
        self.assertEqual(self.shut(41), "PR 41 does not merge: it needs a person's review")
        self.passed(ident="c", pr=42)
        self.holds.write_text("hold | #42 | all | x\n")
        self.assertEqual(self.shut(42), "PR 42 does not merge: it has a hold row")
        self.holds.unlink()
        self.assertEqual(self.shut(42), "PR 42 does not merge: it has a hold row")
        self.holds.write_text("")
        self.forge.prs[42]["draft"] = True
        self.assertEqual(self.shut(42), "PR 42 does not merge: the PR is a draft")
        self.forge.prs[42].update(draft=False, ci="FAILURE")
        self.assertEqual(self.shut(42), f"PR 42 does not merge: {BASE['merge']['gate_check']} has not passed on that head")
        self.forge.prs[42].update(ci="SUCCESS", more=[{"name": "lint", "status": "IN_PROGRESS"}])
        self.assertEqual(self.shut(42), "PR 42 does not merge: a check is still running on that head, and a merge now would arm auto-merge")
        self.forge.prs[42]["more"] = [{"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"}, {"context": "legacy", "state": "SUCCESS"}]
        self.assertIsNone(self.shut(42))

    def test_one_pr_has_one_verdict_whichever_item_recorded_it(self):
        self.passed()
        self.add("b")
        self.to_working("b", thread="t-b")
        self.assertIsNone(self.shut())
        queue.verified(self.pol, "b", 40, H1, "fail")
        self.assertEqual(self.shut(), f"PR 40 does not merge: the verifier has not passed head {H1}")
        queue.verified(self.pol, "b", 40, H1, "pass", "review")
        self.assertEqual((self.shut(), self.items()["a"]["passed"]), ("PR 40 does not merge: it needs a person's review", None))

    def test_a_preapproved_item_or_head_merges_on_a_review_pass_but_never_past_a_hold(self):
        self.passed(said="review", preapproved=True)
        self.assertIsNone(self.shut())
        self.passed(said="review", ident="b", pr=41)
        self.holds.write_text(f"preapprove | #41 | all | {H2}\n")
        self.assertEqual(self.shut(41), "PR 41 does not merge: it needs a person's review")
        self.holds.write_text(f"preapprove | #41 | all | {H1}\nhold | #40 | all | x\n")
        self.assertEqual((self.shut(41), self.shut()), (None, "PR 40 does not merge: it has a hold row"))

    def test_an_items_own_thread_never_records_its_verdict(self):
        self.add("a")
        self.to_working("a", thread="t-a")
        self.pr_open("a", 40)
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-a"}), self.assertRaisesRegex(queue.Refused, "a's own thread"):
            queue.verified(self.pol, "a", 40, H1, "pass", "auto")
        self.assertIsNotNone(self.shut())
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-verifier"}):
            queue.verified(self.pol, "a", 40, H1, "pass", "auto")
        self.assertIsNone(self.shut())
        self.add("b")
        with self.assertRaisesRegex(queue.Refused, "b is queued, and no thread has run it"):
            queue.verified(self.pol, "b", 40, H1, "pass", "auto")


class Release(Case):
    def setUp(self):
        super().setUp()
        self.wakes = []
        p = patch.object(queue, "tell", lambda kind, ident, it: self.wakes.append((kind, ident, it["why"])) or ["sent"])
        p.start()
        self.addCleanup(p.stop)

    def test_a_turn_end_frees_the_slot_tells_the_orchestrator_and_a_late_verdict_still_opens_the_gate(self):
        self.pol["run"]["threads"] = 1
        self.add("a")
        self.to_working("a", thread="t-a")
        self.add("b")
        self.pr_open("a", 40)
        self.assertEqual(queue.next_up(self.pol)["running"], {"a": "t-a"})
        queue.verified(self.pol, "a", 40, H1, "fail")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-a"}):
            got = queue.release(self.pol)
        why = f"PR 40 waits for the owner: the verifier has not passed head {H1}"
        self.assertEqual((got["released"], got["state"], got["why"], self.wakes), ("a", "released", why, [("blocked", "a", why)]))
        self.assertEqual(queue.next_up(self.pol)["launch"]["id"], "b")
        queue.verified(self.pol, "a", 40, H1, "pass", "auto")
        self.assertEqual(queue.gate(self.pol, 40, H1), {"merge": 40, "head": H1})

    def test_the_hook_keeps_a_turn_going_while_its_prs_head_has_no_verdict_but_not_for_ever(self):
        self.add("a")
        self.to_working("a", thread="t-a")
        self.pr_open("a", 40)
        self.forge.prs[40]["branch"] = "a"
        env = patch.dict(os.environ, {queue.THREAD_VAR: "t-a"})
        with env, patch.object(queue, "branch_pr", return_value=40):
            with self.assertRaisesRegex(queue.Unfinished, "PR 40 has no verdict for its current head"):
                queue.release(self.pol, hook=True)
            self.assertEqual((self.items()["a"]["state"], self.wakes), ("running", []))
            with patch.dict(os.environ, {queue.THREAD_VAR: "t-verifier"}):
                queue.verified(self.pol, "a", 40, H1, "fail")
            self.assertEqual(queue.release(self.pol, hook=True)["state"], "released")
        self.add("b")
        self.to_working("b", thread="t-b")
        self.pr_open("b", 41)
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-b"}), patch.object(queue, "branch_pr", return_value=41):
            for _ in range(queue.HOLDS_BACK):
                with self.assertRaises(queue.Unfinished):
                    queue.release(self.pol, hook=True)
            self.assertEqual(queue.release(self.pol, hook=True)["state"], "released")
        self.add("c")
        self.to_working("c", thread="t-c")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-c"}):
            self.assertEqual(queue.release(self.pol, hook=True)["why"], "nothing to merge")

    def test_a_session_that_runs_no_item_releases_nothing_and_a_second_turn_end_tells_only_what_changed(self):
        self.add("a")
        self.to_working("a", thread="t-a")
        for thread in ("t-verifier", ""):
            with patch.dict(os.environ, {queue.THREAD_VAR: thread}):
                self.assertEqual(queue.release(self.pol), {"released": None})
        self.assertEqual(self.items()["a"]["state"], "running")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-a"}):
            self.assertEqual(queue.release(self.pol)["why"], "nothing to merge")
            queue.release(self.pol)
        self.assertEqual(self.wakes, [("done", "a", "nothing to merge")])
        with self.assertRaisesRegex(queue.Refused, "zzz is not queued, not running"):
            queue.release(self.pol, "zzz")

    def test_the_dispatcher_releases_a_dead_thread_by_id_and_a_merged_pr_is_done(self):
        self.add("a")
        self.to_working("a")
        self.pr_open("a", 40)
        queue.verified(self.pol, "a", 40, H1, "pass", "auto")
        self.forge.prs[40]["state"] = "MERGED"
        got = queue.release(self.pol, "a")
        self.assertEqual((got["why"], got["open"], self.wakes), ("PR 40 merged", False, [("done", "a", "PR 40 merged")]))
        self.add("b")
        self.to_working("b")
        self.items()
        with patch.object(queue, "branch_pr", side_effect=queue.Refused("cannot look up the PR of branch b: gh down")):
            got = queue.release(self.pol, "b")
        self.assertEqual((got["state"], got["open"], got["why"]), ("released", True, "cannot look up the PR of branch b: gh down"))
        with patch.object(queue, "IDLE_S", 0):
            self.assertEqual((self.pick([])["tidied"][0]["why"], self.items()["b"]["open"]), ("nothing to merge", False))

    def test_the_pickup_pass_tidies_a_released_items_worktree_once_it_has_been_idle(self):
        import subprocess

        def git(*a, cwd):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)
        up, clone = self.root / "up.git", self.root / "clone"
        git("init", "-q", "--bare", str(up), cwd=self.root)
        git("clone", "-q", str(up), str(clone), cwd=self.root)
        (clone / ".gitignore").write_text("build/\n")
        git("add", ".gitignore", cwd=clone)
        git("commit", "-q", "-m", "one", cwd=clone)
        git("push", "-q", "origin", "HEAD", cwd=clone)
        self.pol["host"]["clone"] = str(clone)
        self.pol["run"]["threads"] = 9
        for x in ("clean", "dirty", "ahead", "gone", "open"):
            wt = self.root / f"wt-{x}"
            if x != "gone":
                git("worktree", "add", "-q", "-b", x, str(wt), cwd=clone)
                (wt / "build").mkdir()
                (wt / "build" / "out.o").write_text("x")
            self.add(x)
            queue.step(self.pol, "start", x)
            queue.step(self.pol, "claim", x, thread=f"t-{x}", worktree=str(wt))
        (self.root / "wt-dirty" / "new.txt").write_text("x")
        git("commit", "-q", "--allow-empty", "-m", "two", cwd=self.root / "wt-ahead")
        self.forge.prs[50] = {"head": H1, "branch": "open"}
        for x in ("clean", "dirty", "ahead", "gone", "open"):
            queue.release(self.pol, x)
        self.assertEqual(self.items()["open"]["why"], f"PR 50 waits for the owner: the verifier has not passed head {H1}")
        self.assertEqual((self.pick([])["tidied"], (self.root / "wt-clean").exists()), ([], True))
        with patch.object(queue, "IDLE_S", 0):
            tidied = {x["id"]: x["worktree"] for x in self.pick([])["tidied"]}
        self.assertEqual(tidied, {"clean": None, "gone": None})
        self.assertEqual([(self.root / f"wt-{x}").exists() for x in ("clean", "dirty", "ahead", "open")], [False, True, True, True])
        self.assertEqual([(self.root / f"wt-{x}" / "build").exists() for x in ("dirty", "open")], [True, False])
        self.forge.prs[50]["state"] = "MERGED"
        self.assertEqual(self.pick([])["tidied"], [{"id": "open", "why": "PR 50 merged", "worktree": None}])
        self.assertFalse((self.root / "wt-open").exists())
        git("worktree", "add", "-q", "-b", "mine", str(self.root / "wt-mine"), cwd=clone)
        self.add("mine")
        queue.step(self.pol, "start", "mine")
        queue.step(self.pol, "claim", "mine", thread="t-mine", worktree=str(self.root / "wt-mine"))
        self.forge.prs[51] = {"head": H1, "branch": "mine", "state": "MERGED"}
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-mine"}):
            self.assertEqual(queue.release(self.pol)["why"], "PR 51 merged")
        self.assertFalse((self.root / "wt-mine").exists())

    def test_an_issue_with_a_released_item_is_never_picked_up_again_and_its_open_pr_blocks_an_overlap(self):
        self.pick([cand(1)])
        self.to_working("app-1")
        self.pr_open("app-1", 40)
        queue.verified(self.pol, "app-1", 40, H1, "pass", "review")
        queue.release(self.pol, "app-1")
        skipped = {x["id"]: x["why"] for x in self.pick([cand(1), cand(40, "review")])["skipped"]}
        self.assertEqual(skipped, {"app-1": "released", f"app-40-review-{H1[:7]}": "overlap"})

    def test_items_from_an_older_queue_load_as_running_or_released_and_keep_their_pass(self):
        old = {k: {**queue.item("task", None, "orchestrator", 0, "t", "t"), "state": s, "pr": pr, "head": H1, "auto": auto}
               for k, s, pr, auto in (("w", "working", None, False), ("p", "passed", 40, True), ("o", "owner", 41, False),
                                      ("d", "done", 42, False), ("v", "verifying", 43, False))}
        for it in old.values():
            del it["passed"], it["open"]
        (self.root / "queue.json").write_text(json.dumps({"items": old}))
        self.pr_open("p", 40)
        got = queue.locked(self.pol, lambda items: (items, False))
        self.assertEqual({k: (v["state"], v["passed"], v["open"]) for k, v in got.items()}, {
            "w": ("running", None, False), "p": ("released", "auto", True), "o": ("released", None, True),
            "d": ("released", None, False), "v": ("released", None, True)})
        self.assertEqual(queue.gate(self.pol, 40, H1), {"merge": 40, "head": H1})

    def test_the_steps_are_the_models(self):
        spec = (PKG / "model" / "Queue.tla").read_text()
        self.assertEqual(queue.STEPS, {"start": ("queued", "starting"), "claim": ("starting", "running"),
                                       "requeue": ("starting", "queued")})
        for name, to in (("Start(m, i)", "starting"), ("Claim(m)", "running"), ("Requeue(m)", "queued"), ("Release(m, i)", "released")):
            self.assertIn(f'"{to}")', spec[spec.index(f"\n{name} =="):].split("\n\n")[0], name)
        states = set(spec[spec.index("States == {") + 11:spec.index("}", spec.index("States == {"))].replace('"', "").split(", "))
        self.assertEqual(states - {"none"}, {*queue.ACTIVE, "queued", "released"})


class Cli(Case):
    def test_the_commands(self):
        out = io.StringIO()
        with patch.object(queue.policy, "load", return_value=self.pol), patch.object(queue, "tell", lambda *a: []), \
                patch("sys.stdout", out):
            self.assertEqual(queue.main(["add", "a", "--priority", "3"], stdin=io.StringIO("task")), 0)
            self.assertEqual(queue.main(["add", "b", "--priority"], stdin=io.StringIO("task")), 2)
            self.assertEqual(queue.main(["add", "c", "--preapproved", "--priority", "3"], stdin=io.StringIO("task")), 0)
            self.assertEqual(queue.main(["add", "d", "--preapproved", "--preapproved"], stdin=io.StringIO("task")), 2)
            self.assertEqual(queue.main(["start", "a"]), 0)
            self.assertEqual(queue.main(["claim", "a", "t-1", "/tmp/wt-a"]), 0)
            self.forge.prs[40] = {"head": H1}
            self.assertEqual(queue.main(["verified", "a", "pass", "auto"]), 2)
            self.assertEqual(queue.main(["verified", "a", "40", H1, "pass"]), 2)
            self.assertEqual(queue.main(["verified", "a", "40", H1, "pass", "extreme"]), 2)
            self.assertEqual(queue.main(["verified", "a", "40", H1[:7], "pass", "auto"]), 2)
            self.assertEqual(queue.main(["gate", "40", H1[:7]]), 2)
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["gate", "40", H1]), 1)
            self.assertEqual(err.getvalue(), f"queue: PR 40 does not merge: the verifier has not passed head {H1}\n")
            self.assertEqual(queue.main(["verified", "a", "40", H1, "pass", "auto"]), 0)
            self.assertEqual(queue.main(["gate", "40", H1]), 0)
            self.assertEqual(queue.main(["release", "--hook"]), 0)
            self.assertEqual(queue.main(["release"]), 0)
            self.assertEqual(queue.main(["release", "a"]), 0)
            self.assertEqual(queue.main(["release", "a", "b"]), 2)
            self.assertEqual(queue.main(["sweep"]), 2)
            self.assertEqual(queue.main(["list"]), 0)
            self.assertEqual(queue.main(["next"]), 0)
            self.assertEqual(queue.main(["nope"]), 2)
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["start", "zzz"]), 1)
            self.assertEqual(err.getvalue(), "queue: no item zzz\n")
        self.assertEqual(self.items()["a"]["state"], "released")


if __name__ == "__main__":
    unittest.main()
