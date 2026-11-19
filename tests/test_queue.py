import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

for _k in ("ZSTACK_CONFIG", "ZSTACK_MACHINE", "ZSTACK_REPO", "ONIGIRI_ROOT"):
    os.environ.pop(_k, None)

PKG = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("queue_under_test", PKG / "queue.py")
queue = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(queue)
BASE = queue.load()
REPO, M = BASE["merge"]["repo"], BASE["merge"]
READS = (queue.candidates, queue.open_prs)
LAUNCH, T3 = queue.launch, queue.t3
H1, H2 = "a" * 40, "b" * 40


def cand(n, kind="issue", author="someone", labels=(), linked=(), created="2026-01-01"):
    ident = f"app-{n}" if kind == "issue" else f"app-{n}-review-{H1[:7]}"
    return dict(id=ident, kind=kind, n=n, head=None if kind == "issue" else H1, author={"login": author}, title=f"t{n}", body="b",
                url=f"u{n}", created=created, labels=list(labels), linked=list(linked))


class Forge:
    def __init__(self):
        self.prs, self.issues, self.calls = {}, {}, []

    def gh(self, *a, stdin=None):
        self.calls.append(a)
        if a[:2] == ("pr", "view"):
            p = self.prs[int(a[2])]
            return 0, json.dumps({"number": int(a[2]), "state": p.get("state", "OPEN"), "headRefOid": p["head"], "isDraft": p.get("draft", False),
                                  "labels": [{"name": x} for x in p.get("labels", [])],
                                  "statusCheckRollup": [{"name": M["gate_check"], "conclusion": p.get("ci", "SUCCESS")}, *p.get("more", [])]}), ""
        if a[:2] == ("pr", "list"):
            want = a[a.index("--state") + 1]
            return 0, json.dumps([{"number": n} for n, p in sorted(self.prs.items(), reverse=True) if p.get("branch") == a[a.index("--head") + 1]
                                  and (want == "all" or p.get("state", "OPEN") == "OPEN")][:1]), ""
        if a[:2] == ("issue", "view"):
            if int(a[2]) not in self.issues:
                return 1, "", "issue not found"
            return 0, json.dumps({"labels": [{"name": x} for x in self.issues[int(a[2])]]}), ""
        raise AssertionError(a)


