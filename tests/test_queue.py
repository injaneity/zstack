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
            want = a[a.index("--state") + 1]
            return 0, json.dumps([{"number": n} for n, p in sorted(self.prs.items(), reverse=True) if p.get("branch") == a[a.index("--head") + 1]
                                  and (want == "all" or p.get("state", "OPEN") == "OPEN")][:1]), ""
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

    def to_working(self, ident, thread="t-1", worktree=None):
        return queue.claim(self.pol, ident, thread, worktree or f"/nowhere/wt-{ident}")

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
            self.to_working("low")
        got = self.to_working("high", thread="t-high", worktree="/w/high")
        self.assertEqual((got["state"], got["thread"], got["worktree"]), ("running", "t-high", "/w/high"))
        self.assertEqual(queue.next_up(self.pol)["launch"]["id"], "high-later")

    def test_a_claim_is_refused_past_the_thread_cap_and_for_an_item_already_running(self):
        for x in ("a", "b", "c"):
            self.add(x)
        self.to_working("a", "t-a")
        self.to_working("b", "t-b")
        got = queue.next_up(self.pol)
        self.assertEqual((got["launch"], got["waiting"], got["running"]), (None, "the machine is at its thread cap", {"a": "t-a", "b": "t-b"}))
        with self.assertRaisesRegex(queue.Refused, "c is not next: the machine is at its thread cap"):
            self.to_working("c")
        with self.assertRaisesRegex(queue.Refused, "a is running, not queued"):
            self.to_working("a")
        with self.assertRaisesRegex(queue.Refused, "zzz is not in the queue, not queued"):
            self.to_working("zzz")

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

    def test_a_turn_end_frees_the_slot_keeps_an_open_pr_as_review_and_a_late_verdict_still_opens_the_gate(self):
        self.pol["run"]["threads"] = 1
        self.add("a")
        self.to_working("a", thread="t-a")
        self.add("b")
        self.pr_open("a", 40)
        queue.verified(self.pol, "a", 40, H1, "fail")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-a"}):
            got = queue.release(self.pol)
        why = f"PR 40 waits for the owner: the verifier has not passed head {H1}"
        self.assertEqual((got["released"], got["state"], got["why"], self.wakes), ("a", "review", why, [("blocked", "a", why)]))
        self.assertEqual(queue.next_up(self.pol)["launch"]["id"], "b")
        queue.verified(self.pol, "a", 40, H1, "pass", "auto")
        self.assertEqual(queue.gate(self.pol, 40, H1), {"merge": 40, "head": H1})

    def test_a_finished_item_leaves_the_queue_and_is_not_picked_up_again_while_it_is_still_listed(self):
        self.pick([cand(1), cand(2)])
        self.to_working("app-1")
        self.to_working("app-2")
        self.pr_open("app-2", 40)
        self.forge.prs[40]["state"] = "MERGED"
        with patch.object(queue, "branch_pr", side_effect=[None, 40, 40]):
            got = [queue.release(self.pol, x) for x in ("app-1", "app-2")]
        self.assertEqual([(x["state"], x["why"]) for x in got], [("done", "nothing to merge"), ("done", "PR 40 merged")])
        self.assertEqual(self.wakes, [("done", "app-1", "nothing to merge"), ("done", "app-2", "PR 40 merged")])
        state = json.loads((self.root / "queue.json").read_text())
        self.assertEqual(state, {"items": {}, "done": ["app-1", "app-2"]})
        self.assertEqual(self.pick([cand(1)])["skipped"], [{"id": "app-1", "why": "done"}])
        self.assertEqual(json.loads((self.root / "queue.json").read_text())["done"], ["app-1"])
        self.pick([])
        self.assertEqual([x["id"] for x in self.pick([cand(1)])["queued"]], ["app-1"])

    def test_the_hook_keeps_a_turn_going_until_its_prs_head_has_a_verdict_or_the_pr_closes(self):
        self.add("a")
        self.to_working("a", thread="t-a")
        self.pr_open("a", 40)
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-a"}), patch.object(queue, "branch_pr", return_value=40):
            for _ in range(5):
                with self.assertRaisesRegex(queue.Unfinished, "PR 40 has no verdict for its current head"):
                    queue.release(self.pol, hook=True)
            self.assertEqual((self.items()["a"]["state"], self.wakes), ("running", []))
            with patch.dict(os.environ, {queue.THREAD_VAR: "t-verifier"}):
                queue.verified(self.pol, "a", 40, H1, "fail")
            self.assertEqual(queue.release(self.pol, hook=True)["state"], "review")
        self.add("b")
        self.to_working("b", thread="t-b")
        self.pr_open("b", 41)
        self.forge.prs[41]["state"] = "CLOSED"
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-b"}), patch.object(queue, "branch_pr", return_value=41):
            self.assertEqual(queue.release(self.pol, hook=True)["why"], "PR 41 closed")
        self.add("c")
        self.to_working("c", thread="t-c")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-c"}):
            self.assertEqual(queue.release(self.pol, hook=True)["why"], "nothing to merge")

    def test_a_session_that_runs_no_item_releases_nothing_and_the_dispatcher_releases_by_id(self):
        self.add("a")
        self.to_working("a", thread="t-a")
        for thread in ("t-verifier", ""):
            with patch.dict(os.environ, {queue.THREAD_VAR: thread}):
                self.assertEqual(queue.release(self.pol), {"released": None})
        self.assertEqual(self.items()["a"]["state"], "running")
        self.assertEqual(queue.release(self.pol, "a")["state"], "done")
        for bad in ("a", "zzz"):
            with self.assertRaisesRegex(queue.Refused, f"{bad} is not in the queue, not running"):
                queue.release(self.pol, bad)

    def test_a_failed_lookup_keeps_the_item_as_review_and_the_pickup_tries_again(self):
        self.add("a")
        self.to_working("a")
        with patch.object(queue, "branch_pr", side_effect=queue.Refused("cannot look up the PR of branch a: gh down")):
            got = queue.release(self.pol, "a")
        self.assertEqual((got["state"], got["why"]), ("review", "cannot look up the PR of branch a: gh down"))
        self.assertEqual(self.pick([])["cleared"], [{"id": "a", "why": "nothing to merge"}])
        self.assertEqual(self.items(), {})

    def test_a_finished_items_worktree_goes_with_it_unless_work_in_it_is_not_on_a_remote(self):
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
            self.to_working(x, thread=f"t-{x}", worktree=str(wt))
        (self.root / "wt-dirty" / "new.txt").write_text("x")
        git("commit", "-q", "--allow-empty", "-m", "two", cwd=self.root / "wt-ahead")
        self.forge.prs[50] = {"head": H1, "branch": "open"}
        self.forge.prs[51] = {"head": H2, "branch": "open", "state": "CLOSED"}
        git("worktree", "add", "-q", "--detach", str(self.root / "wt-loose"), cwd=clone)
        self.add("loose")
        self.to_working("loose", thread="t-loose", worktree=str(self.root / "wt-loose"))
        loose = queue.release(self.pol, "loose")
        self.assertEqual((loose["state"], "is on no branch" in loose["why"], (self.root / "wt-loose").exists()), ("review", True, True))
        with patch.object(queue, "git", lambda *a: (1, "")):
            self.assertEqual(queue.release(self.pol, "loose")["why"], f"cannot list the worktrees of {os.path.realpath(clone)}")
        got = {x: queue.release(self.pol, x) for x in ("clean", "dirty", "ahead", "gone", "open")}
        self.assertEqual({x: v["state"] for x, v in got.items()},
                         {"clean": "done", "dirty": "review", "ahead": "review", "gone": "done", "open": "review"})
        self.assertEqual(got["dirty"]["why"], "nothing to merge, and its worktree has uncommitted or unpushed work")
        self.assertEqual([(self.root / f"wt-{x}").exists() for x in ("clean", "dirty", "ahead", "open")], [False, True, True, True])
        self.assertEqual([(self.root / f"wt-{x}" / "build").exists() for x in ("dirty", "open")], [True, False])
        self.forge.prs[50]["state"] = "CLOSED"
        self.forge.prs[52] = {"head": H2, "branch": "open"}
        self.assertEqual((self.pick([])["cleared"], self.items()["open"]["pr"], (self.root / "wt-open").exists()), ([], 52, True))
        self.forge.prs[52]["state"] = "MERGED"
        (self.root / "wt-dirty" / "new.txt").unlink()
        self.assertEqual(self.pick([])["cleared"], [{"id": "dirty", "why": "nothing to merge"}, {"id": "open", "why": "PR 52 merged"}])
        left = [(self.root / "wt-open").exists(), (self.root / "wt-dirty").exists()]
        self.assertEqual((sorted(self.items()), left), (["ahead", "loose"], [False, False]))

    def test_a_queue_file_from_an_older_zstack_is_refused_by_name(self):
        for old in ({"items": {}}, {"items": {"a": {"state": "passed"}}, "done": []}):
            (self.root / "queue.json").write_text(json.dumps(old))
            with self.assertRaisesRegex(queue.Refused, "queue.json was written by an older zstack: move it aside"):
                queue.next_up(self.pol)

    def test_an_open_pr_under_review_blocks_an_overlapping_pickup(self):
        self.pick([cand(1)])
        self.to_working("app-1")
        self.pr_open("app-1", 40)
        queue.verified(self.pol, "app-1", 40, H1, "pass", "review")
        queue.release(self.pol, "app-1")
        skipped = {x["id"]: x["why"] for x in self.pick([cand(1), cand(40, "review")])["skipped"]}
        self.assertEqual(skipped, {"app-1": "review", f"app-40-review-{H1[:7]}": "overlap"})

    def test_the_states_are_the_models(self):
        spec = (PKG / "model" / "Queue.tla").read_text()
        for name, to in (("Claim(m, i)", "running"), ("Release(m, i)", "review"), ("Release(m, i)", "done"), ("Settle(m, i)", "done")):
            self.assertIn(f'"{to}")', spec[spec.index(f"\n{name} =="):].split("\n\n")[0].split("\nSettle")[name == "Settle(m, i)"], name)
        states = set(spec[spec.index("States == {") + 11:spec.index("}", spec.index("States == {"))].replace('"', "").split(", "))
        self.assertEqual(states, {"none", "queued", "running", "review", "done"})


