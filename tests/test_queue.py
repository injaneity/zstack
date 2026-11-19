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
PLANT = queue.plant
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
        self.forge, self.cands, self.open, self.wakes, self.trees = Forge(), [], {}, [], {}
        for p in (patch.object(queue, "gh", self.forge.gh),
                  patch.object(queue, "candidates", lambda errors, pol: list(self.cands)),
                  patch.object(queue, "open_prs", lambda errors, name: dict(self.open)),
                  patch.object(queue, "plant", lambda pol, ident: self.trees.get(ident, f"/nowhere/wt-{ident}")),
                  patch.object(queue, "wake", lambda pol, kind, said, pr=None: self.wakes.append((kind, said, pr)) or "sent")):
            p.start()
            self.addCleanup(p.stop)

    def file(self):
        return json.loads((self.root / "queue.json").read_text())

    def add(self, ident, text="do the thing", priority=0, preapproved=False):
        return queue.add(self.pol, ident, text, priority, preapproved)

    def run_(self, ident, thread=None, worktree=None, **task):
        self.add(ident, **task)
        return self.claim(ident, thread or f"t-{ident}", worktree)

    def claim(self, ident, thread, worktree=None):
        if worktree:
            self.trees[ident] = worktree
        at = queue.claim(self.pol, ident)["worktree"]
        self.assertEqual(self.start(thread, at), {"attached": ident, "thread": thread})
        return self.file()["running"][ident]

    def start(self, thread, cwd):
        with patch.dict(os.environ, {queue.THREAD_VAR: thread} if thread else {}), patch.object(os, "getcwd", return_value=cwd):
            if not thread:
                os.environ.pop(queue.THREAD_VAR, None)
            return queue.attach(self.pol)

    def pr_open(self, pr, head=H1, labels=()):
        self.forge.prs[pr] = {"head": head, "labels": list(labels)}


