import importlib.util
import io
import json
import os
import re
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
GATE = {"name": M["gate_check"], "status": "COMPLETED", "conclusion": "SUCCESS", "completedAt": "t"}


def cand(n, kind="issue", author="someone", labels=(), linked=(), created="2026-01-01"):
    ident = f"app-{n}" if kind == "issue" else f"app-{n}-review-{H1[:7]}"
    return dict(id=ident, kind=kind, n=n, head=None if kind == "issue" else H1, author={"login": author}, title=f"t{n}", body="b",
                url=f"u{n}", created=created, labels=list(labels), linked=list(linked))


def green(n):
    return [{"name": f"c{k}", "status": "COMPLETED", "conclusion": "SUCCESS", "completedAt": "t"} for k in range(n)]


class Forge:
    def __init__(self):
        self.prs, self.issues, self.calls = {}, {}, []

    def gh(self, *a, stdin=None):
        self.calls.append(a)
        if a[:2] == ("pr", "view"):
            p = self.prs[int(a[2])]
            return 0, json.dumps({"number": int(a[2]), "state": p.get("state", "OPEN"), "headRefOid": p["head"], "isDraft": p.get("draft", False),
                                  "headRefName": p.get("branch") or f"pr-{a[2]}", "baseRefName": "main", "isCrossRepository": p.get("fork", False),
                                  "mergeable": p.get("mergeable", "MERGEABLE"),
                                  "labels": [{"name": x} for x in p.get("labels", [])],
                                  "statusCheckRollup": [{"name": M["gate_check"], "conclusion": p.get("ci", "SUCCESS")}, *p.get("more", [])]}), ""
        if a[:2] == ("pr", "list"):
            want = a[a.index("--state") + 1]
            return 0, json.dumps([{"number": n} for n, p in sorted(self.prs.items(), reverse=True) if p.get("branch") == a[a.index("--head") + 1]
                                  and (want == "all" or p.get("state", "OPEN") == "OPEN")][:1]), ""
        if a[:2] == ("pr", "merge"):
            p = self.prs[int(a[2])]
            if p.get("refuse") or a[a.index("--match-head-commit") + 1] != p["head"]:
                return 1, "", p.get("refuse") or "head moved"
            p["state"] = "MERGED"
            return 0, "", ""
        if a[:2] == ("api", "graphql") and any(x.startswith("id=") for x in a):
            n = int(next(x for x in a if x.startswith("id="))[6:])
            p, q = self.prs[n], next(x for x in a if x.startswith("query="))
            if p.get("refuse"):
                return 1, "", p["refuse"]
            if "enqueuePullRequest" in q:
                if not p.get("queue") or next(x for x in a if x.startswith("h="))[2:] != p["head"]:
                    return 1, "", "not queueable at that head"
                p["inq"], p["adds"] = True, p.get("adds", 0) + 1
            else:
                p["inq"] = False
            return 0, "{}", ""
        if a[:2] == ("api", "graphql") and any("isInMergeQueue" in x and x.startswith("query=") for x in a):
            n = int(next(x for x in a if x.startswith("p="))[2:])
            p = self.prs[n]
            if p.get("inq") == "down":
                return 1, "", "graphql down"
            gone = p.get("removed", [])
            return 0, json.dumps({"id": f"PR_{n}", "state": p.get("state", "OPEN"), "headRefOid": p["head"], "isInMergeQueue": bool(p.get("inq")),
                                  "added": {"filteredCount": p.get("adds", 0)},
                                  "removed": {"filteredCount": len(gone), "nodes": [{"reason": x} for x in gone[-1:]]}}), ""
        if a[:2] == ("api", "graphql"):
            p = self.prs[int(next(x for x in a if x.startswith("p="))[2:])]
            page = int(next((x[2:] for x in a if x.startswith("c=")), 0))
            p.update(p.pop("then" if page == 0 else "later", {}))
            if p.get("queue") == "down" or page and page == p.get("down"):
                return 1, "", "graphql down"
            pages = p.get("pages") or [[{"name": M["gate_check"], "status": "COMPLETED", "conclusion": p.get("ci", "SUCCESS")}, *p.get("more", [])]]
            checks = pages[page] + ([None] if p.get("nulls") else [])
            more = {"hasNextPage": page + 1 < len(pages), "endCursor": str(page + 1) if page + 1 < len(pages) else None}
            n = int(next(x for x in a if x.startswith("p="))[2:])
            pr = {"id": f"PR_{n}", "headRefOid": p["head"], "isDraft": p.get("draft", False), "mergeable": p.get("mergeable", "MERGEABLE"),
                  "isMergeQueueEnabled": bool(p.get("queue")), "labels": {"nodes": [{"name": x} for x in p.get("labels", [])]},
                  "commits": {"nodes": [{"commit": {"statusCheckRollup": None if page and page == p.get("gone") else
                                                    {"contexts": {"pageInfo": more, "nodes": checks}}}}]}}
            issue = re.search(r"issue\(number: (\d+)\)", next(x for x in a if x.startswith("query=")))
            found = issue and self.issues.get(int(issue.group(1)))
            said = {"pullRequest": pr}
            if issue:
                said["issue"] = None if found is None else {"labels": {"nodes": [{"name": x} for x in found]}}
            return 0, json.dumps(said), ""
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

    def complete(self, *idents):
        for x in idents:
            with patch.dict(os.environ, {queue.THREAD_VAR: self.file()["running"][x]["thread"]}):
                queue.complete(self.pol, x)

    def pr_open(self, pr, head=H1, labels=()):
        self.forge.prs[pr] = {"head": head, "labels": list(labels)}

    def trees(self):
        return patch.multiple(queue, tidy=lambda pol, it, whole: True, branch_pr=lambda pol, it: None, hooked=lambda pol: True)


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
            self.complete("a")
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
        for want in ("# a", "Fix the parser", f"{PKG}/queue.py verified a <pr number> <head sha> auto", f"{PKG}/queue.py complete a`",
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

    def test_a_finished_check_run_github_still_lists_as_in_progress_does_not_block_and_a_running_one_does(self):
        done = [{"name": M["gate_check"], "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "quick / scope", "status": "IN_PROGRESS", "conclusion": "SUCCESS", "completedAt": "2026-11-19T16:58:54Z"}]
        self.assertIsNone(queue.unready(self.pol, {"statusCheckRollup": done}))
        for c in ({"name": "quick", "status": "IN_PROGRESS", "conclusion": None, "completedAt": None},
                  {"name": "quick", "status": "IN_PROGRESS", "conclusion": "SUCCESS", "completedAt": None},
                  {"name": "quick", "status": "QUEUED"}, {"context": "legacy", "state": "PENDING"}):
            with self.subTest(c=c):
                self.assertEqual(queue.unready(self.pol, {"statusCheckRollup": [*done, c]}),
                                 "a check is still running on that head, and a merge now would arm auto-merge")

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


class Approve(Case):
    def setUp(self):
        super().setUp()
        self.pol["run"]["threads"] = 9

    def waiting(self, ident="a", pr=40, said="review", labels=()):
        self.run_(ident, thread=f"t-{ident}")
        self.pr_open(pr, labels=labels)
        queue.verified(self.pol, ident, pr, H1, said)
        got = queue.release(self.pol, ident)
        self.assertEqual(got["state"], "waiting")
        return got

    def settle(self, ident):
        return queue.locked(self.pol, lambda q: (queue.outcome(self.pol, q, ident, merge=True), True))

    def merges(self):
        return [a[2] for a in self.forge.calls if a[:2] == ("pr", "merge")]

    def queued(self, mutation="enqueuePullRequest"):
        return [next(x[6:] for x in a if x.startswith("id=")) for a in self.forge.calls
                if a[:2] == ("api", "graphql") and any(x.startswith("query=") and mutation in x for x in a)]

    def test_approve_preapproves_a_task_a_running_or_a_waiting_item_and_nothing_else(self):
        self.run_("r", thread="t-r")
        self.waiting("w", pr=41)
        self.add("t")
        for ident, part in (("t", "tasks"), ("r", "running"), ("w", "waiting")):
            self.assertEqual(queue.approve(self.pol, ident), {"id": ident, "preapproved": part})
            self.assertTrue(self.file()[part][ident]["preapproved"], ident)
        with self.assertRaisesRegex(queue.Refused, "zzz is not a task, running or waiting, so there is nothing to approve"):
            queue.approve(self.pol, "zzz")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-r"}), self.assertRaisesRegex(queue.Refused, "r's own thread"):
            queue.approve(self.pol, "r")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-x"}):
            queue.approve(self.pol, "r")

    def test_an_approval_while_a_launch_is_recorded_reaches_the_running_item(self):
        self.add("a")

        def launch(pol, up):
            self.assertEqual(queue.approve(self.pol, "a"), {"id": "a", "preapproved": "running"})
            return "t-a", "/nowhere/wt-a"
        with patch.object(queue, "launch", launch):
            self.assertEqual(queue.step(self.pol)["launched"]["id"], "a")
        self.assertEqual((self.file()["tasks"], self.file()["running"]["a"]["preapproved"]), ({}, True))

    def test_a_pass_merges_an_approved_waiting_pr_whose_thread_ended_on_review_and_clears_it(self):
        self.assertEqual(self.waiting()["why"], "PR 40 waits for the owner: it needs a person's review")
        self.assertEqual((queue.step(self.pol)["cleared"], self.merges()), ([], []))
        queue.approve(self.pol, "a")
        self.assertEqual(queue.step(self.pol)["cleared"], [{"id": "a", "why": "PR 40 merged"}])
        self.assertEqual([a for a in self.forge.calls if a[:2] == ("pr", "merge")],
                         [("pr", "merge", "40", "-R", REPO, "--squash", "--match-head-commit", H1)])
        self.assertEqual((self.file()["waiting"], self.file()["verdicts"], self.queued()), ({}, {}, []))

    def test_on_a_merge_queue_base_a_pass_queues_the_passed_head_keeps_it_in_flight_and_clears_it_once_merged(self):
        self.waiting()
        queue.approve(self.pol, "a")
        self.forge.prs[40]["queue"] = True
        self.wakes.clear()
        flight = [{"id": "a", "why": f"PR 40 is in the merge queue at head {H1}"}]
        self.assertEqual((queue.step(self.pol)["kept"], self.queued(), self.merges()), (flight, ["40"], []))
        enq = next(a for a in self.forge.calls if any("enqueuePullRequest" in x for x in a))
        self.assertEqual([x for x in enq if x.startswith(("id=", "h="))], ["id=PR_40", f"h={H1}"])
        self.assertEqual((queue.step(self.pol)["kept"], self.queued(), self.file()["waiting"]["a"]["queued"]), (flight, ["40"], H1))
        self.assertEqual(queue.release(self.pol, "a")["why"], f"PR 40 is in the merge queue at head {H1}")
        self.forge.prs[40].update(state="MERGED", inq=False)
        self.assertEqual(queue.step(self.pol)["cleared"], [{"id": "a", "why": "PR 40 merged"}])
        self.assertEqual((self.file()["waiting"], self.wakes, self.merges()), ({}, [], []))

    def test_a_hold_on_a_queued_pr_or_its_issue_takes_it_out_of_the_queue_and_a_lifted_hold_queues_it_again(self):
        hold = M["hold_label"]
        self.cands = [cand(1)]
        self.start("app-1", "t-1", "/nowhere/1")
        self.forge.issues[1] = []
        self.pr_open(41)
        queue.verified(self.pol, "app-1", 41, H1, "review")
        queue.release(self.pol, "app-1")
        self.waiting("a")
        for ident in ("a", "app-1"):
            queue.approve(self.pol, ident)
        self.forge.prs[40]["queue"] = self.forge.prs[41]["queue"] = True
        self.wakes.clear()
        queue.step(self.pol)
        self.assertEqual(self.queued(), ["40", "41"])
        self.forge.prs[40]["labels"], self.forge.issues[1] = [hold], [hold]
        self.assertEqual(queue.step(self.pol)["kept"],
                         [{"id": "a", "why": f"PR 40 waits for the owner: it is labelled {hold}, so the pass took it out of the merge queue"},
                          {"id": "app-1", "why": f"PR 41 waits for the owner: it is labelled {hold}, so the pass took it out of the merge queue"}])
        self.assertEqual((self.queued("dequeuePullRequest"), self.forge.prs[40]["inq"], self.forge.prs[41]["inq"]), (["40", "41"], False, False))
        self.assertEqual(queue.step(self.pol)["kept"][0], {"id": "a", "why": f"PR 40 waits for the owner: it is labelled {hold}"})
        self.assertEqual((self.queued(), self.merges(), self.wakes), (["40", "41"], [], []))
        self.forge.prs[40]["labels"], self.forge.issues[1] = [], []
        queue.step(self.pol)
        self.assertEqual(self.queued(), ["40", "41", "40", "41"])

    def test_a_review_label_on_a_queued_pr_or_its_issue_takes_it_out_of_the_queue(self):
        review = M["review_label"]
        self.cands = [cand(1)]
        self.start("app-1", "t-1", "/nowhere/1")
        self.forge.issues[1] = []
        self.pr_open(41)
        queue.verified(self.pol, "app-1", 41, H1, "auto")
        queue.release(self.pol, "app-1")
        self.waiting("a")
        for ident in ("a", "app-1"):
            queue.approve(self.pol, ident)
        self.forge.prs[40]["queue"] = self.forge.prs[41]["queue"] = True
        queue.step(self.pol)
        self.forge.prs[40]["labels"], self.forge.issues[1] = [review], [review]
        out = "it needs a person's review, so the pass took it out of the merge queue"
        self.assertEqual(queue.step(self.pol)["kept"], [{"id": "a", "why": f"PR 40 waits for the owner: {out}"},
                                                        {"id": "app-1", "why": f"PR 41 waits for the owner: {out}"}])
        self.assertEqual((self.queued("dequeuePullRequest"), self.merges()), (["40", "41"], []))

    def test_a_newer_head_queued_from_outside_is_the_one_tracked_and_a_stale_removal_gives_no_reason(self):
        self.waiting()
        queue.approve(self.pol, "a")
        self.forge.prs[40].update(queue=True, removed=["an earlier entry left"])
        queue.step(self.pol)
        self.forge.prs[40].update(head=H2, adds=2)
        queue.verified(self.pol, "a", 40, H2, "review")
        self.assertEqual(queue.step(self.pol)["kept"], [{"id": "a", "why": f"PR 40 is in the merge queue at head {H2}"}])
        self.forge.prs[40]["inq"] = False
        why = f"PR 40 waits for the owner: GitHub took head {H2} out of the merge queue: it gave no reason"
        self.assertEqual(queue.step(self.pol)["kept"], [{"id": "a", "why": why}])
        self.assertEqual((queue.step(self.pol)["kept"], self.queued()), ([{"id": "a", "why": why}], ["40"]))

    def test_a_drop_after_an_entry_the_pass_did_not_add_blames_only_the_recorded_head_and_a_new_head_is_still_queued(self):
        self.waiting()
        queue.approve(self.pol, "a")
        self.forge.prs[40]["queue"] = True
        queue.step(self.pol)
        self.forge.prs[40].update(head=H2, adds=3, inq=False, removed=["the merge group failed ci-gate"] * 2)
        queue.verified(self.pol, "a", 40, H2, "review")
        self.wakes.clear()
        why = (f"PR 40 waits for the owner: GitHub took head {H1} out of the merge queue, and an entry the pass did not add since, so whose "
               "reason is whose cannot be told")
        self.assertEqual((queue.step(self.pol)["kept"], self.wakes), ([{"id": "a", "why": why}], [("blocked", f"a: {why}", 40)]))
        self.assertEqual((queue.step(self.pol)["kept"], self.queued()), ([{"id": "a", "why": f"PR 40 is in the merge queue at head {H2}"}],
                                                                         ["40", "40"]))

    def test_a_replacement_pr_starts_with_no_queue_record_from_the_pr_it_replaced(self):
        self.run_("a", thread="t-a", worktree="/nowhere/wt-a")
        self.pr_open(40)
        self.forge.prs[40].update(branch="b", queue=True)
        queue.verified(self.pol, "a", 40, H1, "review")
        queue.approve(self.pol, "a")
        with patch.object(queue, "listed", lambda pol, path: (None, path)), patch.object(queue, "git", lambda *a, cwd=None: (0, "b\n")):
            queue.release(self.pol, "a")
            queue.step(self.pol)
            self.forge.prs[40].update(inq=False, removed=["the merge group failed ci-gate"])
            queue.step(self.pol)
            self.assertEqual(self.file()["waiting"]["a"]["dropped"]["head"], H1)
            self.forge.prs[40]["state"] = "CLOSED"
            self.forge.prs[41] = {"head": H1, "labels": [], "branch": "b", "queue": True}
            queue.step(self.pol)
            queue.verified(self.pol, "a", 41, H1, "review")
            self.assertEqual(queue.step(self.pol)["kept"], [{"id": "a", "why": f"PR 41 is in the merge queue at head {H1}"}])
        self.assertEqual(self.queued(), ["40", "41"])

    def test_a_replacement_pr_verified_before_any_pass_sees_it_starts_with_no_queue_record(self):
        self.waiting()
        queue.approve(self.pol, "a")
        self.forge.prs[40]["queue"] = True
        queue.step(self.pol)
        self.forge.prs[40].update(inq=False, removed=["the merge group failed ci-gate"])
        queue.step(self.pol)
        self.forge.prs[40]["state"] = "CLOSED"
        self.forge.prs[41] = {"head": H1, "labels": [], "queue": True}
        queue.verified(self.pol, "a", 41, H1, "review")
        self.assertNotIn("dropped", self.file()["waiting"]["a"])
        self.assertEqual(queue.step(self.pol)["kept"], [{"id": "a", "why": f"PR 41 is in the merge queue at head {H1}"}])
        self.assertEqual(self.queued(), ["40", "41"])

    def test_a_pr_someone_else_put_in_the_queue_is_in_flight_and_a_hold_still_takes_it_out(self):
        self.waiting(said="auto")
        self.forge.prs[40].update(queue=True, inq=True)
        self.assertEqual(queue.step(self.pol)["kept"], [{"id": "a", "why": f"PR 40 is in the merge queue at head {H1}"}])
        self.forge.prs[40]["labels"] = [M["hold_label"]]
        queue.step(self.pol)
        self.assertEqual((self.queued("dequeuePullRequest"), self.queued(), self.merges()), (["40"], [], []))

    def test_a_queue_entry_github_drops_is_reported_blocked_once_and_not_queued_again_until_a_new_head_passes(self):
        self.waiting()
        queue.approve(self.pol, "a")
        self.forge.prs[40]["queue"] = True
        self.wakes.clear()
        queue.step(self.pol)
        self.forge.prs[40].update(inq=False, removed=["the merge group failed ci-gate"])
        why = f"PR 40 waits for the owner: GitHub took head {H1} out of the merge queue: the merge group failed ci-gate"
        self.assertEqual(queue.step(self.pol)["kept"], [{"id": "a", "why": why}])
        self.assertEqual(self.wakes, [("blocked", f"a: {why}", 40)])
        self.assertEqual((queue.step(self.pol)["kept"], self.queued(), len(self.wakes)), ([{"id": "a", "why": why}], ["40"], 1))
        self.forge.prs[40]["head"] = H2
        queue.verified(self.pol, "a", 40, H2, "review")
        self.assertEqual(queue.step(self.pol)["kept"], [{"id": "a", "why": f"PR 40 is in the merge queue at head {H2}"}])
        self.assertEqual(self.queued(), ["40", "40"])

    def test_a_queue_read_that_fails_keeps_the_pr_waiting_and_neither_merges_nor_queues_it(self):
        self.waiting()
        queue.approve(self.pol, "a")
        self.forge.prs[40].update(queue=True, inq="down")
        self.assertEqual(queue.step(self.pol)["kept"],
                         [{"id": "a", "why": "PR 40: whether it is in the merge queue could not be read: graphql down"}])
        self.assertEqual((self.queued(), self.merges(), list(self.file()["waiting"])), ([], [], ["a"]))

    def test_a_pass_merges_an_approved_pr_whose_green_checks_run_over_several_pages_and_reads_every_page(self):
        self.waiting()
        queue.approve(self.pol, "a")
        self.forge.prs[40]["pages"] = [[*green(99), GATE], green(100), [*green(20), {"name": "docs", "status": "COMPLETED", "conclusion": "SKIPPED",
                                                                                    "completedAt": "t"}]]
        self.assertEqual(queue.step(self.pol)["cleared"], [{"id": "a", "why": "PR 40 merged"}])
        self.assertEqual([next((x for x in a if x.startswith("c=")), None) for a in self.forge.calls if a[:2] == ("api", "graphql")
                          and f"query={queue.FRESH} }} }}" in a], [None, "c=1", "c=2"])
        self.assertEqual(self.merges(), ["40"])

    def test_a_pass_never_merges_an_approved_pr_that_is_unverified_moved_held_red_running_labelled_or_conflicting(self):
        self.never({}, "the pass could not merge it")

    def test_a_pass_never_queues_an_approved_pr_on_a_merge_queue_base_that_fails_any_gate(self):
        self.never({"queue": True}, "the pass could not put it in the merge queue")

    def never(self, base, refused):
        hold = M["hold_label"]
        cases = (("unverified", {"said": "fail"}, {}, f"the verifier has not passed head {H1}"),
                 ("moved", {}, {"head": H2}, f"the verifier has not passed head {H2}"),
                 ("held", {"labels": [hold]}, {}, f"it is labelled {hold}"),
                 ("red", {}, {"ci": "FAILURE"}, f"{M['gate_check']} has not passed on that head"),
                 ("running", {}, {"more": [{"name": "lint", "status": "IN_PROGRESS", "conclusion": None, "completedAt": None}]},
                  "a check is still running on that head, and a merge now would arm auto-merge"),
                 ("labelled", {"labels": [M["review_label"]]}, {}, "it needs a person's review"),
                 ("conflicting", {}, {"mergeable": "CONFLICTING"}, "GitHub says it is conflicting, not mergeable"),
                 ("draft", {}, {"draft": True}, "the PR is a draft"),
                 ("unread", {}, {"queue": "down"}, "it could not be read again just before the merge: graphql down"),
                 ("held-late", {}, {"then": {"labels": [hold]}}, f"it is labelled {hold}"),
                 ("labelled-late", {}, {"then": {"labels": [M["review_label"]]}}, "it needs a person's review"),
                 ("moved-late", {}, {"then": {"head": H2}}, f"the verifier has not passed head {H2}"),
                 ("red-late", {}, {"then": {"ci": "FAILURE"}}, f"{M['gate_check']} has not passed on that head"),
                 ("running-late", {}, {"then": {"more": [{"name": "lint", "status": "IN_PROGRESS", "conclusion": None, "completedAt": None}]}},
                  "a check is still running on that head, and a merge now would arm auto-merge"),
                 ("paged-red", {}, {"pages": [green(100), [*green(40), {"name": M["gate_check"], "status": "COMPLETED", "conclusion": "FAILURE"}]]},
                  f"{M['gate_check']} has not passed on that head"),
                 ("paged-running", {}, {"pages": [[GATE, *green(99)], [*green(20), {"name": "lint", "status": "IN_PROGRESS", "conclusion": None,
                                                                                      "completedAt": None}]]},
                  "a check is still running on that head, and a merge now would arm auto-merge"),
                 ("paged-expected", {}, {"pages": [[GATE, *green(99)], [{"context": "ci/x", "state": "EXPECTED"}]]},
                  "a check is still running on that head, and a merge now would arm auto-merge"),
                 ("paged-unread", {}, {"pages": [[GATE, *green(99)], green(30)], "down": 1},
                  "it could not be read again just before the merge: graphql down"),
                 ("paged-null", {}, {"pages": [[GATE, *green(99)], [*green(10), None]]},
                  "it could not be read again just before the merge: not the answer asked for"),
                 ("paged-gone", {}, {"pages": [[GATE, *green(99)], [{"name": "lint", "status": "IN_PROGRESS", "conclusion": None,
                                                                    "completedAt": None}]], "gone": 1},
                  "it could not be read again just before the merge: not the answer asked for"),
                 ("paged-moved", {}, {"pages": [[GATE, *green(99)], green(30)], "later": {"head": H2}}, f"the verifier has not passed head {H2}"),
                 ("paged-held", {}, {"pages": [[GATE, *green(99)], green(30)], "later": {"labels": [hold]}}, f"it is labelled {hold}"),
                 ("paged-endless", {}, {"pages": [[GATE]] + [green(100)] * queue.FRESH_PAGES},
                  f"it could not be read again just before the merge: its checks run past {queue.FRESH_PAGES} pages"),
                 ("null-check", {}, {"then": {"nulls": True}}, "it could not be read again just before the merge: not the answer asked for"),
                 ("refused", {}, {"refuse": "base branch policy prohibits the merge"}, f"{refused}: base branch policy prohibits the merge"))
        for n, (ident, made, then, _) in enumerate(cases):
            self.waiting(ident, pr=50 + n, **made)
            queue.approve(self.pol, ident)
            self.forge.prs[50 + n].update({**base, **then})
        got = queue.step(self.pol)
        self.assertEqual(got["cleared"], [])
        kept = sorted((c[0], f"PR {50 + n} waits for the owner: {c[3]}") for n, c in enumerate(cases))
        self.assertEqual(got["kept"], [{"id": ident, "why": why} for ident, why in kept])
        self.assertEqual(sorted(self.file()["waiting"]), sorted(c[0] for c in cases))
        for n, (ident, _, _, why) in enumerate(cases):
            self.assertEqual(self.settle(ident), ("waiting", f"PR {50 + n} waits for the owner: {why}"), ident)
        tried, never = (self.queued(), self.merges()) if base else (self.merges(), self.queued())
        self.assertEqual((tried, never), ([str(50 + len(cases) - 1)] * 2, []))

    def test_a_hold_on_the_issue_seen_only_by_the_last_read_stops_the_merge(self):
        hold = M["hold_label"]
        self.cands = [cand(1)]
        self.start("app-1", "t-1", "/nowhere/1")
        self.forge.issues[1] = []
        self.pr_open(40)
        queue.verified(self.pol, "app-1", 40, H1, "review")
        queue.release(self.pol, "app-1")
        queue.approve(self.pol, "app-1")
        real = self.forge.gh

        def gh(*a, stdin=None):
            if a[:2] == ("api", "graphql"):
                self.forge.issues[1] = [hold]
            return real(*a, stdin=stdin)
        with patch.object(queue, "gh", gh):
            self.assertEqual(queue.step(self.pol)["kept"], [{"id": "app-1", "why": f"PR 40 waits for the owner: it is labelled {hold}"}])
        self.assertEqual(self.merges(), [])
        self.forge.issues[1] = []
        self.assertEqual(queue.step(self.pol)["cleared"], [{"id": "app-1", "why": "PR 40 merged"}])

    def test_a_pr_dropped_from_the_merge_queue_whose_thread_ended_goes_back_to_tasks_once_with_its_priority_and_runs_on_that_pr(self):
        self.run_("a", thread="t-a", text="Fix the parser", priority=5)
        self.pr_open(40)
        self.forge.prs[40].update(branch="fix-parser", queue=True)
        queue.verified(self.pol, "a", 40, H1, "auto")
        queue.release(self.pol, "a")
        queue.approve(self.pol, "a")
        queue.step(self.pol)
        self.forge.prs[40].update(inq=False, removed=["MERGE_CONFLICT"], mergeable="CONFLICTING")
        dropped = f"GitHub took head {H1} out of the merge queue: MERGE_CONFLICT"
        self.assertEqual(queue.step(self.pol)["kept"], [{"id": "a", "why": f"PR 40 waits for the owner: {dropped}"}])
        self.status, self.pol["run"]["threads"] = {"t-a": "completed"}, 0
        why = "its thread has ended, and PR 40 waits on that thread's work (a drop from the merge queue), so it goes back to tasks"
        self.assertEqual(queue.step(self.pol)["cleared"], [{"id": "a", "why": why}])
        row = self.file()["tasks"]["a"]
        self.assertEqual({k: row[k] for k in ("priority", "preapproved", "kind", "pr", "base", "was")},
                         {"priority": 5, "preapproved": True, "kind": "task", "pr": 40, "base": "origin/fix-parser", "was": "Fix the parser"})
        for said in ("Fix the parser\n", f"{dropped}. Merge origin/main into the branch", "runs again in a fresh thread",
                     "where the brief above says to open a PR, PR #40 is that PR", "`git push origin HEAD:fix-parser`",
                     "open no new PR"):
            self.assertIn(said, row["task"])
        self.assertEqual(self.file()["waiting"], {})
        self.assertEqual((queue.step(self.pol)["cleared"], self.file()["tasks"], self.file()["waiting"]), ([{"id": "a", "why": why}], {"a": row}, {}))
        self.pol["run"]["threads"] = 9
        self.threads["a"] = ("t-a2", "/nowhere/wt-a2")
        self.assertEqual(queue.step(self.pol)["launched"], {"id": "a", "thread": "t-a2", "worktree": "/nowhere/wt-a2"})
        self.assertEqual((self.briefed[-1]["base"], self.briefed[-1]["task"], self.file()["tasks"]), ("origin/fix-parser", row["task"], {}))
        running = self.file()["running"]["a"]
        self.assertEqual({k: running[k] for k in ("pr", "priority", "preapproved")}, {"pr": 40, "priority": 5, "preapproved": True})
        self.assertEqual(running["dropped"]["head"], H1)
        self.assertEqual(queue.verified(self.pol, "a", 40, H1, "auto")["say"], f"its thread does not merge it: {dropped}")
        with self.assertRaisesRegex(queue.Refused, f"PR 40 does not merge: {dropped}"):
            queue.gate(self.pol, 40, H1)
        queue.release(self.pol, "a")
        self.status = {}
        self.assertEqual((queue.step(self.pol)["kept"], self.queued()), ([{"id": "a", "why": f"PR 40 waits for the owner: {dropped}"}], ["40"]))

    def test_a_drop_seen_by_a_pass_whose_thread_has_ended_goes_back_to_tasks_in_that_pass(self):
        self.run_("a", thread="t-a")
        self.pr_open(40)
        self.forge.prs[40].update(queue=True)
        queue.verified(self.pol, "a", 40, H1, "auto")
        queue.release(self.pol, "a")
        queue.approve(self.pol, "a")
        queue.step(self.pol)
        self.forge.prs[40].update(inq=False, removed=["MERGE_CONFLICT"], mergeable="CONFLICTING")
        self.status, self.pol["run"]["threads"] = {"t-a": "completed"}, 0
        why = "its thread has ended, and PR 40 waits on that thread's work (a drop from the merge queue), so it goes back to tasks"
        self.assertEqual(queue.step(self.pol)["cleared"], [{"id": "a", "why": why}])
        self.assertEqual(self.file()["tasks"]["a"]["dropped"]["head"], H1)

    def test_a_wait_recorded_before_rows_kept_a_priority_goes_back_with_the_priority_its_labels_give(self):
        self.cands = [cand(7, labels=["urgent"])]
        self.start("app-7", "t-7")
        self.cands = []
        self.pr_open(47)
        self.forge.issues[7] = ["urgent"]
        queue.verified(self.pol, "app-7", 47, H1, "fail")
        queue.release(self.pol, "app-7")

        def old(q):
            del q["waiting"]["app-7"]["priority"]
            return None, True
        queue.locked(self.pol, old)
        self.status, self.pol["run"]["threads"] = {"t-7": "completed"}, 0
        queue.step(self.pol)
        self.assertEqual(self.file()["tasks"]["app-7"]["priority"], 100)

    def test_a_preapproved_issue_whose_issue_carries_the_review_label_stays_waiting_when_its_thread_ends(self):
        self.cands = [cand(7)]
        self.start("app-7", "t-7")
        self.cands = []
        self.pr_open(47)
        self.forge.prs[47]["mergeable"] = "CONFLICTING"
        self.forge.issues[7] = [M["review_label"]]
        queue.verified(self.pol, "app-7", 47, H1, "auto")
        queue.release(self.pol, "app-7")
        queue.approve(self.pol, "app-7")
        self.status, self.pol["run"]["threads"] = {"t-7": "completed"}, 0
        self.assertEqual((queue.step(self.pol)["cleared"], sorted(self.file()["waiting"])), ([], ["app-7"]))

    def test_a_resumed_task_keeps_its_verdict_waits_out_a_hold_names_every_kept_worktree_and_never_doubles_a_pr(self):
        self.waiting("a", pr=40, said="fail")
        self.forge.prs[40]["branch"] = "fix/parser(v2)"
        self.status, self.pol["run"]["threads"] = {"t-a": "gone"}, 0
        trees = patch.multiple(queue, tidy=lambda pol, it, whole: False, listed=lambda pol, path: (None, path))
        with trees:
            queue.step(self.pol)
        row = self.file()["tasks"]["a"]
        self.assertEqual(row["kept"], ["/nowhere/wt-a"])
        self.assertIn("git push origin 'HEAD:fix/parser(v2)'", row["task"])
        self.assertIn("`git -C /nowhere/wt-a status`", row["task"])
        self.forge.prs[40]["labels"], self.pol["run"]["threads"] = [M["hold_label"]], 9
        with trees:
            self.run_("b")
            self.pr_open(41)
            queue.verified(self.pol, "b", 41, H1, "auto")
            self.assertEqual(sorted(self.file()["verdicts"]), ["40", "41"])
            self.assertEqual((sorted(self.file()["tasks"]), sorted(self.file()["waiting"])), ([], ["a"]))
            got = queue.step(self.pol)
            self.assertEqual((got["launched"], self.file()["tasks"]), (None, {}))
            self.forge.prs[40]["labels"], self.threads["a"] = [], ("t-a2", "/nowhere/wt-a2")
            self.assertEqual(queue.step(self.pol)["launched"]["id"], "a")
            queue.release(self.pol, "a")
            self.status, self.pol["run"]["threads"] = {"t-a": "gone", "t-a2": "gone"}, 0
            queue.step(self.pol)
        self.assertEqual(self.file()["tasks"]["a"]["kept"], ["/nowhere/wt-a", "/nowhere/wt-a2"])

    def test_only_a_wait_whose_thread_ended_and_whose_pr_needs_that_thread_goes_back_to_tasks(self):
        hold = M["hold_label"]
        cases = (("live", "fail", {}, "running"), ("person", "review", {"mergeable": "CONFLICTING"}, "completed"),
                 ("labelled", "auto", {"labels": [M["review_label"]], "ci": "FAILURE"}, "cancelled"),
                 ("held", "fail", {"labels": [hold]}, "completed"), ("flagged", "fail", {"labels": [M["review_label"]]}, "completed"),
                 ("approved-flagged", "auto", {"labels": [M["review_label"]], "mergeable": "CONFLICTING"}, "completed"),
                 ("draft", "fail", {"draft": True}, "failed"),
                 ("green", "auto", {}, "completed"), ("fork", "fail", {"fork": True}, "completed"),
                 ("red", "auto", {"ci": "FAILURE"}, "completed"), ("unverified", "fail", {}, "interrupted"),
                 ("moved", "auto", {"head": H2}, "cancelled"), ("gone", "auto", {"mergeable": "CONFLICTING"}, "gone"))
        for n, (ident, said, pr, _) in enumerate(cases):
            self.waiting(ident, pr=60 + n, said=said)
            self.forge.prs[60 + n].update(pr)
        queue.approve(self.pol, "approved-flagged")
        self.status, self.pol["run"]["threads"] = {f"t-{c[0]}": c[3] for c in cases}, 0
        got = queue.step(self.pol)
        work = {"red": f"{M['gate_check']} red", "unverified": f"no pass for head {H1[:12]}", "moved": f"no pass for head {H2[:12]}",
                "gone": "a merge conflict"}
        self.assertEqual(sorted((x["id"], x["why"]) for x in got["cleared"]),
                         sorted((k, f"its thread has ended, and PR {60 + [c[0] for c in cases].index(k)} waits on that thread's work ({w}), so it "
                                    "goes back to tasks") for k, w in work.items()))
        self.assertEqual((sorted(self.file()["tasks"]), sorted(self.file()["waiting"])), (sorted(work), sorted({c[0] for c in cases} - set(work))))
        tasks = self.file()["tasks"]
        self.assertEqual((len(queue.step(self.pol)["cleared"]), self.file()["tasks"]), (len(work), tasks))
        self.assertIn(f"The verifier failed head {H1}: fix its findings and push.", tasks["unverified"]["task"])
        self.assertIn(f"The verifier has not passed head {H2}: have it check the head you end on.", tasks["moved"]["task"])
        self.assertIn(f"{M['gate_check']} failed on head {H1}. Read its logs", tasks["red"]["task"])
        self.assertIn("GitHub says the PR conflicts with main. Merge origin/main", tasks["gone"]["task"])

    def test_a_wait_on_a_pr_another_item_holds_stays_and_of_two_ended_waits_on_one_pr_one_goes_back(self):
        self.waiting("a", pr=40, said="fail")
        self.waiting("b", pr=40, said="fail")
        self.status, self.pol["run"]["threads"] = {"t-a": "completed"}, 0
        self.assertEqual((queue.step(self.pol)["cleared"], sorted(self.file()["waiting"])), ([], ["a", "b"]))
        self.status["t-b"] = "cancelled"
        self.assertEqual(([x["id"] for x in queue.step(self.pol)["cleared"]], sorted(self.file()["waiting"])), (["a"], ["b"]))
        self.assertEqual(([x["id"] for x in queue.step(self.pol)["cleared"]], sorted(self.file()["tasks"]), sorted(self.file()["waiting"])),
                         (["a"], ["a"], ["b"]))

    def test_a_resumed_row_whose_pr_no_longer_needs_its_thread_waits_again_and_an_issue_hold_is_read_from_the_issue(self):
        self.cands = [cand(7)]
        self.start("app-7", "t-7")
        self.cands = []
        self.pr_open(47)
        queue.verified(self.pol, "app-7", 47, H1, "fail")
        queue.release(self.pol, "app-7")
        self.forge.issues[7] = []
        for n, ident in enumerate(("draft", "merged", "queued", "unread"), 40):
            self.waiting(ident, pr=n, said="fail")
        self.status = {f"t-{x}": "completed" for x in ("draft", "merged", "queued", "unread", "7")}
        self.pol["run"]["threads"] = 0
        queue.locked(self.pol, lambda q: (queue.settle(self.pol, q), True))
        self.assertEqual((sorted(self.file()["tasks"]), self.file()["waiting"]), (["app-7", "draft", "merged", "queued", "unread"], {}))
        self.forge.prs[40]["draft"], self.forge.prs[41]["state"], self.forge.prs[42]["inq"], self.forge.prs[43]["inq"] = True, "MERGED", True, "down"
        self.forge.issues[7] = [M["hold_label"]]
        got = queue.step(self.pol)
        unpassed = f"waits for the owner: the verifier has not passed head {H1}"
        self.assertEqual({x["id"]: x["why"] for x in got["kept"]},
                         {"app-7": f"PR 47 {unpassed}", "draft": f"PR 40 {unpassed}", "queued": f"PR 42 is in the merge queue at head {H1}",
                          "unread": "PR 43: whether it is in the merge queue could not be read: graphql down"})
        self.assertEqual((got["cleared"], self.file()["done"]), ([{"id": "merged", "why": "PR 41 merged"}], ["merged"]))
        self.assertEqual((self.file()["tasks"], sorted(self.file()["waiting"]), self.file()["waiting"]["queued"]["queued"]),
                         ({}, ["app-7", "draft", "queued", "unread"], H1))

    def test_a_resumed_row_keeps_its_pr_from_another_wait_and_an_item_whose_thread_is_live_stays(self):
        self.waiting("z", pr=80, said="fail")
        self.waiting("w", pr=70, said="fail")
        self.status, self.pol["run"]["threads"] = {"t-z": "completed", "t-w": "running"}, 0
        got = queue.step(self.pol)
        self.assertEqual(([x["id"] for x in got["cleared"]], sorted(self.file()["tasks"]), sorted(self.file()["waiting"])), (["z"], ["z"], ["w"]))

        def join(q):
            q["waiting"]["a"] = {**q["waiting"]["w"], "pr": 80, "thread": "t-a"}
            return None, True
        queue.locked(self.pol, join)
        self.status["t-a"] = "completed"
        got = queue.step(self.pol)
        self.assertEqual(([x["id"] for x in got["cleared"]], sorted(self.file()["tasks"]), sorted(self.file()["waiting"])),
                         (["z"], ["z"], ["a", "w"]))
        self.pol["run"]["threads"] = 9
        self.assertEqual((queue.step(self.pol)["launched"]["id"], sorted(self.file()["waiting"])), ("z", ["a", "w"]))

    def test_a_wait_on_the_pr_a_running_review_has_not_recorded_yet_stays(self):
        self.cands = [cand(50, kind="review")]
        self.start(f"app-50-review-{H1[:7]}")
        self.cands = []
        self.waiting("c", pr=50, said="fail")
        self.status, self.pol["run"]["threads"] = {"t-c": "completed"}, 0
        self.assertEqual((queue.step(self.pol)["cleared"], sorted(self.file()["waiting"])), ([], ["c"]))

    def test_an_unapproved_waiting_pr_is_left_for_its_owner(self):
        self.waiting(said="auto")
        self.assertEqual((queue.step(self.pol)["cleared"], self.merges()), ([], []))
        self.assertEqual(self.settle("a"), ("waiting", "PR 40 waits for the owner: its thread did not merge it"))


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
        self.complete("app-1")
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

    def test_each_ended_item_leaves_one_events_log_line_with_its_pr_head_and_the_verdict_on_that_head(self):
        self.pol["run"]["threads"] = 9
        for x in ("a", "b", "c"):
            self.run_(x, thread=f"t-{x}")
        self.pr_open(41)
        queue.verified(self.pol, "b", 41, H1, "auto")
        self.forge.prs[41].update(head=H2, state="CLOSED")
        self.pr_open(42)
        queue.verified(self.pol, "c", 42, H1, "review")
        self.complete("a")
        self.assertEqual([queue.release(self.pol, x)["state"] for x in ("a", "b", "c")], ["done", "done", "waiting"])
        queue.approve(self.pol, "c")
        self.assertEqual(queue.step(self.pol)["cleared"], [{"id": "c", "why": "PR 42 merged"}])
        log = (self.root / "events.log").read_text().splitlines()
        self.assertTrue(all(re.match(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ", x) for x in log), log)
        self.assertEqual([x.split(" ", 1)[1] for x in log],
                         ["ended a pr=- head=- verdict=- nothing to merge", f"ended b pr=41 head={H2} verdict=- PR 41 closed",
                          f"ended c pr=42 head={H1} verdict=review PR 42 merged"])

    def test_an_unwritable_events_log_still_ends_the_item_and_a_failed_save_logs_nothing_until_the_retry(self):
        self.pol["run"]["threads"] = 9
        self.run_("a")
        (self.root / "events.log").mkdir()
        self.complete("a")
        err = io.StringIO()
        with patch("sys.stderr", err):
            self.assertEqual(queue.release(self.pol, "a")["state"], "done")
        self.assertEqual((self.file()["done"], "ended a pr=- head=- verdict=- nothing to merge" in err.getvalue()), (["a"], True))
        (self.root / "events.log").rmdir()
        self.run_("b")
        self.complete("b")
        with patch.object(queue.os, "replace", side_effect=PermissionError("busy")), self.assertRaises(PermissionError):
            queue.release(self.pol, "b")
        self.assertFalse((self.root / "events.log").exists())
        self.assertEqual(queue.release(self.pol, "b")["state"], "done")
        self.assertEqual([x.split(" ", 1)[1] for x in (self.root / "events.log").read_text().splitlines()],
                         ["ended b pr=- head=- verdict=- nothing to merge"])

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
            queue.complete(self.pol, "c")
            self.assertEqual(queue.release(self.pol, hook=True)["why"], "nothing to merge")

    def test_a_session_that_runs_no_item_releases_nothing_and_an_id_releases_that_item(self):
        self.run_("a", thread="t-a")
        for thread in ("t-verifier", ""):
            with patch.dict(os.environ, {queue.THREAD_VAR: thread}):
                self.assertEqual(queue.release(self.pol), {"released": None})
        self.assertEqual(sorted(self.file()["running"]), ["a"])
        self.complete("a")
        self.assertEqual(queue.release(self.pol, "a")["state"], "done")
        for bad in ("a", "zzz"):
            with self.assertRaisesRegex(queue.Refused, f"{bad} is not running or waiting on its PR"):
                queue.release(self.pol, bad)

    def test_a_thread_that_dies_with_no_pr_goes_back_to_tasks_and_runs_again(self):
        self.pol["run"]["threads"] = 9
        self.cands = [cand(1)]
        self.start("app-1")
        self.add("a", "take over PR 40", priority=3)
        row = self.file()["tasks"]["a"]
        self.start("a")
        queue.approve(self.pol, "a")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-a"}), self.trees():
            got = queue.release(self.pol, died=True)
        said = "its thread died with no PR, so it goes back to tasks to run again"
        self.assertEqual((got["state"], got["why"], self.wakes), ("tasks", said, [("blocked", f"a: {said}", None)]))
        self.assertEqual((self.file()["tasks"], self.file()["done"]), ({"a": {**row, "preapproved": True}}, []))
        self.status = {"t-app-1": "failed"}
        with self.trees():
            got = queue.run_pass(self.pol)
        self.assertEqual([(x["released"], x["state"], x["why"]) for x in got["released"]], [("app-1", "tasks", said)])
        self.assertEqual(([x["id"] for x in got["launched"]], [x["task"] for x in self.briefed[-2:-1]]), (["a", "app-1"], ["take over PR 40"]))
        self.assertEqual((sorted(self.file()["running"]), self.file()["tasks"], self.file()["done"]), (["a", "app-1"], {}, []))
        with patch.object(queue, "branch_pr", side_effect=queue.Refused("gh down")):
            self.assertEqual(queue.release(self.pol, "a", died=True)["state"], "waiting")
        self.status = {"t-app-1": "completed"}
        self.complete("app-1")
        with self.trees():
            got = queue.run_pass(self.pol)
        self.assertEqual([(x["released"], x["state"]) for x in got["released"]], [("app-1", "done")])
        self.assertEqual((got["cleared"], [x["id"] for x in got["launched"]]), ([{"id": "a", "why": said}], ["a"]))
        self.assertEqual((sorted(self.file()["running"]), self.file()["waiting"], self.file()["done"]), (["a"], {}, ["app-1"]))
        queue.locked(self.pol, lambda q: (q["running"]["a"].pop("task"), True))
        with self.trees():
            self.assertEqual(queue.release(self.pol, "a", died=True)["why"], "nothing to merge")
        self.run_("gone")
        self.status = {"t-gone": "failed"}
        got = queue.run_pass(self.pol)
        why = "its thread died with no record of its pushes, so whether it opened a PR cannot be told"
        self.assertEqual(([(x["released"], x["state"], x["why"]) for x in got["released"]], got["launched"]), ([("gone", "done", why)], []))

    def test_a_turn_that_ends_with_no_pr_goes_back_to_tasks_unless_its_thread_marked_it_complete(self):
        self.pol["run"]["threads"] = 9
        for x in ("ended", "marked", "died", "open", "pushed", "unhooked"):
            self.add(x, f"do {x}")
            self.start(x)
        queue.log_push(self.pol, "origin", "refs/heads/pushed", H1, cwd="/nowhere/wt-pushed")
        rows = {x: self.file()["running"][x]["task"] for x in ("ended", "died")}
        self.pr_open(40)
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-marked"}):
            with self.assertRaisesRegex(queue.Refused, "this is not ended's own thread"):
                queue.complete(self.pol, "ended")
            self.assertEqual(queue.complete(self.pol, "marked"), {"complete": "marked"})
        for thread in ("", None):
            with patch.dict(os.environ, {queue.THREAD_VAR: thread or ""}), self.assertRaisesRegex(queue.Refused, "this is not ended's own thread"):
                if thread is None:
                    del os.environ[queue.THREAD_VAR]
                queue.complete(self.pol, "ended")
        with self.assertRaisesRegex(queue.Refused, "nobody is not running"):
            queue.complete(self.pol, "nobody")
        with self.trees():
            with patch.dict(os.environ, {queue.THREAD_VAR: "t-ended"}):
                ended = queue.release(self.pol, hook=True)
            marked = queue.release(self.pol, "marked")
            died = queue.release(self.pol, "died", died=True)
            with patch.object(queue, "branch_pr", lambda pol, it: 40):
                opened = queue.release(self.pol, "open")
            pushed = queue.release(self.pol, "pushed")
            with patch.object(queue, "hooked", lambda pol: False):
                unhooked = queue.release(self.pol, "unhooked")
        said = "its thread ended, not marked complete, with no PR, so it goes back to tasks to run again"
        self.assertEqual([(x["state"], x["why"]) for x in (ended, marked, died, opened)], [
            ("tasks", said), ("done", "nothing to merge"), ("tasks", "its thread died with no PR, so it goes back to tasks to run again"),
            ("waiting", f"PR 40 waits for the owner: the verifier has not passed head {H1}")])
        self.assertEqual([(x["state"], x["why"]) for x in (pushed, unhooked)], [
            ("tasks", "its thread ended, not marked complete, with no PR after it pushed refs/heads/pushed, so it goes back to tasks to run again"),
            ("tasks", said)])
        self.assertEqual(self.wakes[0], ("blocked", f"ended: {said}", None))
        got = self.file()
        self.assertEqual((sorted(got["tasks"]), got["done"], sorted(got["waiting"])), (["died", "ended", "pushed", "unhooked"], ["marked"], ["open"]))
        self.assertEqual({x: self.file()["tasks"][x]["task"] for x in rows}, {x: r["task"] for x, r in rows.items()})
        self.status = {"t-ended": "completed"}
        with self.trees():
            got = queue.run_pass(self.pol)
        self.assertEqual(sorted(x["id"] for x in got["launched"]), ["died", "ended", "pushed", "unhooked"])
        self.assertNotIn("complete", self.file()["running"]["ended"])
        with self.trees():
            self.assertEqual(queue.run_pass(self.pol)["released"][0]["state"], "tasks")
        self.complete("ended")
        self.status = {"t-ended": "completed"}
        with self.trees():
            got = queue.run_pass(self.pol)
        self.assertEqual([(x["released"], x["state"]) for x in got["released"]], [("ended", "done")])

    def test_a_turn_whose_push_the_hook_refused_waits_for_the_owner_and_runs_again_only_once_approved(self):
        self.pol["run"]["threads"] = 9
        for x in ("held", "died", "marked", "pushed", "lookup"):
            (self.root / f"wt-{x}").mkdir()
            self.run_(x, worktree=str(self.root / f"wt-{x}"), text=f"do {x}")
        row = self.file()["running"]["held"]["task"]
        hook = queue.Patent(self.pol)
        for x in ("held", "died", "marked", "lookup"):
            with patch.dict(os.environ, {queue.THREAD_VAR: f"t-{x}"}), patch.object(queue, "lint", lambda head, cwd=None: []), \
                    patch.object(queue.Patent, "diff_hits", lambda self, head, cwd=None: ["src/sealing.rs"]):
                said = io.StringIO()
                refused = hook.main(["origin", f"https://github.com/{REPO}"], io.StringIO(f"refs/heads/{x} {H1} refs/heads/{x} {'0' * 40}\n"),
                                    cwd=str(self.root / f"wt-{x}"), staging_ok=lambda: False, out=said)
            self.assertEqual((refused, "is woken when your turn ends" in said.getvalue()), (1, True))
        queue.log_push(self.pol, "origin", "refs/heads/pushed", H1, cwd=str(self.root / "wt-pushed"))
        self.complete("marked")
        self.assertEqual(self.wakes, [])
        hold_wakes = 0
        self.assertIn(" patent-held app:held at ", (self.root / "events.log").read_text())
        with self.trees():
            with patch.dict(os.environ, {queue.THREAD_VAR: "t-held"}):
                held = queue.release(self.pol, hook=True)
            died = queue.release(self.pol, "died", died=True)
            marked = queue.release(self.pol, "marked")
            pushed = queue.release(self.pol, "pushed")
            with patch.object(queue, "branch_pr", side_effect=queue.Refused("gh down")):
                lookup = queue.release(self.pol, "lookup")
            self.assertEqual((lookup["state"], self.file()["waiting"]["lookup"]["refused"]["why"]), ("waiting", lookup["why"]))
        wt = self.root / "wt-held"
        why = (f"its thread ended with no PR after the pre-push hook refused its push of refs/heads/held, since no allowed remote is "
               f"pushable, so it waits for the owner, its work in {wt}, and runs again once the owner runs `queue.py approve held`")
        self.assertEqual((held["state"], held["why"]), ("waiting", why))
        for x, said in ((died, "its thread died"), (marked, "its thread ended")):
            self.assertEqual(x["state"], "waiting")
            self.assertTrue(x["why"].startswith(f"{said} with no PR after the pre-push hook refused its push of refs/heads/"), x["why"])
        self.assertEqual(pushed["state"], "tasks")
        self.assertEqual(self.wakes[hold_wakes:], [("blocked", f"held: {why}", None), ("blocked", f"died: {died['why']}", None),
                                                   ("blocked", f"marked: {marked['why']}", None), ("blocked", f"pushed: {pushed['why']}", None),
                                                   ("blocked", f"lookup: {lookup['why']}", None)])
        got = self.file()
        self.assertEqual((sorted(got["waiting"]), sorted(got["tasks"]), got["done"]), (["died", "held", "lookup", "marked"], ["pushed"], []))
        self.assertEqual(got["waiting"]["held"]["refused"], {"refs": ["refs/heads/held"], "worktree": str(wt), "why": why, "woke": False})
        log = [x for x in (self.root / "events.log").read_text().splitlines() if " blocked " in x]
        self.assertEqual([x.split(" ", 3)[1:3] for x in log], [["blocked", x] for x in ("held", "died", "marked", "lookup")])
        self.assertIn(f"refs=refs/heads/held worktree={wt} ", log[0])
        for launched in (["pushed"], []):
            with self.trees():
                got = queue.run_pass(self.pol)
            self.assertEqual(([x["id"] for x in got["launched"]], [x["id"] for x in got["kept"]]), (launched, ["died", "held", "lookup", "marked"]))
        self.assertEqual(self.file()["waiting"]["lookup"]["refused"]["refs"], ["refs/heads/lookup"])
        self.assertEqual(len(self.wakes), hold_wakes + 5)
        self.assertEqual(len([x for x in (self.root / "events.log").read_text().splitlines() if " blocked " in x]), 4)
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-held"}), self.assertRaisesRegex(queue.Refused, "this is held's own thread"):
            queue.approve(self.pol, "held")
        self.assertEqual(queue.approve(self.pol, "held"), {"id": "held", "preapproved": "waiting", "unblocked": ["refs/heads/held"]})
        queue.approve(self.pol, "marked")
        with self.trees():
            got = queue.run_pass(self.pol)
        self.assertEqual(([x["id"] for x in got["launched"]], [x["id"] for x in got["kept"]]), (["held", "marked"], ["died", "lookup"]))
        self.assertEqual(([x["task"] for x in self.briefed[-2:]], self.file()["running"]["held"]["task"]),
                         ([row["task"], "do marked"], {**row, "preapproved": True}))
        self.assertEqual([sorted({"refused", "complete"} & set(self.file()["running"][x])) for x in ("held", "marked")], [[], []])
        (self.root / "wt-bare").mkdir()
        self.run_("bare", worktree=str(self.root / "wt-bare"), text="do bare")
        woken = len(self.wakes)
        with patch.dict(os.environ), patch.object(queue, "lint", lambda head, cwd=None: []), \
                patch.object(queue.Patent, "diff_hits", lambda self, head, cwd=None: ["src/sealing.rs"]):
            os.environ.pop(queue.THREAD_VAR, None)
            hook.main(["origin", f"https://github.com/{REPO}"], io.StringIO(f"refs/heads/bare {H1} refs/heads/bare {'0' * 40}\n"),
                      cwd=str(self.root / "wt-bare"), staging_ok=lambda: False, out=io.StringIO())
        self.assertEqual(len(self.wakes), woken)
        with self.trees():
            self.assertEqual(queue.release(self.pol, "bare")["state"], "waiting")
        self.assertEqual(len(self.wakes), woken + 1)
        (self.root / "wt-mixed").mkdir()
        self.run_("mixed", worktree=str(self.root / "wt-mixed"), text="do mixed")
        queue.log_push(self.pol, "origin", "refs/heads/mixed", H1, cwd=str(self.root / "wt-mixed"))
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-mixed"}), patch.object(queue, "lint", lambda head, cwd=None: []), \
                patch.object(queue.Patent, "diff_hits", lambda self, head, cwd=None: ["src/sealing.rs"]):
            hook.main(["origin", f"https://github.com/{REPO}"], io.StringIO(f"refs/heads/mixed {H2} refs/heads/mixed {H1}\n"),
                      cwd=str(self.root / "wt-mixed"), staging_ok=lambda: False, out=io.StringIO())
        with self.trees():
            self.assertEqual(queue.release(self.pol, "mixed", died=True)["state"], "waiting")
            queue.approve(self.pol, "mixed")
            got = queue.step(self.pol)
        self.assertEqual(([x["id"] for x in got["cleared"]], got["launched"]["id"]), (["mixed"], "mixed"))
        (self.root / "wt-early").mkdir()
        self.run_("early", worktree=str(self.root / "wt-early"), text="do early")
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-early"}), patch.object(queue, "lint", lambda head, cwd=None: []), \
                patch.object(queue.Patent, "diff_hits", lambda self, head, cwd=None: ["src/sealing.rs"]):
            hook.main(["origin", f"https://github.com/{REPO}"], io.StringIO(f"refs/heads/early {H1} refs/heads/early {'0' * 40}\n"),
                      cwd=str(self.root / "wt-early"), staging_ok=lambda: False, out=io.StringIO())
        woken = len(self.wakes)
        with self.trees():
            with patch.object(queue, "branch_pr", side_effect=queue.Refused("gh down")):
                self.assertEqual(queue.release(self.pol, "early")["state"], "waiting")
            self.assertEqual(queue.approve(self.pol, "early")["unblocked"], ["refs/heads/early"])
            got = queue.step(self.pol)
        self.assertEqual(([x["id"] for x in got["cleared"]], got["launched"]["id"], len(self.wakes)), (["early"], "early", woken + 1))
        woken = len(self.wakes)
        for x, status in (("late", "webhook sent (200)"), ("late-fail", "webhook failed: TimeoutError")):
            (self.root / f"wt-{x}").mkdir()
            self.run_(x, text=f"do {x}")
            queue.locked(self.pol, lambda q, x=x: (q["running"][x].update(worktree=None), True))
            with patch.dict(os.environ), patch.object(queue, "lint", lambda head, cwd=None: []), \
                    patch.object(queue.Patent, "diff_hits", lambda self, head, cwd=None: ["src/sealing.rs"]), \
                    patch.object(queue, "wake", lambda pol, kind, said, pr=None, thread=None, status=status: self.wake(pol, kind, said) and status):
                os.environ.pop(queue.THREAD_VAR, None)
                hook.main(["origin", f"https://github.com/{REPO}"], io.StringIO(f"refs/heads/{x} {H1} refs/heads/{x} {'0' * 40}\n"),
                          cwd=str(self.root / f"wt-{x}"), staging_ok=lambda: False, out=io.StringIO())
            woken += 1
            self.assertEqual((len(self.wakes), self.wakes[-1][0]), (woken, "blocked"))
            late = {"thread": {"status": "completed", "worktreePath": str(self.root / f"wt-{x}")}}
            with self.trees(), patch.object(queue, "t3", lambda pol, *a, stdin=None, late=late: late):
                got = queue.release(self.pol, x)
            woken += x == "late-fail"
            self.assertEqual((got["state"], bool(got["wake"]), len(self.wakes)), ("waiting", x == "late-fail", woken))
            self.assertEqual(self.file()["waiting"][x]["refused"]["woke"], x == "late")
        for x in ("unseen", "unseen-marked"):
            (self.root / f"wt-{x}").mkdir()
            self.run_(x, text=f"do {x}")
            queue.locked(self.pol, lambda q, x=x: (q["running"][x].update(worktree=None), True))
            with patch.dict(os.environ, {queue.THREAD_VAR: f"t-{x}"}), patch.object(queue, "lint", lambda head, cwd=None: []), \
                    patch.object(queue.Patent, "diff_hits", lambda self, head, cwd=None: ["src/sealing.rs"]):
                hook.main(["origin", f"https://github.com/{REPO}"], io.StringIO(f"refs/heads/{x} {H1} refs/heads/{x} {'0' * 40}\n"),
                          cwd=str(self.root / f"wt-{x}"), staging_ok=lambda: False, out=io.StringIO())
        self.complete("unseen-marked")
        self.t3_down = True
        with self.trees():
            got = [queue.release(self.pol, x) for x in ("unseen", "unseen-marked")]
        self.assertEqual([x["state"] for x in got], ["waiting", "waiting"])
        self.assertEqual([self.file()["waiting"][x]["refused"]["worktree"] for x in ("unseen", "unseen-marked")],
                         [os.path.realpath(self.root / f"wt-{x}") for x in ("unseen", "unseen-marked")])

    def test_a_refused_push_from_a_detached_or_reused_branch_waits_and_runs_again_once_approved(self):
        def git(*a, cwd):
            return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True,
                                  text=True).stdout.strip()
        up, clone, wt = self.root / "up.git", self.root / "clone", self.root / "wt-det"
        git("init", "-q", "--bare", str(up), cwd=self.root)
        git("clone", "-q", str(up), str(clone), cwd=self.root)
        git("commit", "-q", "--allow-empty", "-m", "one", cwd=clone)
        git("push", "-q", "origin", "HEAD", cwd=clone)
        git("worktree", "add", "-q", "--detach", str(wt), cwd=clone)
        git("worktree", "add", "-q", "-b", "att", str(self.root / "wt-att"), cwd=clone)
        self.pol["host"]["clone"] = str(clone)
        self.forge.prs[30] = {"head": H2, "branch": "det", "state": "MERGED"}
        self.forge.prs[31] = {"head": H2, "branch": "att", "state": "MERGED"}
        for x, tree in (("det", wt), ("att", self.root / "wt-att")):
            git("commit", "-q", "--allow-empty", "-m", "two", cwd=tree)
            self.run_(x, worktree=str(tree))
            head = git("rev-parse", "HEAD", cwd=tree)
            with patch.dict(os.environ, {queue.THREAD_VAR: f"t-{x}"}), patch.object(queue, "lint", lambda head, cwd=None: []), \
                    patch.object(queue.Patent, "diff_hits", lambda self, head, cwd=None: ["src/sealing.rs"]):
                queue.Patent(self.pol).main(["origin", f"https://github.com/{REPO}"], io.StringIO(f"HEAD {head} refs/heads/{x} {'0' * 40}\n"),
                                            cwd=str(tree), staging_ok=lambda: False, out=io.StringIO())
        with patch.object(queue, "hooked", lambda pol: True):
            got = {x: queue.release(self.pol, x) for x in ("det", "att")}
            self.assertEqual({x: (v["state"], self.file()["waiting"][x]["refused"]["refs"], self.file()["waiting"][x]["pr"]) for x, v in got.items()},
                             {x: ("waiting", [f"refs/heads/{x}"], None) for x in ("det", "att")})
            self.assertEqual(queue.step(self.pol)["kept"], [{"id": x, "why": got[x]["why"]} for x in ("att", "det")])
            for x in ("det", "att"):
                queue.approve(self.pol, x)
            got = queue.step(self.pol)
        self.assertEqual(([x["id"] for x in got["cleared"]], got["launched"]["id"]), (["att", "det"], "att"))
        self.assertEqual(sorted(self.file()["tasks"]), ["det"])
        self.assertEqual(len(self.wakes), 2)

    def test_a_failed_lookup_keeps_the_item_waiting_and_the_next_pass_tries_again(self):
        self.run_("a")
        self.complete("a")
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
        self.complete("clean", "dirty", "ahead", "gone", "open", "loose")
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

    def test_a_dead_thread_goes_back_to_tasks_only_when_the_hook_logged_no_push_from_its_worktree(self):
        self.pol["run"]["threads"] = 9
        for x in ("idle", "early", "pushed", "lost", "old", "unhooked"):
            (self.root / f"wt-{x}").mkdir()
            self.run_(x, worktree=str(self.root / f"wt-{x}"))
        with patch.object(queue, "now", lambda ms=False: "2000-01-01T00:00:00.000Z"):
            queue.log_push(self.pol, "origin", "refs/heads/early", H1, cwd=str(self.root / "wt-early"))
        for x in ("pushed", "lost"):
            queue.log_push(self.pol, "origin", f"refs/heads/{x}", H1, cwd=str(self.root / f"wt-{x}"))
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-unhooked"}):
            queue.log_push(self.pol, "origin", "refs/heads/unhooked", H1, cwd=str(self.root))
        self.assertEqual(queue.pushed_refs(self.pol, self.file()["running"]["unhooked"]), ["refs/heads/unhooked"])
        queue.locked(self.pol, lambda q: (q["running"]["old"].pop("logged"), True))
        self.forge.prs[40] = {"head": H1, "branch": "pushed"}
        with self.trees():
            got = {x: queue.release(self.pol, x, died=True) for x in ("idle", "early", "pushed", "lost", "old")}
            with patch.object(queue, "hooked", lambda pol: False):
                got["unhooked"] = queue.release(self.pol, "unhooked", died=True)
        back, unknown = ("tasks", "its thread died with no PR, so it goes back to tasks to run again"), \
            ("done", "its thread died with no record of its pushes, so whether it opened a PR cannot be told")
        self.assertEqual({x: (v["state"], v["why"]) for x, v in got.items()}, {
            "idle": back, "early": back, "pushed": ("waiting", "PR 40 waits for the owner: the verifier has not passed head " + H1),
            "lost": ("done", "its thread died after it pushed refs/heads/lost, and no PR of it was found, so whether it opened one cannot be told"),
            "old": unknown, "unhooked": unknown})
        self.assertEqual((self.file()["waiting"]["pushed"]["pr"], sorted(self.file()["tasks"])), (40, ["early", "idle"]))
        (self.root / "wt-spaced ").mkdir()
        (self.root / "wt-line\n").mkdir()
        with patch.object(queue.os, "getcwd", lambda: str(self.root / "wt-line\n")):
            queue.log_push(self.pol, "origin", "refs/heads/line", H1)
        self.assertEqual(queue.pushed_refs(self.pol, {"worktree": str(self.root / "wt-line\n"), "at": ""}), ["refs/heads/line"])
        (self.root / "wt-unread").mkdir()
        self.run_("unread", worktree=str(self.root / "wt-unread"), priority=5)
        self.assertEqual(queue.pushed_refs(self.pol, {"worktree": str(self.root / "wt-spaced "), "at": ""}), [])
        queue.log_push(self.pol, "origin", "refs/heads/spaced", H1, cwd=str(self.root / "wt-spaced "))
        self.assertEqual(queue.pushed_refs(self.pol, {"worktree": str(self.root / "wt-spaced "), "at": ""}), ["refs/heads/spaced"])
        (self.root / "pushes.jsonl").chmod(0)
        self.addCleanup((self.root / "pushes.jsonl").chmod, 0o644)
        with self.trees():
            self.assertEqual(queue.release(self.pol, "unread", died=True)["why"], unknown[1])

    def test_the_hook_is_known_only_when_the_clone_links_this_package_s_pre_push(self):
        clone = self.root / "clone"
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        self.pol["host"]["clone"] = str(clone)
        self.assertFalse(queue.hooked(self.pol))
        (clone / ".git" / "hooks" / "pre-push").symlink_to(PKG / "hooks" / "pre-push")
        self.assertTrue(queue.hooked(self.pol))

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
        for name, to in (("Launch(m, i)", "running"), ("Leave(m, i, marked)", "waiting"), ("Leave(m, i, marked)", "done"), ("Settle(m, i)", "done")):
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
        self.status = {"t-a": "completed"}
        self.complete("a")
        got = queue.run_pass(self.pol)
        self.assertEqual([(x["released"], x["state"]) for x in got["released"]], [("a", "done")])
        self.assertEqual(([x["id"] for x in got["launched"]], got["waiting"]), (["c"], "the machine is at its thread cap"))
        self.assertEqual((sorted(self.file()["running"]), sorted(self.file()["tasks"]), self.woken), (["b", "c"], ["d"], ["t-a"]))
        self.pol["run"]["threads"] = 9
        queue.run_pass(self.pol)
        for said in queue.DEAD:
            self.run_(said)
            self.complete(*[said] if said not in queue.DIED else [])
            self.status = {f"t-{said}": said, "t-c": "waiting", "t-b": "queued"}
            with self.trees():
                got = queue.run_pass(self.pol)
            self.assertEqual([(x["released"], x["state"]) for x in got["released"]], [(said, "tasks" if said in queue.DIED else "done")])
            self.assertEqual([x["id"] for x in got["launched"]], [said] if said in queue.DIED else [])
            self.status = {}
        self.assertEqual(sorted(self.file()["running"]), ["b", "c", "cancelled", "d", "failed", "interrupted"])

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
        with self.trees():
            got = queue.run_pass(self.pol)
        self.assertEqual(([x["released"] for x in got["released"]], [x["id"] for x in got["launched"]]), (["a"], ["a"]))
        self.assertEqual(sorted(self.file()["tasks"]), ["b"])

    def test_a_worktree_the_launch_could_not_read_is_filled_in_by_the_next_pass(self):
        self.threads["a"] = ("t-a", None)
        self.add("a")
        self.assertEqual(queue.run_pass(self.pol)["launched"], [{"id": "a", "thread": "t-a", "worktree": None}])
        queue.run_pass(self.pol)
        self.assertEqual(self.file()["running"]["a"]["worktree"], "/seen/t-a")

    def test_a_dead_thread_whose_worktree_the_launch_could_not_read_still_finds_its_pr(self):
        self.threads["a"] = ("t-a", None)
        self.add("a")
        queue.run_pass(self.pol)
        self.pr_open(40)
        queue.verified(self.pol, "a", 40, H1, "review")
        self.status = {"t-a": "failed"}
        with self.trees(), patch.object(queue, "branch_pr", lambda pol, it: 40 if it["worktree"] == "/seen/t-a" else None):
            got = queue.run_pass(self.pol)
        self.assertEqual(([(x["released"], x["state"]) for x in got["released"]], got["launched"]), ([("a", "waiting")], []))
        self.assertEqual((sorted(self.file()["waiting"]), self.file()["tasks"]), (["a"], {}))
        self.threads["b"] = ("t-b", None)
        self.add("b")
        queue.run_pass(self.pol)
        self.pr_open(41)
        with patch.dict(os.environ, {queue.THREAD_VAR: "t-b"}), self.trees(), \
                patch.object(queue, "branch_pr", lambda pol, it: 41 if it["worktree"] == "/seen/t-b" else None):
            self.assertEqual(queue.release(self.pol, died=True)["state"], "waiting")
        self.threads["c"] = ("t-c", None)
        self.add("c")
        queue.run_pass(self.pol)
        self.status = {"t-c": "gone"}
        got = queue.run_pass(self.pol)
        said = "its thread died with no record of its pushes, so whether it opened a PR cannot be told"
        self.assertEqual(([(x["released"], x["state"], x["why"]) for x in got["released"]], got["launched"]), ([("c", "done", said)], []))

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
            for bad in (["approve"], ["approve", "a", "b"]):
                self.assertEqual(queue.main(bad), 2, bad)
            self.assertEqual(queue.main(["approve", "c"]), 0)
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["approve", "zzz"]), 1)
            self.assertEqual(err.getvalue(), "queue: zzz is not a task, running or waiting, so there is nothing to approve\n")
            self.forge.prs[40] = {"head": H1}
            for bad in (["verified", "a", "auto"], ["verified", "a", "40", H1], ["verified", "a", "40", H1, "pass", "auto"],
                        ["verified", "a", "40", H1, "extreme"], ["verified", "a", "40", H1[:7], "auto"], ["gate", "40", H1[:7]],
                        ["release", "a", "b"], ["complete"], ["complete", "a", "b"]):
                self.assertEqual(queue.main(bad), 2, bad)
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["complete", "zzz"]), 1)
            self.assertEqual(err.getvalue(), "queue: zzz is not running, so there is no turn of it to mark complete\n")
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["gate", "40", H1]), 1)
            self.assertEqual(err.getvalue(), f"queue: PR 40 does not merge: the verifier has not passed head {H1}\n")
            self.assertEqual(queue.main(["verified", "a", "40", H1, "auto"]), 0)
            for ok in (["gate", "40", H1], ["release", "--hook"], ["release", "--died"], ["release", "a"], ["release"], ["list"], ["pass"]):
                self.assertEqual(queue.main(ok), 0, ok)
            self.t3_down = True
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["pass"]), 1)
            self.assertEqual(err.getvalue(), "queue: T3 Code does not answer, so no thread can be read or launched: connection refused\n")
        self.assertEqual(sorted(self.file()["waiting"]), ["a"])


if __name__ == "__main__":
    unittest.main()