class Case(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(td.name)
        self.pol = json.loads(json.dumps(BASE))
        self.pol["host"]["root"] = str(self.root)
        self.pol["run"]["threads"] = 2
        self.forge, self.cands, self.open, self.wakes, self.woken = Forge(), [], {}, [], []
        self.threads, self.briefed, self.status, self.t3_down, self.listing = {}, [], {}, False, []
        for p in (patch.object(queue, "gh", self.forge.gh),
                  patch.object(queue, "candidates", lambda errors, pol: list(self.cands)),
                  patch.object(queue, "open_prs", lambda errors, name: dict(self.open)),
                  patch.object(queue, "wake", self.wake), patch.object(queue, "launch", self.launch), patch.object(queue, "t3", self.t3)):
            p.start()
            self.addCleanup(p.stop)

    def wake(self, pol, kind, said, pr=None, thread=None):
        self.wakes.append((kind, said, pr))
        self.woken.append(thread)
        return "sent"

    def launch(self, pol, up):
        self.briefed.append(up)
        return self.threads.get(up["id"], (f"t-{up['id']}", f"/nowhere/wt-{up['id']}"))

    def t3(self, pol, *args, stdin=None):
        if self.t3_down:
            return "connection refused"
        if args[0] == "list":
            return list(self.listing)
        said = self.status.get(args[1], "running")
        return "Thread is no longer available." if said == "gone" else {"thread": {"status": said, "worktreePath": f"/seen/{args[1]}"}}

    def file(self):
        return json.loads((self.root / "queue.json").read_text())

    def add(self, ident, text="do the thing", priority=0, preapproved=False):
        return queue.add(self.pol, ident, text, priority, preapproved)

    def start(self, ident, thread=None, worktree=None):
        self.threads[ident] = (thread or f"t-{ident}", worktree or f"/nowhere/wt-{ident}")
        got = queue.step(self.pol)
        self.assertEqual(got["launched"] and got["launched"]["id"], ident, got["waiting"])
        return self.file()["running"][ident]

    def run_(self, ident, thread=None, worktree=None, **task):
        self.add(ident, **task)
        return self.start(ident, thread, worktree)

    def pr_open(self, pr, head=H1, labels=()):
        self.forge.prs[pr] = {"head": head, "labels": list(labels)}


class Entry(Case):
    def test_a_step_reads_the_repo_launches_the_first_and_counts_why_it_passed_over_the_rest(self):
        hold = M["hold_label"]
        self.cands = [cand(1), cand(7, "review", labels=[hold]), cand(2, labels=[hold]), cand(3, linked=[70]), cand(4, author="app/App-agents"),
                      cand(5, author="x[bot]"), cand(6, linked=[9])]
        self.open = {9: [], 70: [hold]}
        got = queue.step(self.pol)
        self.assertEqual((got["launched"], got["waiting"]), ({"id": "app-1", "thread": "t-app-1", "worktree": "/nowhere/wt-app-1"}, None))
        self.assertEqual(got["skipped"], {"held": 3, "skipped-author": 2, "pr": 1})
        self.assertEqual(sorted(self.file()["running"]), ["app-1"])

    def test_a_failed_read_launches_nothing_and_says_why(self):
        with patch.object(queue, "candidates", READS[0]), patch.object(queue, "open_prs", READS[1]), \
                patch.object(queue, "gh", lambda *a, **k: (1, "", "gh down")):
            self.add("a")
            got = queue.step(self.pol)
            self.assertEqual((got["launched"], got["waiting"], self.briefed), (None, "gh issue list -R: gh down", []))
            self.assertEqual((self.file()["running"], sorted(self.file()["tasks"])), ({}, ["a"]))

    def test_the_repo_is_read_for_our_issues_our_review_requests_and_every_open_prs_labels(self):
        rows = {("issue", "list"): [{"number": 3, "title": "t", "body": None, "url": "u", "createdAt": "2026-01-02", "labels": [{"name": "bug"}],
                                     "closedByPullRequestsReferences": [{"number": 8}], "author": {"login": "x"}}],
                ("pr", "list"): [{"number": 8, "title": "p", "body": "b", "url": "u", "createdAt": "2026-01-01",
                                  "labels": [{"name": M["hold_label"]}],
                                  "headRefOid": H1, "author": {"login": "y"}}]}
        with patch.object(queue, "candidates", READS[0]), patch.object(queue, "open_prs", READS[1]), \
                patch.object(queue, "gh", lambda *a, **k: (0, json.dumps(rows[a[:2]]), "")):
            errors = []
            got = queue.candidates(errors, self.pol)
            self.assertEqual([(c["id"], c["labels"], c["linked"]) for c in got],
                             [(f"app-8-review-{H1[:7]}", [M["hold_label"]], []), ("app-3", ["bug"], [8])])
            self.assertEqual((queue.open_prs(errors, REPO), errors), ({8: [M["hold_label"]]}, []))
            self.assertEqual(queue.step(self.pol)["skipped"], {"held": 2})

    def test_a_machine_takes_its_labels_one_takes_the_unlabelled_and_an_item_with_two_goes_to_one(self):
        mac = {"labels": ["macOS"], "unlabelled": False, "all_labels": ["Linux", "macOS"]}
        linux = {"labels": ["Linux"], "unlabelled": True, "all_labels": ["Linux", "macOS"]}
        for labels, on_mac, on_linux in ((["macOS"], True, False), (["Linux"], False, True), (["bug"], False, True), ([], False, True),
                                         (["macOS", "Linux"], False, True)):
            self.assertEqual((queue.takes(labels, mac), queue.takes(labels, linux)), (on_mac, on_linux), labels)
        self.assertTrue(queue.takes(["Linux"], {"labels": [], "unlabelled": False, "all_labels": []}))
        self.pol["host"].update(mac)
        self.cands = [cand(1, labels=["Linux"])]
        self.assertEqual(queue.step(self.pol)["skipped"], {"other-machine": 1})

    def test_the_orchestrator_adds_a_task_with_a_priority(self):
        got = self.add("ship-docs", "Write the docs\nmore", priority=5)
        self.assertEqual((got["priority"], got["title"], got["preapproved"]), (5, "Write the docs", False))
        self.assertEqual(sorted(self.file()["tasks"]), ["ship-docs"])
        for bad in (("ship-docs", "again"), ("bad id", "x"), ("empty", "  ")):
            with self.subTest(bad=bad), self.assertRaises(queue.Refused):
                self.add(*bad)

    def test_a_review_brief_follows_the_repos_preference_and_a_teammate_is_never_salvaged(self):
        self.pol["people"]["review_only"] = ["Mate"]
        outside, mate = queue.task(cand(5, "review", author="guest"), self.pol), queue.task(cand(6, "review", author="mate"), self.pol)
        for want in ("salvage a fixable PR", "`Salvaged from #5`", "keeps guest's commits as authored", "Never re-author"):
            self.assertIn(want, outside)
        self.assertIn("review-only: a first review and nothing else", mate)
        self.assertNotIn("alvage", mate)
        self.cands = [cand(6, "review", author="mate"), cand(5, "review", author="guest", created="2026-02-01"),
                      cand(4, author="mate", created="2026-03-01")]
        self.pol["run"]["threads"] = 9
        for c in self.cands:
            self.start(c["id"])
        self.assertEqual([v["readonly"] for _, v in sorted(self.file()["running"].items())], [False, False, True])
        self.pol["review"]["outside"] = "request-changes"
        sent_back = queue.task(cand(5, "review", author="guest"), self.pol)
        self.assertIn("request changes on a fixable PR", sent_back)
        self.assertNotIn("Salvaged from", sent_back)


class Launch(Case):
    def test_the_highest_priority_goes_first_then_the_oldest_and_an_urgent_label_counts(self):
        self.add("low", priority=1)
        self.add("high", priority=9)
        self.cands = [cand(1, created="2025-01-01"), cand(2, labels=["urgent"])]
        got = self.start("app-2", "t-2", "/w/2")
        self.assertEqual((got["thread"], got["worktree"], got["n"], got["kind"]), ("t-2", "/w/2", 2, "issue"))
        got = queue.step(self.pol)
        self.assertEqual((got["launched"]["id"], got["skipped"]), ("high", {"running": 1}))
        self.assertEqual([up["id"] for up in self.briefed], ["app-2", "high"])

    def test_nothing_is_launched_past_the_thread_cap_and_a_running_item_is_never_launched_again(self):
        for x in ("a", "b"):
            self.run_(x)
        self.add("c")
        self.cands = [cand(1)]
        got = queue.step(self.pol)
        self.assertEqual((got["launched"], got["waiting"]), (None, "the machine is at its thread cap"))
        self.assertEqual((sorted(self.file()["running"]), len(self.briefed)), (["a", "b"], 2))
        self.pol["run"]["threads"] = 9
        self.assertEqual([queue.step(self.pol)["launched"]["id"] for _ in range(2)], ["app-1", "c"])
        got = queue.step(self.pol)
        self.assertEqual((got["launched"], got["waiting"], got["skipped"], len(self.briefed)), (None, "nothing is ready", {"running": 1}, 4))

    def test_an_item_is_recorded_before_its_thread_starts_and_a_start_that_failed_is_launched_again(self):
        self.add("a")
        seen = []

        def down(pol, up):
            seen.append(json.loads((self.root / "queue.json").read_text())["running"]["a"]["thread"])
            raise queue.Refused("no thread started for a: t3 down")
        with patch.object(queue, "launch", down):
            got = queue.step(self.pol)
        said = "no thread started for a: t3 down; a later pass takes the thread if one did start, and launches a again if none shows"
        self.assertEqual((got["launched"], got["waiting"], seen), (None, said, [None]))
        self.assertEqual((self.file()["running"]["a"]["thread"], sorted(self.file()["tasks"])), (None, ["a"]))
        self.assertEqual((queue.step(self.pol)["waiting"], self.briefed), ("nothing is ready", []))
        self.listing = [{"threadId": "t-old", "title": "a", "createdAt": "2020-01-01T00:00:00.000Z"}]
        self.assertEqual((queue.run_pass(self.pol)["launched"], self.file()["running"]["a"]["thread"], self.briefed), ([], None, []))
        self.pol["run"]["lost_min"] = 0
        self.assertEqual(queue.run_pass(self.pol)["launched"], [{"id": "a", "thread": "t-a", "worktree": "/nowhere/wt-a"}])
        self.assertEqual((self.file()["running"]["a"]["thread"], self.file()["tasks"], len(self.briefed)), ("t-a", {}, 1))

    def test_a_start_whose_answer_was_lost_is_found_by_its_title_and_never_started_twice(self):
        self.add("a")
        for lost in (queue.Refused("no thread started for a: TimeoutExpired"), RuntimeError("the pass died")):
            with patch.object(queue, "launch", side_effect=lost):
                try:
                    queue.step(self.pol)
                except RuntimeError:
                    pass
            self.listing = [{"threadId": "t-lost", "title": "a", "createdAt": "2999-01-01T00:00:00.000Z"},
                            {"threadId": "t-other", "title": "b", "createdAt": "2999-01-01T00:00:00.000Z"}]
            got = queue.run_pass(self.pol)
            self.assertEqual((got["launched"], self.briefed), ([], []))
            it = self.file()["running"]["a"]
            self.assertEqual((it["thread"], it["worktree"], self.file()["tasks"]), ("t-lost", "/seen/t-lost", {}))
            self.status = {"t-lost": "completed"}
            self.assertEqual([x["released"] for x in queue.run_pass(self.pol)["released"]], ["a"])
            self.status, self.listing = {}, []
            self.add("a")
        with patch.object(queue, "now", lambda ms=False: "2026-01-01T00:00:00.500Z" if ms else "2026-01-01T00:00:00Z"), \
                patch.object(queue, "launch", side_effect=queue.Refused("lost")):
            queue.step(self.pol)
        self.listing = [{"threadId": "t-new", "title": "a", "createdAt": "2026-01-01T00:00:00.500Z"},
                        {"threadId": "t-before", "title": "a", "createdAt": "2026-01-01T00:00:00.499Z"}]
        queue.run_pass(self.pol)
        self.assertEqual((self.file()["running"]["a"]["thread"], self.briefed), ("t-new", []))

    def test_a_claimed_task_leaves_the_tasks_and_an_issues_preapproval_is_read_at_the_claim(self):
        self.run_("a", preapproved=True)
        self.cands = [cand(1, labels=M["preapproved_labels"][:1])]
        self.start("app-1", "t-1", "/w/1")
        got = self.file()
        self.assertEqual((got["tasks"], [got["running"][x]["preapproved"] for x in ("a", "app-1")]), ({}, [True, True]))

    def test_the_brief_tells_the_thread_how_to_report(self):
        self.add("a", "Fix the parser")
        queue.step(self.pol)
        said = queue.brief(self.pol, self.briefed[0])
        for want in ("# a", "Fix the parser", f"{PKG}/queue.py verified a <pr number> <head sha> auto",
                     "one line under `say`", "That line decides what you do, whatever else the verifier wrote", "ZSTACK_REPO=acme/app ",
                     "they are yours to judge:\n- You write the code and a short test plan",
                     "\n- No invented limits: a restriction must trace to Robin's words", "\n- Never revert a PR, except"):
            self.assertIn(want, said)
        self.assertNotIn("{", said)


class Gate(Case):
    def setUp(self):
        super().setUp()
        self.pol["run"]["threads"] = 9

    def passed(self, said="auto", labels=(), ident="a", pr=40, preapproved=False):
        self.run_(ident, preapproved=preapproved)
        self.pr_open(pr, labels=labels)
        return queue.verified(self.pol, ident, pr, H1, said)

    def shut(self, pr=40, head=H1):
        try:
            queue.gate(self.pol, pr, head)
        except queue.Refused as e:
            return str(e)

    def test_the_gate_opens_for_the_head_the_verifier_passed_as_auto_with_the_check_green(self):
        got = self.passed()
        self.assertEqual(got["say"], f"its thread merges it with `gh pr merge 40 -R {REPO} --squash --match-head-commit {H1}`")
        self.assertEqual(got["recorded"], "auto")
        self.assertEqual(self.file()["verdicts"], {"40": {"head": H1, "result": "auto"}})
        self.assertEqual(queue.gate(self.pol, 40, H1), {"merge": 40, "head": H1})

    def test_the_gate_stays_shut_without_a_pass_and_for_a_pr_the_queue_never_ran(self):
        self.run_("a")
        self.pr_open(40)
        self.pr_open(41)
        self.assertEqual(self.shut(), f"PR 40 does not merge: the verifier has not passed head {H1}")
        self.assertEqual(self.shut(41), f"PR 41 does not merge: the verifier has not passed head {H1}")
        queue.verified(self.pol, "a", 40, H1, "auto")
        self.assertIsNone(self.shut())
        self.assertIn("its thread fixes the findings", queue.verified(self.pol, "a", 40, H1, "fail")["say"])
        self.assertEqual(self.shut(), f"PR 40 does not merge: the verifier has not passed head {H1}")

    def test_a_new_push_shuts_the_gate_until_the_verifier_passes_the_new_head(self):
        self.passed()
        self.forge.prs[40]["head"] = H2
        self.assertEqual(self.shut(), f"PR 40 is at {H2}, not {H1}")
        self.assertEqual(self.shut(head=H2), f"PR 40 does not merge: the verifier has not passed head {H2}")
        with self.assertRaisesRegex(queue.Refused, "the head moved"):
            queue.verified(self.pol, "a", 40, H1, "auto")
        queue.verified(self.pol, "a", 40, H2, "auto")
        self.assertIsNone(self.shut(head=H2))

    def test_a_review_pass_the_review_label_a_draft_or_a_red_check_keeps_it_shut(self):
        self.assertEqual(self.passed(said="review")["say"], "its thread does not merge it: it needs a person's review")
        self.assertEqual(self.shut(), "PR 40 does not merge: it needs a person's review")
        self.passed(ident="b", pr=41, labels=[M["review_label"]])
        self.assertEqual(self.shut(41), "PR 41 does not merge: it needs a person's review")
        self.run_("c")
        self.pr_open(42)
        self.forge.prs[42]["ci"] = "PENDING"
        waits = queue.verified(self.pol, "c", 42, H1, "auto")["say"]
        self.assertRegex(waits, rf"^its thread merges it with `gh pr merge 42 .*`, but not yet: {M['gate_check']} has not passed on that head$")
        self.forge.prs[42].update(ci="SUCCESS", draft=True)
        self.assertEqual(self.shut(42), "PR 42 does not merge: the PR is a draft")
        self.forge.prs[42].update(draft=False, ci="FAILURE")
        self.assertEqual(self.shut(42), f"PR 42 does not merge: {M['gate_check']} has not passed on that head")
        self.forge.prs[42].update(ci="SUCCESS", more=[{"name": "lint", "status": "IN_PROGRESS"}])
        self.assertEqual(self.shut(42), "PR 42 does not merge: a check is still running on that head, and a merge now would arm auto-merge")
        self.forge.prs[42]["more"] = [{"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE"}, {"context": "legacy", "state": "SUCCESS"}]
        self.assertIsNone(self.shut(42))

    def test_a_hold_label_on_the_pr_or_on_its_issue_keeps_it_shut_and_an_unreadable_issue_counts_as_held(self):
        hold = M["hold_label"]
        self.passed(preapproved=True, said="review")
        self.forge.prs[40]["labels"] = [hold]
        self.assertEqual(self.shut(), f"PR 40 does not merge: it is labelled {hold}")
        self.cands = [cand(1)]
        self.start("app-1", "t-1", "/w/1")
        self.pr_open(41)
        self.forge.issues[1] = []
        queue.verified(self.pol, "app-1", 41, H1, "auto")
        self.assertIsNone(self.shut(41))
        self.forge.issues[1] = [hold]
        self.assertEqual(self.shut(41), f"PR 41 does not merge: it is labelled {hold}")
        del self.forge.issues[1]
        self.assertEqual(self.shut(41), "cannot read issue 1, so whether it is held is unknown: issue not found")

    def test_one_pr_has_one_verdict_whichever_item_recorded_it(self):
        self.passed()
        self.run_("b")
        self.assertIsNone(self.shut())
        queue.verified(self.pol, "b", 40, H1, "fail")
        self.assertEqual(self.shut(), f"PR 40 does not merge: the verifier has not passed head {H1}")
        queue.verified(self.pol, "b", 40, H1, "review")
        self.assertEqual((self.shut(), len(self.file()["verdicts"])), ("PR 40 does not merge: it needs a person's review", 1))

    def test_a_verdict_goes_when_its_pr_has_no_item_and_a_pr_no_item_owns_never_merges(self):
        self.passed()
        self.pr_open(41)
        queue.verified(self.pol, "a", 41, H1, "auto")
        self.assertEqual((sorted(self.file()["verdicts"]), self.shut(), self.shut(41)),
                         (["41"], f"PR 40 does not merge: the verifier has not passed head {H1}", None))
        stale = {**self.file(), "verdicts": {"40": {"head": H1, "result": "auto"}, "41": {"head": H1, "result": "auto"}}}
        (self.root / "queue.json").write_text(json.dumps(stale))
        self.assertEqual(self.shut(), f"PR 40 does not merge: the verifier has not passed head {H1}")
        self.forge.prs[41]["state"] = "MERGED"
        with patch.object(queue, "branch_pr", return_value=41):
            self.assertEqual(queue.release(self.pol, "a")["state"], "done")
        self.assertEqual(self.file()["verdicts"], {})

    def test_a_preapproved_item_merges_on_a_review_pass_or_the_review_label_but_never_on_a_fail(self):
        self.passed(said="review", preapproved=True)
        self.assertIsNone(self.shut())
        self.passed(said="auto", ident="b", pr=41, labels=[M["review_label"]], preapproved=True)
        self.assertIsNone(self.shut(41))
        queue.verified(self.pol, "b", 41, H1, "fail")
        self.assertEqual(self.shut(41), f"PR 41 does not merge: the verifier has not passed head {H1}")

    def test_an_items_own_thread_never_records_its_verdict_and_nor_does_an_item_nobody_ran(self):
        self.run_("a", thread="t-a")
        self.pr_open(40)
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-a"}), self.assertRaisesRegex(queue.Refused, "a's own thread"):
            queue.verified(self.pol, "a", 40, H1, "auto")
        self.assertIsNotNone(self.shut())
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-verifier"}):
            queue.verified(self.pol, "a", 40, H1, "auto")
        self.assertIsNone(self.shut())
        self.add("b")
        with self.assertRaisesRegex(queue.Refused, "b is not running or waiting on its PR, so no thread has run it"):
            queue.verified(self.pol, "b", 40, H1, "auto")


class Release(Case):
    def test_a_turn_end_frees_the_slot_keeps_an_open_pr_waiting_and_a_late_verdict_still_opens_the_gate(self):
        self.pol["run"]["threads"] = 1
        self.run_("a", thread="t-a")
        self.add("b")
        self.pr_open(40)
        queue.verified(self.pol, "a", 40, H1, "fail")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-a"}):
            got = queue.release(self.pol)
        why = f"PR 40 waits for the owner: the verifier has not passed head {H1}"
        self.assertEqual((got["released"], got["state"], got["why"], self.wakes), ("a", "waiting", why, [("blocked", f"a: {why}", 40)]))
        self.assertEqual((sorted(self.file()["waiting"]), self.file()["running"], self.woken), (["a"], {}, ["t-a"]))
        self.assertEqual(queue.step(self.pol)["launched"]["id"], "b")
        queue.verified(self.pol, "a", 40, H1, "auto")
        self.assertEqual(queue.gate(self.pol, 40, H1), {"merge": 40, "head": H1})
        self.assertEqual(len(self.wakes), 1)

    def test_a_finished_item_is_forgotten_and_is_not_picked_up_again_while_the_repo_still_lists_it(self):
        self.cands = [cand(1), cand(2)]
        self.start("app-1", "t-1", "/nowhere/1")
        self.start("app-2", "t-2", "/nowhere/2")
        self.pr_open(40)
        self.forge.prs[40]["state"] = "MERGED"
        with patch.object(queue, "branch_pr", side_effect=[None, 40, 40]):
            got = [queue.release(self.pol, x) for x in ("app-1", "app-2")]
        self.assertEqual([(x["state"], x["why"]) for x in got], [("done", "nothing to merge"), ("done", "PR 40 merged")])
        self.assertEqual(self.wakes, [("done", "app-1: nothing to merge", None), ("done", "app-2: PR 40 merged", 40)])
        self.assertEqual(self.file(), {"running": {}, "waiting": {}, "tasks": {}, "verdicts": {}, "done": ["app-1", "app-2"]})
        self.cands = [cand(1)]
        got = queue.step(self.pol)
        self.assertEqual((got["launched"], got["skipped"], self.file()["done"]), (None, {"done": 1}, ["app-1"]))
        self.cands = []
        queue.step(self.pol)
        self.cands = [cand(1)]
        self.assertEqual(queue.step(self.pol)["launched"]["id"], "app-1")

    def test_the_hook_keeps_a_turn_going_until_its_prs_head_has_a_verdict_or_the_pr_closes(self):
        self.pol["run"]["threads"] = 9
        self.run_("a", thread="t-a")
        self.pr_open(40)
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-a"}), patch.object(queue, "branch_pr", return_value=40):
            for _ in range(5):
                with self.assertRaisesRegex(queue.Unfinished, "PR 40 has no verdict for its current head"):
                    queue.release(self.pol, hook=True)
            self.assertEqual((sorted(self.file()["running"]), self.wakes), (["a"], []))
            with patch.dict(os.environ, {queue.THREAD_VAR: "t-verifier"}):
                queue.verified(self.pol, "a", 40, H1, "fail")
            self.assertEqual(queue.release(self.pol, hook=True)["state"], "waiting")
        self.run_("e", thread="t-e")
        self.forge.prs[60] = {"head": H1, "branch": "e", "state": "CLOSED"}
        self.forge.prs[61] = {"head": H2, "branch": "e"}
        queue.verified(self.pol, "e", 60, H1, "auto")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-e"}), patch.object(queue, "branch_pr", return_value=61):
            with self.assertRaisesRegex(queue.Unfinished, "PR 61 has no verdict for its current head"):
                queue.release(self.pol, hook=True)
        self.run_("d", thread="t-d")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-d"}), patch.object(queue, "branch_pr", side_effect=queue.Refused("gh down")):
            with self.assertRaisesRegex(queue.Unfinished, r"cannot be told \(gh down\), so your turn is not over"):
                queue.release(self.pol, hook=True)
            self.assertEqual(queue.release(self.pol)["state"], "waiting")
        self.run_("b", thread="t-b")
        self.pr_open(41)
        self.forge.prs[41]["state"] = "CLOSED"
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-b"}), patch.object(queue, "branch_pr", return_value=41):
            self.assertEqual(queue.release(self.pol, hook=True)["why"], "PR 41 closed")
        self.run_("c", thread="t-c")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-c"}):
            self.assertEqual(queue.release(self.pol, hook=True)["why"], "nothing to merge")

    def test_a_session_that_runs_no_item_releases_nothing_and_an_id_releases_that_item(self):
        self.run_("a", thread="t-a")
        for thread in ("t-verifier", ""):
            with patch.dict(os.environ, {queue.THREAD_VAR: thread}):
                self.assertEqual(queue.release(self.pol), {"released": None})
        self.assertEqual(sorted(self.file()["running"]), ["a"])
        self.assertEqual(queue.release(self.pol, "a")["state"], "done")
        for bad in ("a", "zzz"):
            with self.assertRaisesRegex(queue.Refused, f"{bad} is not running or waiting on its PR"):
                queue.release(self.pol, bad)

    def test_a_failed_lookup_keeps_the_item_waiting_and_the_next_pass_tries_again(self):
        self.run_("a")
        with patch.object(queue, "branch_pr", side_effect=queue.Refused("cannot look up the PR of branch a: gh down")):
            got = queue.release(self.pol, "a")
        self.assertEqual((got["state"], got["why"]), ("waiting", "cannot look up the PR of branch a: gh down"))
        self.assertEqual(queue.step(self.pol)["cleared"], [{"id": "a", "why": "nothing to merge"}])
        self.assertEqual((self.file()["waiting"], len(self.wakes)), ({}, 1))

    def test_a_worktree_stays_while_its_pr_is_open_and_goes_once_nothing_in_it_is_unpushed(self):
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
            self.run_(x, worktree=str(wt))
        (self.root / "wt-dirty" / "new.txt").write_text("x")
        git("commit", "-q", "--allow-empty", "-m", "two", cwd=self.root / "wt-ahead")
        self.forge.prs[50] = {"head": H1, "branch": "open"}
        self.forge.prs[51] = {"head": H2, "branch": "open", "state": "CLOSED"}
        git("worktree", "add", "-q", "--detach", str(self.root / "wt-loose"), cwd=clone)
        self.run_("loose", worktree=str(self.root / "wt-loose"))
        loose = queue.release(self.pol, "loose")
        self.assertEqual((loose["state"], "is on no branch" in loose["why"], (self.root / "wt-loose").exists()), ("waiting", True, True))
        with patch.object(queue, "git", lambda *a, **k: (1, "")):
            self.assertEqual(queue.release(self.pol, "loose")["why"], f"cannot list the worktrees of {os.path.realpath(clone)}")
        got = {x: queue.release(self.pol, x) for x in ("clean", "dirty", "ahead", "gone", "open")}
        self.assertEqual({x: v["state"] for x, v in got.items()},
                         {"clean": "done", "dirty": "waiting", "ahead": "waiting", "gone": "done", "open": "waiting"})
        self.assertEqual(got["dirty"]["why"], "nothing to merge, and its worktree has uncommitted or unpushed work")
        self.assertEqual([(self.root / f"wt-{x}").exists() for x in ("clean", "dirty", "ahead", "open")], [False, True, True, True])
        self.assertEqual([(self.root / f"wt-{x}" / "build").exists() for x in ("dirty", "open")], [True, False])
        self.forge.prs[50]["state"] = "CLOSED"
        self.forge.prs[52] = {"head": H2, "branch": "open"}
        got = queue.step(self.pol)
        self.assertEqual((got["cleared"], self.file()["waiting"]["open"]["pr"], (self.root / "wt-open").exists()), ([], 52, True))
        self.forge.prs[52]["state"] = "MERGED"
        (self.root / "wt-dirty" / "new.txt").unlink()
        self.assertEqual(queue.step(self.pol)["cleared"], [{"id": "dirty", "why": "nothing to merge"}, {"id": "open", "why": "PR 52 merged"}])
        left = [(self.root / "wt-open").exists(), (self.root / "wt-dirty").exists()]
        self.assertEqual((sorted(self.file()["waiting"]), left), (["ahead", "loose"], [False, False]))

    def test_a_queue_file_from_an_older_zstack_is_refused_by_name(self):
        for old in ({"items": {}}, {"items": {}, "done": []}, {"running": {}, "waiting": {}, "tasks": {}, "done": []}):
            (self.root / "queue.json").write_text(json.dumps(old))
            with self.assertRaisesRegex(queue.Refused, "queue.json was written by an older zstack: move it aside"):
                queue.step(self.pol)

    def test_an_open_pr_someone_waits_on_blocks_an_overlapping_pickup(self):
        self.cands = [cand(1)]
        self.start("app-1", "t-1", "/nowhere/1")
        self.pr_open(40)
        self.forge.issues[1] = []
        queue.verified(self.pol, "app-1", 40, H1, "review")
        queue.release(self.pol, "app-1")
        self.cands = [cand(1), cand(40, "review")]
        self.assertEqual(queue.step(self.pol)["skipped"], {"waiting": 1, "overlap": 1})

    def test_the_states_are_the_models(self):
        spec = (PKG / "model" / "Queue.tla").read_text()
        for name, to in (("Launch(m, i)", "running"), ("Leave(m, i)", "waiting"), ("Leave(m, i)", "done"), ("Settle(m, i)", "done")):
            self.assertIn(f'"{to}")', spec[spec.index(f"\n{name} =="):].split("\n\n")[0].split("\nSettle")[name == "Settle(m, i)"], name)
        states = set(spec[spec.index("States == {") + 11:spec.index("}", spec.index("States == {"))].replace('"', "").split(", "))
        self.assertEqual(states - {"none"}, set(queue.KEYS) - {"tasks", "verdicts"})


FAKE_T3 = """#!/usr/bin/env python3
import json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "stdin": "" if sys.stdin.isatty() or sys.argv[2] != "start" else sys.stdin.read()}) + "\\n")
mode = open(os.path.join(here, "mode")).read().strip()
if sys.argv[2] == "start" and mode != "no-start":
    print(json.dumps({"threadId": "mcp:1", "projectId": "p", "runId": "r"}))
elif sys.argv[2] == "show" and mode == "ok":
    print(json.dumps({"thread": {"threadId": sys.argv[3], "status": "running", "worktreePath": "/wt/1"}, "messages": []}))
else:
    sys.exit("the server is not running")
"""


class Pass(Case):
    def test_a_pass_frees_a_dead_thread_keeps_a_live_one_and_launches_up_to_the_cap(self):
        for x in ("a", "b"):
            self.run_(x)
        for x in ("c", "d"):
            self.add(x)
        self.status = {"t-a": "interrupted"}
        got = queue.run_pass(self.pol)
        self.assertEqual([(x["released"], x["state"]) for x in got["released"]], [("a", "done")])
        self.assertEqual(([x["id"] for x in got["launched"]], got["waiting"]), (["c"], "the machine is at its thread cap"))
        self.assertEqual((sorted(self.file()["running"]), sorted(self.file()["tasks"]), self.woken), (["b", "c"], ["d"], ["t-a"]))
        self.pol["run"]["threads"] = 9
        queue.run_pass(self.pol)
        for said in queue.DEAD:
            self.run_(said)
            self.status = {f"t-{said}": said, "t-c": "waiting", "t-b": "queued"}
            self.assertEqual([x["released"] for x in queue.run_pass(self.pol)["released"]], [said])
        self.assertEqual(sorted(self.file()["running"]), ["b", "c", "d"])

    def test_only_a_thread_t3_no_longer_lists_is_freed_and_a_t3_that_does_not_answer_frees_and_launches_nothing(self):
        self.pol["run"]["threads"] = 1
        self.run_("a")
        self.add("b")
        self.status, self.t3_down = {"t-a": "gone"}, True
        with self.assertRaisesRegex(queue.Refused, "T3 Code does not answer, so no thread can be read or launched: connection refused"):
            queue.run_pass(self.pol)
        self.assertEqual((sorted(self.file()["running"]), len(self.briefed)), (["a"], 1))
        self.t3_down, self.listing = False, [{"threadId": "t-a", "title": "a", "createdAt": "2999-01-01T00:00:00.000Z"}]
        got = queue.run_pass(self.pol)
        self.assertEqual((got["released"], got["launched"], got["waiting"]), ([], [], "the machine is at its thread cap"))
        self.listing = []
        got = queue.run_pass(self.pol)
        self.assertEqual(([x["released"] for x in got["released"]], [x["id"] for x in got["launched"]]), (["a"], ["b"]))

    def test_a_worktree_the_launch_could_not_read_is_filled_in_by_the_next_pass(self):
        self.threads["a"] = ("t-a", None)
        self.add("a")
        self.assertEqual(queue.run_pass(self.pol)["launched"], [{"id": "a", "thread": "t-a", "worktree": None}])
        queue.run_pass(self.pol)
        self.assertEqual(self.file()["running"]["a"]["worktree"], "/seen/t-a")

    def test_two_passes_never_run_at_once(self):
        self.add("a")
        with open(self.root / "pass.lock", "w") as held:
            queue.fcntl.flock(held, queue.fcntl.LOCK_EX)
            with self.assertRaisesRegex(queue.Refused, "another pass is running on this machine"):
                queue.run_pass(self.pol)
        self.assertEqual(self.briefed, [])
        self.assertEqual(len(queue.run_pass(self.pol)["launched"]), 1)

    def test_a_launch_starts_a_t3_thread_in_a_new_worktree_of_the_default_branch_with_the_brief(self):
        def git(*a, cwd):
            subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)
        seed, clone, fake = self.root / "seed", self.root / "clone", self.root / "t3"
        git("init", "-q", "-b", "trunk", str(seed), cwd=self.root)
        git("commit", "-q", "--allow-empty", "-m", "one", cwd=seed)
        git("clone", "-q", str(seed), str(clone), cwd=self.root)
        fake.write_text(FAKE_T3)
        fake.chmod(0o755)
        (self.root / "mode").write_text("ok")
        self.pol["host"]["clone"], self.pol["run"]["t3"] = str(clone), str(fake)
        up = {"id": "a", "task": "Fix the parser"}
        with patch.object(queue, "t3", T3):
            self.assertEqual(LAUNCH(self.pol, up), ("mcp:1", "/wt/1"))
            calls = [json.loads(x) for x in (self.root / "calls").read_text().splitlines()]
            provider, model = queue.model_for("coding", self.pol)
            self.assertEqual(calls[0]["argv"], ["thread", "start", str(clone), "-", "--title", "a", "--provider", provider, "--model", model,
                                                "--worktree", "origin/trunk", "--json"])
            self.assertEqual(calls[0]["stdin"], queue.brief(self.pol, up))
            self.assertEqual(calls[1]["argv"], ["thread", "show", "mcp:1", "--messages", "0", "--json"])
            (self.root / "mode").write_text("no-show")
            self.assertEqual(LAUNCH(self.pol, up), ("mcp:1", None))
            (self.root / "mode").write_text("no-start")
            with self.assertRaisesRegex(queue.Refused, "no thread started for a: the server is not running"):
                LAUNCH(self.pol, up)
            self.pol["run"]["t3"] = str(self.root / "missing")
            with self.assertRaisesRegex(queue.Refused, "no thread started for a: FileNotFoundError"):
                LAUNCH(self.pol, up)
            git("remote", "set-url", "origin", str(self.root / "nowhere"), cwd=clone)
            with self.assertRaisesRegex(queue.Refused, "git fetch origin failed"):
                LAUNCH(self.pol, up)