class Entry(Case):
    def test_next_reads_the_repo_and_counts_why_it_passed_over_the_rest(self):
        hold = M["hold_label"]
        self.cands = [cand(1), cand(7, "review", labels=[hold]), cand(2, labels=[hold]), cand(3, linked=[70]), cand(4, author="app/App-agents"),
                      cand(5, author="x[bot]"), cand(6, linked=[9])]
        self.open = {9: [], 70: [hold]}
        got = queue.next_up(self.pol)
        self.assertEqual((got["claim"], got["waiting"]), ("app-1", None))
        self.assertEqual(got["skipped"], {"held": 3, "skipped-author": 2, "pr": 1})
        self.assertFalse((self.root / "queue.json").exists())

    def test_a_failed_read_launches_nothing_and_says_why(self):
        with patch.object(queue, "candidates", READS[0]), patch.object(queue, "open_prs", READS[1]), \
                patch.object(queue, "gh", lambda *a, **k: (1, "", "gh down")):
            got = queue.next_up(self.pol)
            self.assertEqual((got["claim"], got["waiting"]), (None, "gh issue list -R: gh down"))
            self.add("a")
            with self.assertRaisesRegex(queue.Refused, "a is not next: gh issue list -R: gh down"):
                self.claim("a", "t", "/w")

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
            self.assertEqual(queue.next_up(self.pol)["skipped"], {"held": 2})

    def test_a_machine_takes_its_labels_one_takes_the_unlabelled_and_an_item_with_two_goes_to_one(self):
        mac = {"labels": ["macOS"], "unlabelled": False, "all_labels": ["Linux", "macOS"]}
        linux = {"labels": ["Linux"], "unlabelled": True, "all_labels": ["Linux", "macOS"]}
        for labels, on_mac, on_linux in ((["macOS"], True, False), (["Linux"], False, True), (["bug"], False, True), ([], False, True),
                                         (["macOS", "Linux"], False, True)):
            self.assertEqual((queue.takes(labels, mac), queue.takes(labels, linux)), (on_mac, on_linux), labels)
        self.assertTrue(queue.takes(["Linux"], {"labels": [], "unlabelled": False, "all_labels": []}))
        self.pol["host"].update(mac)
        self.cands = [cand(1, labels=["Linux"])]
        self.assertEqual(queue.next_up(self.pol)["skipped"], {"other-machine": 1})

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
            self.claim(c["id"], f"t-{c['n']}", "/w")
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
        self.assertEqual(queue.next_up(self.pol)["claim"], "app-2")
        with self.assertRaisesRegex(queue.Refused, "low is not next: app-2 is"):
            self.claim("low", "t", "/w")
        got = self.claim("app-2", "t-2", "/w/2")
        self.assertEqual((got["thread"], got["worktree"], got["n"], got["kind"]), ("t-2", "/w/2", 2, "issue"))
        got = queue.next_up(self.pol)
        self.assertEqual((got["claim"], got["skipped"]), ("high", {"running": 1}))

    def test_a_claim_is_refused_past_the_thread_cap_for_an_item_already_running_and_for_one_nobody_listed(self):
        for x in ("a", "b"):
            self.run_(x)
        self.add("c")
        got = queue.next_up(self.pol)
        self.assertEqual((got["claim"], got["waiting"], got["running"]), (None, "the machine is at its thread cap", {"a": "t-a", "b": "t-b"}))
        with self.assertRaisesRegex(queue.Refused, "c is not next: the machine is at its thread cap"):
            self.claim("c", "t", "/w")
        with self.assertRaisesRegex(queue.Refused, "a is already running"):
            self.claim("a", "t", "/w")
        self.pol["run"]["threads"] = 9
        with self.assertRaisesRegex(queue.Refused, "zzz is not next: c is"):
            self.claim("zzz", "t", "/w")
        self.claim("c", "t", "/w")
        with self.assertRaisesRegex(queue.Refused, "zzz is not next: nothing is ready"):
            self.claim("zzz", "t", "/w")

    def test_a_claimed_task_leaves_the_tasks_and_an_issues_preapproval_is_read_at_the_claim(self):
        self.run_("a", preapproved=True)
        self.cands = [cand(1, labels=M["preapproved_labels"][:1])]
        self.claim("app-1", "t-1", "/w/1")
        got = self.file()
        self.assertEqual((got["tasks"], [got["running"][x]["preapproved"] for x in ("a", "app-1")]), ({}, [True, True]))

    def test_a_claim_runs_the_item_before_any_thread_exists_and_says_the_same_again_until_one_is_recorded(self):
        self.pol["run"]["threads"] = 1
        self.add("a", "Fix the parser")
        self.add("b")
        got = queue.claim(self.pol, "a")
        self.assertEqual((got["id"], got["worktree"], "Fix the parser" in got["brief"]), ("a", "/nowhere/wt-a", True))
        self.assertEqual((self.file()["running"]["a"]["thread"], self.file()["tasks"].keys() == {"b"}), (None, True))
        self.assertEqual(queue.claim(self.pol, "a"), got)
        got = queue.next_up(self.pol)
        self.assertEqual((got["claim"], got["waiting"], got["running"]), (None, "the machine is at its thread cap", {"a": None}))
        self.pr_open(40)
        with self.assertRaisesRegex(queue.Refused, "a has no thread recorded yet, so its verifier cannot be told from its thread"):
            queue.verified(self.pol, "a", 40, H1, "auto")
        for thread, cwd in ((None, "/nowhere/wt-a"), ("t-1", "/nowhere/wt-ab"), ("t-1", "/nowhere")):
            self.assertEqual(self.start(thread, cwd), {"attached": None})
        self.assertEqual(self.start("t-1", "/nowhere/wt-a/src"), {"attached": "a", "thread": "t-1"})
        for later in ("t-1", "t-verifier"):
            self.assertEqual(self.start(later, "/nowhere/wt-a"), {"attached": None})
        self.assertEqual(self.file()["running"]["a"]["thread"], "t-1")
        with self.assertRaisesRegex(queue.Refused, "a is already running"):
            queue.claim(self.pol, "a")

    def test_a_claim_that_cannot_make_its_worktree_takes_nothing(self):
        self.add("a")
        with patch.object(queue, "plant", side_effect=queue.Refused("cannot make the worktree")):
            with self.assertRaisesRegex(queue.Refused, "cannot make the worktree"):
                queue.claim(self.pol, "a")
        self.assertEqual((self.file()["running"], list(self.file()["tasks"])), ({}, ["a"]))

    def test_the_worktree_is_a_new_branch_from_the_remotes_default_branch_as_it_is_now(self):
        def git(*a, cwd):
            cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a]
            return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout
        up, clone, other = self.root / "up.git", self.root / "clone", self.root / "other"
        git("init", "-q", "--bare", "-b", "trunk", str(up), cwd=self.root)
        git("clone", "-q", str(up), str(clone), cwd=self.root)
        git("commit", "-q", "--allow-empty", "-m", "one", cwd=clone)
        git("push", "-q", "origin", "HEAD:trunk", cwd=clone)
        self.pol["host"]["clone"] = str(clone)
        git("clone", "-q", str(up), str(other), cwd=self.root)
        git("commit", "-q", "--allow-empty", "-m", "two", cwd=other)
        git("push", "-q", "origin", "HEAD:trunk", cwd=other)
        git("commit", "-q", "--allow-empty", "-m", "local only", cwd=clone)
        path = PLANT(self.pol, "a")
        self.assertEqual(path, os.path.realpath(self.root / "trees" / "a"))
        self.assertEqual(git("log", "-1", "--format=%s %D", cwd=path).strip(), "two HEAD -> zstack/a, origin/trunk, origin/HEAD")
        with self.assertRaisesRegex(queue.Refused, "trees/a is already there, and may hold someone's work: move it aside"):
            PLANT(self.pol, "a")
        self.trees["a"] = path
        self.add("a")
        queue.claim(self.pol, "a")
        self.assertEqual((queue.release(self.pol, "a")["state"], os.path.exists(path)), ("done", False))
        self.assertEqual(git("branch", "--list", "zstack/*", cwd=clone), "")
        git("branch", "zstack/b", cwd=clone)
        with self.assertRaisesRegex(queue.Refused, "on a new branch zstack/b from origin/trunk: if that branch is there, it may hold work"):
            PLANT(self.pol, "b")
        self.assertEqual(git("log", "-1", "--format=%s", "zstack/b", cwd=clone).strip(), "local only")
        git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/gone", cwd=clone)
        self.assertEqual(git("log", "-1", "--format=%D", cwd=PLANT(self.pol, "c")).strip(), "HEAD -> zstack/c, origin/trunk, origin/HEAD")
        with patch.object(queue, "git", lambda *a, **k: (0, "origin/trunk\n") if "rev-parse" in a or "set-head" in a else (1, "")):
            with self.assertRaisesRegex(queue.Refused, "git fetch origin failed in .*, so the worktree would start from an old origin/trunk"):
                PLANT(self.pol, "d")
        with patch.object(queue, "git", lambda *a, **k: (1, "")):
            with self.assertRaisesRegex(queue.Refused, "cannot read the default branch of origin"):
                PLANT(self.pol, "d")
        for bad in ("a..b", "a.", "a.lock", ".a"):
            with self.assertRaisesRegex(queue.Refused, "that can name a branch"):
                self.add(bad)

    def test_the_brief_tells_the_thread_how_to_report(self):
        self.add("a", "Fix the parser")
        said = queue.claim(self.pol, "a")["brief"]
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
        self.claim("app-1", "t-1", "/w/1")
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
        self.assertEqual((sorted(self.file()["waiting"]), self.file()["running"]), (["a"], {}))
        self.assertEqual(queue.next_up(self.pol)["claim"], "b")
        queue.verified(self.pol, "a", 40, H1, "auto")
        self.assertEqual(queue.gate(self.pol, 40, H1), {"merge": 40, "head": H1})
        self.assertEqual(len(self.wakes), 1)

    def test_a_finished_item_is_forgotten_and_is_not_picked_up_again_while_the_repo_still_lists_it(self):
        self.cands = [cand(1), cand(2)]
        self.claim("app-1", "t-1", "/nowhere/1")
        self.claim("app-2", "t-2", "/nowhere/2")
        self.pr_open(40)
        self.forge.prs[40]["state"] = "MERGED"
        with patch.object(queue, "branch_pr", side_effect=[None, 40, 40]):
            got = [queue.release(self.pol, x) for x in ("app-1", "app-2")]
        self.assertEqual([(x["state"], x["why"]) for x in got], [("done", "nothing to merge"), ("done", "PR 40 merged")])
        self.assertEqual(self.wakes, [("done", "app-1: nothing to merge", None), ("done", "app-2: PR 40 merged", 40)])
        self.assertEqual(self.file(), {"running": {}, "waiting": {}, "tasks": {}, "verdicts": {}, "done": ["app-1", "app-2"]})
        self.cands = [cand(1)]
        got = queue.next_up(self.pol)
        self.assertEqual((got["claim"], got["skipped"], self.file()["done"]), (None, {"done": 1}, ["app-1"]))
        self.cands = []
        queue.next_up(self.pol)
        self.cands = [cand(1)]
        self.assertEqual(queue.next_up(self.pol)["claim"], "app-1")

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

    def test_a_session_that_runs_no_item_releases_nothing_and_the_dispatcher_releases_by_id(self):
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
        self.assertEqual(queue.next_up(self.pol)["cleared"], [{"id": "a", "why": "nothing to merge"}])
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
        got = queue.next_up(self.pol)
        self.assertEqual((got["cleared"], self.file()["waiting"]["open"]["pr"], (self.root / "wt-open").exists()), ([], 52, True))
        self.forge.prs[52]["state"] = "MERGED"
        (self.root / "wt-dirty" / "new.txt").unlink()
        self.assertEqual(queue.next_up(self.pol)["cleared"], [{"id": "dirty", "why": "nothing to merge"}, {"id": "open", "why": "PR 52 merged"}])
        left = [(self.root / "wt-open").exists(), (self.root / "wt-dirty").exists()]
        self.assertEqual((sorted(self.file()["waiting"]), left), (["ahead", "loose"], [False, False]))

    def test_a_queue_file_from_an_older_zstack_is_refused_by_name(self):
        for old in ({"items": {}}, {"items": {}, "done": []}, {"running": {}, "waiting": {}, "tasks": {}, "done": []}):
            (self.root / "queue.json").write_text(json.dumps(old))
            with self.assertRaisesRegex(queue.Refused, "queue.json was written by an older zstack: move it aside"):
                queue.next_up(self.pol)

    def test_an_open_pr_someone_waits_on_blocks_an_overlapping_pickup(self):
        self.cands = [cand(1)]
        self.claim("app-1", "t-1", "/nowhere/1")
        self.pr_open(40)
        self.forge.issues[1] = []
        queue.verified(self.pol, "app-1", 40, H1, "review")
        queue.release(self.pol, "app-1")
        self.cands = [cand(1), cand(40, "review")]
        self.assertEqual(queue.next_up(self.pol)["skipped"], {"waiting": 1, "overlap": 1})

    def test_the_states_are_the_models(self):
        spec = (PKG / "model" / "Queue.tla").read_text()
        for name, to in (("Claim(m, i)", "running"), ("Leave(m, i)", "waiting"), ("Leave(m, i)", "done"), ("Settle(m, i)", "done")):
            self.assertIn(f'"{to}")', spec[spec.index(f"\n{name} =="):].split("\n\n")[0].split("\nSettle")[name == "Settle(m, i)"], name)
        states = set(spec[spec.index("States == {") + 11:spec.index("}", spec.index("States == {"))].replace('"', "").split(", "))
        self.assertEqual(states - {"none"}, set(queue.KEYS) - {"tasks", "verdicts"})