class Cli(Case):
    def test_the_commands(self):
        out = io.StringIO()
        with patch.object(queue.policy, "load", return_value=self.pol), patch.object(queue, "tell", lambda *a: []), \
                patch("sys.stdout", out):
            self.assertEqual(queue.main(["add", "a", "--priority", "3"], stdin=io.StringIO("task")), 0)
            self.assertEqual(queue.main(["add", "b", "--priority"], stdin=io.StringIO("task")), 2)
            self.assertEqual(queue.main(["add", "c", "--preapproved", "--priority", "3"], stdin=io.StringIO("task")), 0)
            self.assertEqual(queue.main(["add", "d", "--preapproved", "--preapproved"], stdin=io.StringIO("task")), 2)
            self.assertEqual(queue.main(["start", "a"]), 2)
            self.assertEqual(queue.main(["claim", "a", "t-1"]), 2)
            self.assertEqual(queue.main(["claim", "a", "t-1", "/nowhere/wt-a"]), 0)
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
            self.assertEqual(queue.main(["release", "a"]), 0)
            self.assertEqual(queue.main(["release"]), 0)
            self.assertEqual(queue.main(["release", "a", "b"]), 2)
            self.assertEqual(queue.main(["sweep"]), 2)
            self.assertEqual(queue.main(["list"]), 0)
            self.assertEqual(queue.main(["next"]), 0)
            self.assertEqual(queue.main(["nope"]), 2)
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["claim", "zzz", "t", "/w"]), 1)
            self.assertEqual(err.getvalue(), "queue: zzz is not in the queue, not queued\n")
        self.assertEqual(self.items()["a"]["state"], "review")


if __name__ == "__main__":
    unittest.main()