class Cli(Case):
    def test_the_commands(self):
        with patch.object(queue, "load", return_value=self.pol), patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            self.assertEqual(queue.main(["add", "a", "--priority", "3"], stdin=io.StringIO("task")), 0)
            self.assertEqual(queue.main(["add", "b", "--priority"], stdin=io.StringIO("task")), 2)
            self.assertEqual(queue.main(["add", "c", "--preapproved", "--priority", "1"], stdin=io.StringIO("task")), 0)
            self.assertEqual(queue.main(["add", "d", "--preapproved", "--preapproved"], stdin=io.StringIO("task")), 2)
            for gone in (["pickup"], ["holds"], ["hold", "add", "x"], ["start", "a"], ["claim", "a", "t-1", "/w"], ["next"], ["pass", "a"], ["nope"]):
                self.assertEqual(queue.main(gone), 2, gone)
            self.assertEqual(queue.main(["pass"]), 0)
            self.assertEqual(sorted(self.file()["running"]), ["a", "c"])
            self.forge.prs[40] = {"head": H1}
            for bad in (["verified", "a", "auto"], ["verified", "a", "40", H1], ["verified", "a", "40", H1, "pass", "auto"],
                        ["verified", "a", "40", H1, "extreme"], ["verified", "a", "40", H1[:7], "auto"], ["gate", "40", H1[:7]],
                        ["release", "a", "b"]):
                self.assertEqual(queue.main(bad), 2, bad)
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["gate", "40", H1]), 1)
            self.assertEqual(err.getvalue(), f"queue: PR 40 does not merge: the verifier has not passed head {H1}\n")
            self.assertEqual(queue.main(["verified", "a", "40", H1, "auto"]), 0)
            for ok in (["gate", "40", H1], ["release", "--hook"], ["release", "a"], ["release"], ["list"], ["pass"]):
                self.assertEqual(queue.main(ok), 0, ok)
            self.t3_down = True
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["pass"]), 1)
            self.assertEqual(err.getvalue(), "queue: T3 Code does not answer, so no thread can be read or launched: connection refused\n")
        self.assertEqual(sorted(self.file()["waiting"]), ["a"])


if __name__ == "__main__":
    unittest.main()