class Cli(Case):
    def test_the_commands(self):
        with patch.object(queue, "load", return_value=self.pol), patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            self.assertEqual(queue.main(["add", "a", "--priority", "3"], stdin=io.StringIO("task")), 0)
            self.assertEqual(queue.main(["add", "b", "--priority"], stdin=io.StringIO("task")), 2)
            self.assertEqual(queue.main(["add", "c", "--preapproved", "--priority", "1"], stdin=io.StringIO("task")), 0)
            self.assertEqual(queue.main(["add", "d", "--preapproved", "--preapproved"], stdin=io.StringIO("task")), 2)
            for gone in (["pickup"], ["holds"], ["hold", "add", "x"], ["start", "a"], ["claim", "a", "t-1", "/w"], ["attach", "a", "t-1"], ["nope"]):
                self.assertEqual(queue.main(gone), 2, gone)
            self.assertEqual(queue.main(["claim", "a"]), 0)
            with patch.dict(os.environ, {queue.THREAD_VAR: "t-1"}), patch.object(os, "getcwd", return_value="/nowhere/wt-a"):
                self.assertEqual(queue.main(["attach"]), 0)
            self.forge.prs[40] = {"head": H1}
            for bad in (["verified", "a", "auto"], ["verified", "a", "40", H1], ["verified", "a", "40", H1, "pass", "auto"],
                        ["verified", "a", "40", H1, "extreme"], ["verified", "a", "40", H1[:7], "auto"], ["gate", "40", H1[:7]],
                        ["release", "a", "b"]):
                self.assertEqual(queue.main(bad), 2, bad)
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["gate", "40", H1]), 1)
            self.assertEqual(err.getvalue(), f"queue: PR 40 does not merge: the verifier has not passed head {H1}\n")
            self.assertEqual(queue.main(["verified", "a", "40", H1, "auto"]), 0)
            for ok in (["gate", "40", H1], ["release", "--hook"], ["release", "a"], ["release"], ["list"], ["next"]):
                self.assertEqual(queue.main(ok), 0, ok)
            with patch("sys.stderr", io.StringIO()) as err:
                self.assertEqual(queue.main(["claim", "zzz"]), 1)
            self.assertEqual(err.getvalue(), "queue: zzz is not next: c is\n")
        self.assertEqual(sorted(self.file()["waiting"]), ["a"])


if __name__ == "__main__":
    unittest.main()
