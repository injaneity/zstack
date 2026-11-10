"""Tests for hosts/vm/merge_job.py and hosts/vm/patent_push.py. No test reaches GitHub.
Run: python3 -m unittest discover -s tests -p 'test_*.py'"""
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

VM = Path(__file__).resolve().parent.parent / "hosts" / "vm"


def load(name):
    spec = importlib.util.spec_from_file_location(name, VM / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mj = load("merge_job")
wake = mj.wake   # the module merge_job imported; tests patch its KEY_FILE and urlopen
pp = load("patent_push")

H, H2 = "a" * 40, "b" * 40
HOLDS = """# comment
hold           | 5675          | yes | open PR, never touch
hands-off-user | someone       | no  | hands-off login
skip-author    | app/cua-agents | no | bot
review-only    | f-trycua      | no  | teammate: first review only
hands-off-path | impl_.rs      | yes | mode=split-undone | only if it undoes #2886
hands-off-path | never.rs      | no  | Zane reviews every change to it
exception      | impl_.rs      | yes | #5686 follow-up | the MSAA path | Zane 26 Oct
"""
FIX = mj.parse_holds(HOLDS)


def cr(name, status="COMPLETED", conclusion="SUCCESS", started="2026-10-25T01:00:00Z", run="111", workflow="CI"):
    return {"__typename": "CheckRun", "name": name, "workflowName": workflow, "status": status, "conclusion": conclusion,
            "startedAt": started, "detailsUrl": f"https://github.com/trycua/cua/actions/runs/{run}/job/9"}


def pr(**kw):
    base = {"number": 5900, "headRefOid": H, "isDraft": False, "mergeable": "MERGEABLE", "labels": [],
            "body": "change\nrisk: light\n", "files": ["README.md"], "statusCheckRollup": [cr("build"), cr("ci-gate")]}
    base.update(kw)
    return base


def verdict(p, holds=FIX, paused=None, rerun_head=None):
    notes = []
    reasons = mj.gate(p, holds, notes) + mj.blockers(p, paused)
    act, who, why = mj.next_action(reasons, notes, p, rerun_head)
    return (act, why) if who == "none" else (act, who, why)


def codes(p, holds=FIX, paused=None):
    return [r.code for r in mj.gate(p, holds) + mj.blockers(p, paused)]


class Gate(unittest.TestCase):
    """Exactly three checks at the head: ci-gate SUCCESS, no hold row, deep/human preapproved at head."""

    def test_green_light_merges_and_nothing_else_is_read(self):
        self.assertEqual(verdict(pr()), ("merge", "green, ci-gate SUCCESS, risk light"))
        self.assertEqual(verdict(pr(statusCheckRollup=[cr("ci-gate")], body="risk: standard\n")),
                         ("merge", "green, ci-gate SUCCESS, risk standard"))

    def test_ci_gate_must_be_success_at_head(self):
        for c, word in ((cr("ci-gate", conclusion="SKIPPED"), "SKIPPED"), (cr("ci-gate", conclusion="NEUTRAL"), "NEUTRAL"),
                        (cr("ci-gate", conclusion="FAILURE"), "FAILURE"),
                        (cr("ci-gate", status="IN_PROGRESS", conclusion=None), "PENDING")):
            got = mj.gate(pr(statusCheckRollup=[cr("build"), c]), FIX)
            self.assertEqual([(r.code, r.text) for r in got], [("ci", f"ci-gate {word}")], word)
        got = mj.gate(pr(statusCheckRollup=[cr("build")]), FIX)
        self.assertEqual([(r.code, r.text) for r in got], [("ci", "ci-gate missing")])
        # a rerun supersedes the older ci-gate run
        late = [cr("ci-gate", conclusion="FAILURE"), cr("ci-gate", started="2026-10-25T02:00:00Z")]
        self.assertEqual(verdict(pr(statusCheckRollup=late))[0], "merge")

    def test_skipped_missing_or_red_platform_jobs_pass_when_ci_gate_is_green(self):
        win = "libs/cua-driver/rust/crates/platform-windows/src/lib.rs"
        for rollup in ([cr("ci-gate")], [cr("ci-gate"), cr("Rust Windows shard 1/3", conclusion="SKIPPED")],
                       [cr("ci-gate"), cr("Rust Windows shard 1/3", conclusion="FAILURE", workflow="CI: Rust Windows")],
                       [cr("ci-gate"), cr("e2e", status="IN_PROGRESS", conclusion=None)]):
            self.assertEqual(verdict(pr(files=[win], statusCheckRollup=rollup))[0], "merge", rollup)

    def test_cla_never_blocks(self):
        """CUA-3481: the CLA rule is gone."""
        for cla in (cr("cla", conclusion="FAILURE"), cr("cla", status="IN_PROGRESS", conclusion=None),
                    {"__typename": "StatusContext", "context": "cla", "state": "FAILURE"}):
            for path in ("libs/cua-spacesd/src/x.rs", "libs/cua-driver/src/x.rs"):
                p = pr(files=[path], statusCheckRollup=[cr("ci-gate"), cla])
                self.assertEqual(verdict(p), ("merge", "green, ci-gate SUCCESS, risk light"), (cla, path))
        self.assertFalse(hasattr(mj, "NON_REQUIRED_CHECKS"))

    def test_a_pr_without_a_tier_from_the_enum_is_skipped_never_merged(self):
        for body, labels in (("no tier here\n", []), ("risk: high\n", []), ("risk: low\n", []), ("x risk: deep\n", []),
                             ("", ["risk:high"]), ("risk: lightly\n", [])):
            self.assertEqual(verdict(pr(body=body, labels=labels)), ("wait", "session", "no risk tier (light|standard|deep|human)"), body)

    def test_hold_rows_and_unreadable_holds(self):
        self.assertEqual(verdict(pr(number=5675)), ("wait", "zane", "hold: #5675 has a hold row in the holds file"))
        self.assertEqual(verdict(pr(number=5675, isDraft=True)), ("wait", "hold: #5675 has a hold row in the holds file; draft"))
        self.assertEqual(verdict(pr(), holds=None), ("wait", "zane", "holds file unreadable: every PR counts as held"))

    def test_hands_off_path(self):
        self.assertEqual(verdict(pr(files=["a/tools/never.rs", "b.rs"])),
                         ("wait", "zane", "touches never.rs (hands-off path, mode never; see the holds file)"))
        self.assertEqual(verdict(pr(files=["a/never.rs.bak", "a/my_never.rs"]))[0], "merge")

    def test_a_split_undone_row_holds_nothing_and_a_hold_row_stops_the_pr(self):
        """split-undone is gone (Zane 10 Nov): the live file's impl_.rs row no longer holds, and a PR Zane wants stopped
        gets a plain hold row."""
        p = pr(files=["libs/x/src/tools/impl_.rs"])
        self.assertEqual(verdict(p), ("merge", "green, ci-gate SUCCESS, risk light"))
        held = mj.parse_holds(HOLDS + "hold | 5900 | no | Zane reviews this one\n")
        self.assertEqual(verdict(p, holds=held), ("wait", "zane", "hold: #5900 has a hold row in the holds file"))

    def test_deep_and_human_need_a_preapprove_row_at_exactly_the_head(self):
        pre = mj.parse_holds(HOLDS + f"preapprove | #5900 | no | {H} | Zane, test\n")
        for tier in ("deep", "human"):
            want = tier
            p = pr(body=f"risk: {tier}\n")
            self.assertEqual(verdict(p), ("wait", "zane", f"declared {want}: needs Zane (no preapprove row)"))
            self.assertEqual(verdict(p, holds=pre), ("merge", f"green, ci-gate SUCCESS, risk {want}, preapproved at {H[:9]}"))
            self.assertEqual(verdict(dict(p, headRefOid=H2), holds=pre),
                             ("wait", "zane", f"declared {want}: needs Zane (preapproved at {H[:9]}, head is {H2[:9]})"))
            self.assertEqual(verdict(dict(p, number=5901), holds=pre)[:2], ("wait", "zane"))
            self.assertEqual(verdict(dict(p, isDraft=True))[0], "wait")   # a deep draft is never readied
        self.assertEqual(verdict(pr(labels=["risk:deep"]))[:2], ("wait", "zane"))    # the strictest of label and body wins

    def test_strictest_declared_tier(self):
        self.assertEqual(mj.declared_risk(["risk:light", "risk:deep"], ""), "deep")
        self.assertEqual(mj.declared_risk(["risk:light"], "risk: human\n"), "human")
        self.assertIsNone(mj.declared_risk([], "x\nrisk: high\n"))   # not in TIERS
        self.assertEqual(mj.declared_risk([], "risk: high\nrisk: light\n"), "light")
        self.assertIsNone(mj.declared_risk([], "the risk: deep is mid-line"))

    def test_every_reason_is_reported_gate_then_blockers(self):
        p = pr(number=5675, isDraft=True, mergeable="CONFLICTING", body="risk: deep\n",
               statusCheckRollup=[cr("validate", conclusion="SKIPPED")])
        self.assertEqual(codes(p, paused="manual"), ["ci", "hold", "risk", "draft", "conflict", "paused"])


class NextAction(unittest.TestCase):
    RED = [cr("ci-gate", conclusion="FAILURE", run="778"), cr("rust", conclusion="FAILURE", run="777")]

    def test_red_reruns_once_per_head_then_waits_on_the_session(self):
        p = pr(statusCheckRollup=self.RED)
        self.assertEqual(verdict(p), ("rerun", "red once: ci-gate, rust"))
        self.assertEqual(verdict(p, rerun_head=H), ("wait", "session", "red after rerun: ci-gate, rust"))
        self.assertEqual(verdict(p, rerun_head=H2)[0], "rerun")   # a new head resets the allowance
        for cla in (cr("cla", status="IN_PROGRESS", conclusion=None), cr("cla", conclusion="FAILURE", run="999")):
            got = verdict(pr(statusCheckRollup=self.RED + [cla]))   # cla neither holds a rerun back nor gets one
            self.assertEqual(got, ("rerun", "red once: ci-gate, rust"), cla)
            self.assertEqual(mj.check_state(self.RED + [cla])[2], {"777", "778"})
        status = pr(statusCheckRollup=[{"__typename": "StatusContext", "context": "ci-gate", "state": "FAILURE"}])
        self.assertEqual(verdict(status)[:2], ("wait", "session"))             # nothing to rerun

    def test_pending_waits(self):
        pending = pr(statusCheckRollup=[cr("ci-gate", status="IN_PROGRESS", conclusion=None), cr("x", conclusion="FAILURE")])
        self.assertEqual(verdict(pending), ("wait", "ci-gate PENDING"))
        self.assertEqual(verdict(pr(statusCheckRollup=[])), ("wait", "ci-gate missing"))

    def test_a_draft_is_never_readied_or_merged(self):
        """Draft means not ready. Only a person, or the session that opened it, readies a draft."""
        held = "draft"
        # cua#6970: draft, risk standard, "do not merge" in the body, CI not started, mergeability unknown
        p6970 = pr(number=6970, isDraft=True, mergeable="UNKNOWN", statusCheckRollup=[cr("ci-gate", status="QUEUED", conclusion=None)],
                   body="**awaits Zane's decision; do not merge.**\nrisk: standard\n")
        self.assertEqual(verdict(p6970), ("wait", f"ci-gate PENDING; {held}; mergeable UNKNOWN"))
        # cua#7050: a light draft shown as a sample ("propose this before merging"), no hold row, CI pending
        p7050 = pr(number=7050, isDraft=True, statusCheckRollup=[cr("ci-gate", status="IN_PROGRESS", conclusion=None)],
                   body="Draft: a sample. Please do not merge until the audit is reviewed.\nrisk: light\n")
        self.assertEqual(verdict(p7050), ("wait", f"ci-gate PENDING; {held}"))
        # and still not once its CI is green: nothing but the draft is left
        self.assertEqual(verdict(dict(p7050, statusCheckRollup=[cr("ci-gate")])), ("wait", held))
        # cua#7035 pattern: a draft with no hold row whose body says light; a human tier waits on nobody too, not a wake
        for tier in ("light", "standard", "human"):
            self.assertEqual(len(verdict(pr(number=7035, isDraft=True, body=f"risk: {tier}\n"))), 2, tier)
        self.assertNotIn("ready", {verdict(pr(isDraft=True, statusCheckRollup=r))[0]
                                   for r in ([], [cr("ci-gate")], self.RED, [cr("ci-gate", conclusion="SKIPPED")])})

    def test_github_mergeability_and_pause(self):
        self.assertEqual(verdict(pr(mergeable="CONFLICTING")), ("wait", "session", "merge conflict"))
        self.assertEqual(verdict(pr(mergeable="UNKNOWN")), ("wait", "mergeable UNKNOWN"))
        self.assertEqual(verdict(pr(), paused="manual"), ("wait", "zane", "tick paused: manual"))


class Holds(unittest.TestCase):
    def test_rows_parse(self):
        self.assertEqual(FIX, {"hold": {5675}, "paths": ["never.rs"], "preapproved": {}})
        self.assertEqual(mj.parse_holds("hands-off-path | x.rs | no | mode=never | b\n")["paths"], ["x.rs"])
        h = mj.parse_holds(f"preapprove | #5650 | no | {H} | Zane\npreapprove | 5690 | no | {H2} | x\n")
        self.assertEqual(h["preapproved"], {5650: H, 5690: H2})

    def test_a_row_that_names_no_pr_is_not_read_and_cannot_hold_every_pr(self):
        """8 Nov 14:17: one bad row the job had no use for made the file unreadable, and every PR counted as held."""
        for row in ("review-only | f-trycua | no | teammate", "skip-author | app/cua-agents | no | bot", "review-only | f-trycua | maybe",
                    "exception | impl_.rs | yes | #5686 follow-up"):
            self.assertEqual(mj.parse_holds(row), {"hold": set(), "paths": [], "preapproved": {}}, row)

    def test_malformed_rows_raise(self):
        for bad in ("hold 5675", "hodl | 5675 | no | x", "hold | | no | x", "hold | abc | no | x", "hold | 5675",
                    "no-move | 5675 | no | a kind this job does not know, on a PR", "review-only | | no | x", "review-only",
                    "hands-off-path | x.rs | no | mode=sometimes | x", "hands-off-path | x.rs", "hands-off-path | | no | x",
                    "preapprove | #5650 | no | abc123 | x", f"preapprove | #5650 | no | {H}", f"preapprove | x | no | {H} | x",
                    f"preapprove | #1 | no | {H} | x\npreapprove | #1 | no | {H2} | y"):
            with self.assertRaises(ValueError, msg=bad):
                mj.parse_holds(bad)

    def test_live_holds_file_parses(self):
        if not os.path.exists(mj.HOLDS_FILE):
            self.skipTest("no live holds file on this machine")
        h = mj.parse_holds(mj.read(mj.HOLDS_FILE))
        self.assertIn(5675, h["hold"])


class Stage(unittest.TestCase):
    """Job.run() over a fake GitHub (gh_json and sh mocked) on a temp ONIGIRI_ROOT. Each run is a fresh Job, as cron
    runs it, so the once-per-head allowances must survive in state.json between runs."""
    GREEN = [cr("validate"), cr("ci-gate")]
    RED = [cr("ci-gate", conclusion="FAILURE", run="778"), cr("rust", conclusion="FAILURE", run="777")]

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        d = Path(td.name)
        (d / "tick").mkdir()
        (d / "hold-prs.txt").write_text(HOLDS)
        self.drift, self.unknown, self.sleeps = None, {}, []
        p = patch.multiple(mj, ROOT=str(d), EVENTS=str(d / "events.log"), HOLDS_FILE=str(d / "hold-prs.txt"),
                           STATE=str(d / "tick/state.json"), PAUSED=str(d / "tick/PAUSED"), LAST=str(d / "tick/last-run"),
                           gh_json=self.gh, sh=self.sh, parity_problem=lambda: self.drift)
        p.start()
        self.addCleanup(p.stop)
        z = patch.object(mj.time, "sleep", self.sleeps.append)
        z.start()
        self.addCleanup(z.stop)
        k = patch.object(wake, "KEY_FILE", str(d / "no-key"))
        k.start()
        self.addCleanup(k.stop)
        self.d, self.prs, self.calls, self.merged, self.queue = d, {}, [], [], False
        self.lists, self.reads = [], []
        self.entries = {}   # PR number -> its mergeQueueEntry while GitHub's queue holds it

    def add(self, n, head=H, rollup=None, body="risk: light\n", draft=False, files=("README.md",), mergeable="MERGEABLE"):
        self.prs[n] = {"number": n, "headRefOid": head, "isDraft": draft, "title": f"pr {n}", "labels": [], "body": body,
                       "mergeable": mergeable, "state": "OPEN", "mergedAt": None, "files": [{"path": x} for x in files],
                       "statusCheckRollup": list(self.GREEN if rollup is None else rollup)}

    def node(self, n, files=None, checks=None):
        """PR n as GitHub's GraphQL returns it, 100 files and 100 checks a page (files/checks: the page's cursor, or None)."""
        p = self.prs[n]
        mergeable = p["mergeable"]
        if self.unknown.get(n):   # GitHub has not computed it yet: UNKNOWN for this many reads
            self.unknown[n] -= 1
            mergeable = "UNKNOWN"
        self.reads.append(n)

        def page(items, after):
            i = int(after or 0)
            return {"totalCount": len(items), "nodes": items[i:i + 100],
                    "pageInfo": {"hasNextPage": i + 100 < len(items), "endCursor": str(i + 100)}}
        checks_ = [{**({k: v for k, v in c.items() if k != "workflowName"}),
                    **({"checkSuite": {"workflowRun": {"workflow": {"name": c["workflowName"]}}}} if c["__typename"] == "CheckRun" else {})}
                   for c in p["statusCheckRollup"]]
        return {"number": n, "headRefOid": p["headRefOid"], "isDraft": p["isDraft"], "title": p["title"], "body": p["body"],
                "mergeable": mergeable, "labels": {"totalCount": len(p["labels"]), "nodes": [{"name": x} for x in p["labels"][:100]]},
                "files": page(p["files"], files), "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": page(checks_, checks)}}}]}}

    def gh(self, args):
        if args[:2] == ["api", "graphql"]:
            query = args[-1].split("=", 1)[1]
            kv = dict(a.split("=", 1) for a in args[2:-1] if "=" in a)
            if "search(" in query:
                return {"data": {"search": {"nodes": [self.node(n) for n, p in self.prs.items() if p["state"] == "OPEN"],
                                            "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
            n = int(kv["n"])
            if "mergeQueueEntry" in query:
                return {"data": {"repository": {"pullRequest": {"id": f"PR_{n}", "mergeQueueEntry": self.entries.get(n)}}}}
            node = self.node(n, kv.get("files"), kv.get("checks"))
            if "@include" in query:   # MORE: only the connections asked for
                node = {k: node[k] for k in (["files"] if kv["f"] == "true" else []) + (["commits"] if kv["c"] == "true" else [])}
            return {"data": {"repository": {"pullRequest": node}}}
        if args[:2] == ["pr", "list"]:
            self.lists.append(args)
            return self.merged
        if args[:2] == ["pr", "view"]:
            n = int(args[2])
            out = {k: self.prs[n][k] for k in args[-1].split(",")}
            if "mergeable" in out and self.unknown.get(n):   # GitHub has not computed it yet: UNKNOWN for this many reads
                self.unknown[n] -= 1
                out["mergeable"] = "UNKNOWN"
            return out
        raise AssertionError(f"unexpected gh {args}")

    def sh(self, args, timeout=120):
        self.calls.append(args)
        assert args[:3] in (["gh", "pr", "merge"], ["gh", "run", "rerun"], ["gh", "pr", "ready"], ["gh", "api", "graphql"]), args
        assert "--auto" not in args, args
        if args[:3] == ["gh", "pr", "merge"] and not self.queue:
            self.prs[int(args[3])].update(state="MERGED", mergedAt="2026-11-09T22:46:48Z")
        elif args[:3] == ["gh", "pr", "merge"]:
            self.entries[int(args[3])] = {"enqueuer": {"login": "injaneity"}, "headCommit": {"oid": args[-1]}}
        elif args[:3] == ["gh", "api", "graphql"]:
            assert "dequeuePullRequest" in args[-1], args
            self.entries.pop(int(args[4].split("_")[1]))
        return 0, "", ""

    def run_(self, dry=False):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mj.Job(dry).run()
        return out.getvalue()

    def events(self):
        p = self.d / "events.log"
        return [x.split(" ", 1)[1] for x in p.read_text().splitlines()] if p.exists() else []

    def woken(self):
        """The urgent wakes this job raised: their `urgent <id> <kind>: <reason>` events. The job writes nothing in its root
        but events.log and tick/ (no buffer.jsonl)."""
        self.assertEqual(sorted(os.listdir(self.d)), sorted({"hold-prs.txt", "tick"} | ({"events.log"} if self.events() else set())))
        out = []
        for x in self.events():
            if m := re.fullmatch(r"urgent (\S+) ([a-z-]+): (.*)", x):
                out.append({"id": m.group(1), "kind": m.group(2), "reason": m.group(3)})
        return out

    def of(self, cmd):
        return [c for c in self.calls if c[:3] == cmd]

    def test_green_pr_is_squash_merged_at_its_head(self):
        self.add(6301)
        self.run_()
        self.assertEqual(self.calls, [["gh", "pr", "merge", "6301", "-R", "trycua/cua", "--squash", "--match-head-commit", H]])
        self.assertEqual(self.events(), [f"merge-queued cua#6301 squash at {H[:9]}: green, ci-gate SUCCESS, risk light",
                                         f"merged cua#6301 squash at {H[:9]}, mergedAt 2026-11-09T22:46:48Z: green, ci-gate SUCCESS, risk light"])
        self.assertTrue((self.d / "tick/last-run").read_text().endswith(" ok\n"))

    def test_a_queued_merge_is_logged_merged_once_only_when_github_says_merged(self):
        """cua#7082: gh pr merge exits 0 when the merge queue only took it, and the job logged `merged` three runs running."""
        self.queue = True
        self.add(7082)
        for _ in range(3):
            self.run_()
        self.assertEqual(len(self.of(["gh", "pr", "merge"])), 3)   # asked again each run, logged once at this head
        self.assertEqual([x.split(" ", 2)[:2] for x in self.events()], [["merge-queued", "cua#7082"]])
        self.prs[7082].update(state="MERGED", mergedAt="2026-11-09T22:46:48Z")
        self.run_()
        self.run_()
        ev = [x for x in self.events() if x.startswith("merged ")]
        self.assertEqual(ev, [f"merged cua#7082 squash at {H[:9]}, mergedAt 2026-11-09T22:46:48Z: green, ci-gate SUCCESS, risk light"])
        self.assertEqual(json.loads((self.d / "tick/state.json").read_text())["queued"], {})

    def test_a_run_that_died_before_saving_state_does_not_log_merged_twice(self):
        self.add(7083)
        self.run_()
        (self.d / "tick/state.json").write_text(json.dumps({"queued": {"cua#7083": {"head": H, "why": "green"}}}))   # the lost save
        self.run_()
        self.assertEqual([x.split(" ", 2)[:2] for x in self.events()], [["merge-queued", "cua#7083"], ["merged", "cua#7083"]])

    def test_a_new_head_while_queued_is_queued_again_and_a_close_drops_it(self):
        self.queue = True
        self.add(7090)
        self.run_()
        self.add(7090, head="b" * 40)
        self.run_()
        self.run_()
        self.prs[7090]["state"] = "CLOSED"
        self.run_()
        self.run_()
        self.assertEqual([x.split(" ", 2)[:2] for x in self.events()],
                         [["merge-queued", "cua#7090"], ["merge-queued", "cua#7090"], ["merge-dropped", "cua#7090"]])
        self.assertIn("at bbbbbbbbb", self.events()[1])

    def dequeues(self):
        return [c[4] for c in self.of(["gh", "api", "graphql"])]

    def test_the_jobs_queue_entry_that_gets_a_hold_row_is_dequeued_once(self):
        """A hold row added while the job's entry waits in GitHub's merge queue takes it out (the model's no-dequeue mutant)."""
        self.queue = True
        self.add(7200)
        self.add(7201)
        self.run_()
        self.assertEqual(set(self.entries), {7200, 7201})
        (self.d / "hold-prs.txt").write_text(HOLDS + "hold | 7200 | no | Zane stops it\n")
        self.run_()
        self.run_()
        self.assertEqual(self.dequeues(), ["id=PR_7200"])   # one call, and none for the green 7201
        self.assertEqual(set(self.entries), {7201})
        self.assertIn("merge-dequeued cua#7200 at aaaaaaaaa: hold: #7200 has a hold row in the holds file", self.events())
        self.assertNotIn("cua#7200", json.loads((self.d / "tick/state.json").read_text())["queued"])
        self.assertEqual([c[3] for c in self.of(["gh", "pr", "merge"])], ["7200", "7201", "7201", "7201"])   # 7200 not asked again

    def test_paused_or_unreadable_holds_dequeue_the_jobs_entry_and_a_dry_run_only_says_so(self):
        self.queue = True
        self.add(7210)
        self.run_()
        (self.d / "tick/PAUSED").write_text("manual\n")
        out = self.run_(dry=True)
        self.assertEqual(self.dequeues(), [])
        self.assertIn("would-dequeue cua#7210", out)
        self.run_()
        self.assertEqual(self.dequeues(), ["id=PR_7210"])
        (self.d / "tick/PAUSED").unlink()
        self.run_()   # green again: queued again
        (self.d / "hold-prs.txt").write_text("hodl | 1 | no | typo\n")
        self.run_()
        self.assertEqual(self.dequeues(), ["id=PR_7210", "id=PR_7210"])

    def test_another_actors_entry_and_a_stale_head_are_left_alone(self):
        self.queue = True
        self.add(7220)
        self.add(7221)
        self.run_()
        self.entries[7220]["enqueuer"] = {"login": "cua-agents[bot]"}   # cua#7039: an app queued it too
        self.add(7221, head=H2)   # a push: GitHub drops the old entry with the old head
        self.entries[7221] = {"enqueuer": {"login": "injaneity"}, "headCommit": {"oid": H}}
        (self.d / "hold-prs.txt").write_text(HOLDS + "hold | 7220 | no | x\nhold | 7221 | no | x\n")
        self.run_()
        self.assertEqual(self.dequeues(), [])

    def test_should_dequeue_needs_the_jobs_entry_at_the_head_and_an_off_gate_reason(self):
        mine, q = {"enqueuer": {"login": "injaneity"}, "headCommit": {"oid": H}}, {"head": H}
        hold, ci = [mj.Reason("hold", "h")], [mj.Reason("ci", "c")]
        self.assertTrue(mj.should_dequeue(hold, q, mine, H))
        self.assertTrue(mj.should_dequeue(ci, q, mine, H))
        self.assertTrue(mj.should_dequeue([mj.Reason("paused", "p")], q, mine, H))
        for reasons, queued, entry in (([], q, mine), ([mj.Reason("mergeable", "m")], q, mine), (hold, q, None), (hold, None, mine),
                                       (hold, {"head": H2}, mine), (hold, q, {**mine, "enqueuer": {"login": "cua-agents[bot]"}}),
                                       (hold, q, {**mine, "headCommit": {"oid": H2}})):
            self.assertFalse(mj.should_dequeue(reasons, queued, entry, H), (reasons, queued, entry))

    def test_red_is_rerun_once_then_waits_on_the_session_and_wakes_nobody(self):
        self.add(6308, rollup=self.RED)
        self.run_()
        self.assertEqual(self.of(["gh", "run", "rerun"]), [["gh", "run", "rerun", r, "--failed", "-R", "trycua/cua"] for r in ("777", "778")])
        self.run_()
        self.run_()
        self.assertEqual(len(self.of(["gh", "run", "rerun"])), 2)
        self.assertEqual(self.woken(), [])
        self.assertEqual([x for x in self.events() if "6308" in x][-1], "wait cua#6308 who=session: red after rerun: ci-gate, rust")

    def test_holds_hands_off_risk_and_drafts(self):
        self.add(5675)
        self.add(6309, body="risk: deep\n")
        self.add(6312, draft=True)
        self.add(6306, files=("README.md", "libs/x/src/tools/never.rs"))
        self.run_()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.woken(), [])
        self.assertEqual(sorted(self.events()), ["wait cua#5675 who=zane: hold: #5675 has a hold row in the holds file",
                                                 "wait cua#6306 who=zane: touches never.rs (hands-off path, mode never; see the holds file)",
                                                 "wait cua#6309 who=zane: declared deep: needs Zane (no preapprove row)",
                                                 "wait cua#6312 draft"])
        out = self.run_(dry=True)   # what bin/hourly prints under needs-Zane
        self.assertIn(f"dry-run cua#6309 {H[:9]} wait who=zane: declared deep: needs Zane (no preapprove row)", out)
        self.assertIn(f"dry-run cua#6312 {H[:9]} wait: draft", out)

    def test_an_unknown_mergeability_is_read_again_in_the_same_run(self):
        """28 of 87 merges waited one to four ticks on a mergeability GitHub computes on the first ask."""
        self.add(6330)
        self.unknown[6330] = 2
        self.run_()
        self.assertEqual([c[3] for c in self.of(["gh", "pr", "merge"])], ["6330"])
        self.assertEqual(len(self.sleeps), 2)

    def test_the_reread_is_bounded_and_only_for_a_pr_that_would_otherwise_merge(self):
        self.add(6331)
        self.add(6332, rollup=self.RED)
        self.add(6333, mergeable="CONFLICTING")
        self.unknown.update({6331: 99, 6332: 99})
        self.run_()
        self.assertEqual(self.of(["gh", "pr", "merge"]), [])
        self.assertEqual(len(self.sleeps), mj.MERGEABLE_REREADS)   # all for 6331: 6332 is red, 6333 has an answer
        self.assertIn("wait cua#6331 mergeable UNKNOWN", self.events())

    def test_a_host_that_is_not_merged_main_wakes_a_person_once_and_keeps_merging(self):
        self.add(6340)
        self.drift = "FAIL  package: at 535b300, origin/main is e9ec151"
        with patch.object(mj, "now", lambda: "2026-11-10T05:00:00+08:00"):
            self.run_()
            self.add(6341)
            self.run_()
        self.assertEqual([(b["kind"], b["id"]) for b in self.woken()], [("parity", "host")])
        self.assertIn("535b300", self.woken()[0]["reason"])
        self.assertEqual({c[3] for c in self.of(["gh", "pr", "merge"])}, {"6340", "6341"})
        self.assertIn("error(s)", (self.d / "tick/last-run").read_text())
        self.drift = None
        self.run_()
        self.assertTrue((self.d / "tick/last-run").read_text().endswith(" ok\n"))

    def test_one_search_reads_every_pr_and_only_one_with_more_than_a_page_reads_again(self):
        """Before: one `pr list` plus one `pr view` per PR. Now: one search, and a follow-up only past 100 files or checks."""
        self.add(6350)
        self.add(6351, rollup=[cr(f"lane {i}") for i in range(230)] + [cr("ci-gate")], files=[f"f{i}.rs" for i in range(120)])
        self.add(6352, rollup=[cr(f"lane {i}") for i in range(99)] + [cr("ci-gate", conclusion="FAILURE")])   # 100: one page
        seen = []
        real = self.gh
        with patch.object(mj, "gh_json", lambda a: seen.append(a) or real(a)):
            self.run_(dry=True)
        reads = [a for a in seen if a[:2] != ["pr", "list"]]   # merge-watch's list is not the PR read
        self.assertEqual(sum("search(" in a[-1] for a in reads), 1)
        self.assertEqual([dict(x.split("=", 1) for x in a[2:-1] if "=" in x)["n"] for a in reads if "@include" in a[-1]], ["6351", "6351"])
        self.assertFalse([a for a in reads if a[:2] == ["pr", "view"]])
        self.assertEqual({c[3] for c in self.of(["gh", "pr", "merge"])}, set())   # dry
        job = mj.Job(dry=True)
        with patch.object(mj, "gh_json", real):
            p = mj.from_graphql(mj.read_rest(self.node(6351)))
        self.assertEqual((len(p["statusCheckRollup"]), len(p["files"])), (231, 120))
        self.assertEqual(job.evaluate(p, FIX)[0], [])   # green: every one of the 231 checks was read, ci-gate last

    def test_a_count_that_does_not_match_what_was_read_raises(self):
        self.add(6360)
        n = self.node(6360)
        n["files"]["totalCount"] = 2
        with self.assertRaisesRegex(RuntimeError, "read 1 of 2 files"):
            mj.from_graphql(n)

    def test_a_queued_pr_whose_gate_cannot_be_read_in_full_leaves_the_queue(self):
        self.queue = True
        self.add(6362)
        self.run_()
        self.assertEqual(set(self.entries), {6362})
        self.prs[6362]["labels"] = [f"area-{i}" for i in range(100)] + ["risk:deep"]
        self.run_()
        self.assertEqual(self.dequeues(), ["id=PR_6362"])
        self.assertIn("error cua#6362 merge: RuntimeError: cua#6362: read 1 of 1 files, 2 of 2 checks and 100 of 101 labels", self.events())

    def test_a_pr_with_more_labels_than_one_page_is_an_error_never_a_merge(self):
        """A risk:deep label past the first page must not let a `risk: light` body merge without its preapproval."""
        self.add(6361)
        self.prs[6361]["labels"] = [f"area-{i}" for i in range(100)] + ["risk:deep"]
        self.run_()
        self.assertEqual(self.of(["gh", "pr", "merge"]), [])
        self.assertIn("error cua#6361 merge: RuntimeError: cua#6361: read 1 of 1 files, 2 of 2 checks and 100 of 101 labels", self.events())

    def test_an_unchanged_decision_is_logged_once(self):
        self.add(6302, rollup=[cr("ci-gate", status="IN_PROGRESS", conclusion=None)])
        self.run_()
        self.run_()
        self.assertEqual(self.events(), ["wait cua#6302 ci-gate PENDING"])

    def test_paused_or_unreadable_holds_moves_nothing(self):
        self.add(6320)
        (self.d / "tick/PAUSED").write_text("manual pause\n")
        self.run_()
        self.assertEqual(self.calls, [])
        self.assertIn("wait cua#6320 who=zane: tick paused: manual pause", self.events())
        self.assertTrue((self.d / "tick/last-run").read_text().endswith(" ok paused\n"))
        (self.d / "tick/PAUSED").unlink()
        (self.d / "hold-prs.txt").write_text("hodl | 1 | no | typo\n")
        self.run_()
        self.assertEqual(self.calls, [])
        self.assertIn("wait cua#6320 who=zane: holds file unreadable: every PR counts as held", self.events())
        self.assertEqual([b["kind"] for b in self.woken()], ["holds-unreadable"])

    def test_dry_run_prints_every_pr_and_changes_nothing(self):
        self.add(6312, draft=True)
        self.add(6313)
        self.add(5675)
        out = self.run_(dry=True)
        self.assertEqual(self.calls, [])
        self.assertEqual(sorted(os.listdir(self.d)), ["hold-prs.txt", "tick"])
        self.assertEqual(os.listdir(self.d / "tick"), [])
        self.assertIn(f"dry-run cua#6313 {H[:9]} merge: green, ci-gate SUCCESS, risk light", out)
        self.assertIn(f"dry-run cua#5675 {H[:9]} wait who=zane: hold: #5675 has a hold row in the holds file", out)
        self.assertIn(f"dry-run cua#6312 {H[:9]} wait: draft", out)

    def test_drafts_stay_drafts_across_runs(self):
        """cua#6970, cua#7050 and cua#7035/7037/7039 were readied by one run and merged by a later one."""
        self.add(6970, draft=True, mergeable="UNKNOWN", rollup=[cr("ci-gate", status="QUEUED", conclusion=None)],
                 body="do not merge.\nrisk: standard\n")
        self.add(7050, draft=True, rollup=[cr("ci-gate", status="IN_PROGRESS", conclusion=None)])
        self.add(7035, draft=True)
        self.run_()
        for n in (6970, 7050):   # CI finishes green between runs
            self.prs[n].update(mergeable="MERGEABLE", statusCheckRollup=list(self.GREEN))
        self.prs[7035]["body"] = "risk: human\n"
        self.run_()
        self.run_()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.woken(), [])
        self.assertEqual(sorted(x.split(" ", 2)[1] for x in self.events() if x.startswith("wait")),
                         ["cua#6970", "cua#6970", "cua#7035", "cua#7035", "cua#7050", "cua#7050"])

    def test_one_prs_failure_does_not_skip_the_rest(self):
        self.add(6323, rollup=[cr(f"lane {i}") for i in range(150)] + [cr("ci-gate")])   # past one page: a second read
        self.add(6324)
        real = self.gh

        def flaky(args):
            if "@include" in args[-1] and "n=6323" in args:
                raise RuntimeError("gh api graphql: HTTP 502")
            return real(args)
        with patch.object(mj, "gh_json", flaky):
            self.run_()
        self.assertEqual([c[3] for c in self.of(["gh", "pr", "merge"])], ["6324"])
        self.assertIn("error cua#6323 merge: RuntimeError: gh api graphql: HTTP 502", self.events())
        self.assertTrue((self.d / "tick/last-run").read_text().endswith(" 1 error(s)\n"))

    def test_each_urgent_kind_posts_once_across_two_passes_and_a_closed_pr_none(self):
        posts = []
        with patch.object(mj, "post_webhook", lambda kind, ident, reason: posts.append((kind, ident)) or "webhook sent (200)"):
            for _ in range(2):
                job = mj.Job(dry=False)
                self.add(1)
                with contextlib.redirect_stdout(io.StringIO()):
                    for kind in mj.URGENT:
                        job.wake(kind, "cua#1", H, "why")
                    self.prs[1]["state"] = "CLOSED"
                    self.assertTrue(job.wake("merge-failed", "cua#1", H2, "closed meanwhile"))   # handled: nothing to send
                    with self.assertRaises(AssertionError):
                        job.wake("merge-blocked", "cua#1", H, "not a wake any more")
                job.run()
        self.assertEqual(posts, [(k, "cua#1") for k in mj.URGENT])
        self.assertFalse((self.d / "buffer.jsonl").exists())

    def test_a_wake_that_failed_or_was_paused_is_sent_on_a_later_pass_then_never_again(self):
        self.add(1, draft=True)   # stays open, so the closed-PR guard does not drop the wake
        got = iter(["webhook failed: OSError", "webhook sent (200)"])
        with patch.object(mj, "post_webhook", lambda *a: next(got)) as _, contextlib.redirect_stdout(io.StringIO()):
            (self.d / "tick/PAUSED").write_text("manual\n")
            for _ in range(4):   # paused, then failed, then sent, then nothing
                job = mj.Job(dry=False)
                job.wake("merge-failed", "cua#1", H, "why")
                job.run()
                (self.d / "tick/PAUSED").unlink(missing_ok=True)
        self.assertEqual([x for x in self.events() if x.startswith("urgent cua#1 ") and not x.startswith("urgent cua#1 merge-failed:")],
                         ["urgent cua#1 not sent: paused", "urgent cua#1 webhook failed: OSError", "urgent cua#1 webhook sent (200)"])

    def test_an_unexpected_merge_whose_wake_failed_is_sent_on_a_later_pass_even_after_the_window_moves(self):
        (self.d / "tick/state.json").write_text(json.dumps({"merges": {"since": "2026-11-05T00:00:00+08:00"}}))
        self.merged = [{"number": 10, "headRefOid": H, "mergedBy": {"login": "app/cua-agents"}, "mergedAt": "2026-11-05T01:00:00Z",
                        "title": "t", "mergeCommit": {"oid": "c" * 40}}]
        got = iter(["webhook failed: HTTP 502", "webhook sent (200)"])
        with patch.object(mj, "post_webhook", lambda *a: next(got)):
            self.run_()
            st = json.loads((self.d / "tick/state.json").read_text())   # unsent: the window stays at the merge, and it is not seen
            self.assertEqual((st["merges"]["since"], st["merges"]["seen"]), ("2026-11-05T09:00:00+08:00", {}))
            self.run_()
            self.run_()
        self.assertEqual([x for x in self.events() if x.startswith("urgent cua#10 ") and "unexpected-merge:" not in x],
                         ["urgent cua#10 webhook failed: HTTP 502", "urgent cua#10 webhook sent (200)"])
        self.assertGreater(json.loads((self.d / "tick/state.json").read_text())["merges"]["since"], "2026-11-06")   # sent: it moves on

    def test_the_old_buffers_urgent_keys_carry_over_and_the_rest_is_dropped(self):
        key = f"unexpected-merge|cua#10|{'c' * 40}"
        (self.d / "tick/state.json").write_text(json.dumps({"buffered": {key: "t", f"merge-blocked|cua#9|{H}": "t"},
                                                            "merges": {"since": "2026-11-05T00:00:00+08:00"}}))
        self.merged = [{"number": 10, "headRefOid": H, "mergedBy": {"login": "injaneity"}, "mergedAt": "2026-11-05T01:00:00Z",
                        "title": "t", "mergeCommit": {"oid": "c" * 40}}]
        self.run_()
        self.assertEqual(self.woken(), [])   # woken already under the buffer
        st = json.loads((self.d / "tick/state.json").read_text())
        self.assertNotIn("buffered", st)
        self.assertEqual(list(st["woken"]), [key])

    def test_merge_watch_seeds_then_alerts_once_for_a_merge_it_did_not_make(self):
        self.run_()                                       # first run: sets since, reads nothing
        st = json.loads((self.d / "tick/state.json").read_text())
        self.assertIn("since", st["merges"])
        st["merges"]["since"] = "2026-11-05T00:00:00+08:00"
        st["landed"] = {"11": {"head": H, "at": "2026-11-05T09:00:00+08:00"}}
        (self.d / "tick/state.json").write_text(json.dumps(st))
        m = lambda n, by, head=H: {"number": n, "headRefOid": head, "mergedBy": {"login": by}, "mergedAt": "2026-11-05T01:00:00Z",   # noqa: E731
                                   "title": f"pr {n}", "mergeCommit": {"oid": "c" * 40}}
        self.merged = [m(10, "injaneity"), m(11, "injaneity"), m(12, "zane"), m(13, "app/cua-agents"), m(14, "injaneity", head=H2)]
        self.run_()
        self.run_()
        # every merger but the job wakes: a hand merge, a teammate, an app (cua#7037), and the job's PR at a head it never queued
        self.assertEqual([(b["kind"], b["id"]) for b in self.woken()], [("unexpected-merge", f"cua#{n}") for n in (10, 12, 13, 14)])
        self.assertIn("merged by app/cua-agents", self.woken()[2]["reason"])
        self.assertIn("not by the tick", self.woken()[0]["reason"])
        ev = self.events()
        self.assertFalse(any(x.startswith("merge-noted") for x in ev))
        self.assertIn("urgent cua#10 webhook not configured", ev)
        self.assertFalse(any("cua#11 " in x for x in ev))
        self.assertIn(["pr", "list", "-R", "trycua/cua", "--state", "merged", "--author", "injaneity"], [c[:8] for c in self.lists])
        st = json.loads((self.d / "tick/state.json").read_text())
        self.assertGreater(st["merges"]["since"], "2026-11-05T00:00:00+08:00")   # the window moved up
        self.assertEqual(st["merges"]["seen"], {})                                # and forgot what fell out of it
        self.assertEqual(st["landed"], {})

    def test_a_merge_the_job_queued_is_not_unexpected_whether_or_not_it_has_logged_it_yet(self):
        """cua#7180: queued by the job at c18608a73 and merged by the queue. The job's own heads come from state.json."""
        self.queue = True
        self.add(7180)
        self.run_()                                       # queued; merge-watch seeds since
        st = json.loads((self.d / "tick/state.json").read_text())
        st["merges"]["since"] = "2026-11-05T00:00:00+08:00"
        (self.d / "tick/state.json").write_text(json.dumps(st))
        self.merged = [{"number": 7180, "headRefOid": H, "mergedBy": {"login": "injaneity"}, "mergedAt": "2026-11-05T01:00:00Z",
                        "title": "t", "mergeCommit": {"oid": "c" * 40}}]
        # GitHub merged it after landed() read it OPEN this pass: still queued in state.json, so not unexpected
        self.run_()
        self.prs[7180].update(state="MERGED", mergedAt="2026-11-05T01:00:00Z")
        self.run_()
        self.assertEqual(self.woken(), [])
        self.assertEqual([x.split(" ", 2)[:2] for x in self.events()], [["merge-queued", "cua#7180"], ["merged", "cua#7180"]])

    def test_a_head_pushed_after_the_job_queued_it_and_merged_by_someone_else_wakes(self):
        self.queue = True
        self.add(7230)
        self.run_()                                       # the job queues it at H
        st = json.loads((self.d / "tick/state.json").read_text())
        st["merges"]["since"] = "2026-11-05T00:00:00+08:00"
        (self.d / "tick/state.json").write_text(json.dumps(st))
        self.prs[7230].update(headRefOid=H2, state="MERGED", mergedAt="2026-11-05T01:00:00Z")   # a push, then a hand merge
        self.merged = [{"number": 7230, "headRefOid": H2, "mergedBy": {"login": "injaneity"}, "mergedAt": "2026-11-05T01:00:00Z",
                        "title": "t", "mergeCommit": {"oid": "c" * 40}}]
        self.run_()
        self.assertEqual([(b["kind"], b["id"]) for b in self.woken()], [("unexpected-merge", "cua#7230")])
        self.assertNotIn("7230", json.loads((self.d / "tick/state.json").read_text())["landed"])
        ev = self.events()   # `merged` means the job's own entry merged (MergedEventMeansMerged): not logged for another head
        self.assertFalse([x for x in ev if x.startswith("merged cua#7230")], ev)
        self.assertIn(f"merge-dropped cua#7230 merged at {H2[:9]}, not at the queued {H[:9]}: not the job's merge", ev)

    def test_the_first_run_seeds_the_allow_list_from_events_log(self):
        at = lambda h: (datetime.now(mj.SGT) - timedelta(hours=h)).isoformat(timespec="seconds")   # noqa: E731  inside the window
        with open(self.d / "events.log", "w") as e:
            e.write(f"{at(2)} merged cua#7180 squash at c18608a73: green, ci-gate SUCCESS, risk light\n"
                    f"{at(2)} merge-queued cua#7181 squash at d18608a73: green\n"
                    f"{at(1)} merge-noted cua#7001 merged by f-trycua\n")
        (self.d / "tick/state.json").write_text(json.dumps({"merges": {"since": "2026-11-10T00:00:00+08:00"}}))
        self.merged = [{"number": n, "headRefOid": h + "e3f46f3d2d62599d9ddef1e844810e2", "mergedBy": {"login": "injaneity"},
                        "mergedAt": "2026-11-10T04:40:00Z", "title": "t", "mergeCommit": {"oid": "c" * 40}}
                       for n, h in ((7180, "c18608a73"), (7181, "d18608a73"))]   # 7181: queued, then the run died before its state save
        self.run_()
        self.assertEqual(self.woken(), [])
        self.assertEqual(json.loads((self.d / "tick/state.json").read_text())["landed"]["7180"]["head"], "c18608a73")

    def test_a_seeded_merge_does_not_wake_while_it_is_still_in_the_window(self):
        """cua#7145: the job queued it at 12:32:07 and GitHub merged it at 12:35:44. The seed stamps the queue time, so the
        18:32 run pruned it while its merge was still inside the next window, and the 18:47 run woke on it."""
        head = "872f49fb0bc3" + "0" * 28
        (self.d / "events.log").write_text(f"2026-11-10T12:32:07+08:00 merged cua#7145 squash at {head[:9]}: green, risk deep\n")
        (self.d / "tick/state.json").write_text(json.dumps({"merges": {"since": "2026-11-10T12:00:00+08:00"}}))
        self.merged = [{"number": 7145, "headRefOid": head, "mergedBy": {"login": "injaneity"}, "mergedAt": "2026-11-10T04:35:44Z",
                        "title": "t", "mergeCommit": {"oid": "e730ff4cb65e" + "0" * 28}}]
        tick = datetime(2026, 11, 10, 12, 47, 31, tzinfo=mj.SGT)
        while tick <= datetime(2026, 11, 10, 19, 2, 31, tzinfo=mj.SGT):   # every 15-minute tick until the merge is out of the window
            with patch.object(mj.time, "time", return_value=tick.timestamp()), \
                    patch.object(mj, "now", return_value=tick.isoformat(timespec="seconds")):
                self.run_()
            self.assertEqual(self.woken(), [], tick)
            tick += timedelta(minutes=15)
        st = json.loads((self.d / "tick/state.json").read_text())
        self.assertGreater(st["merges"]["since"], "2026-11-10T12:35:44+08:00")   # the merge has left the window
        self.assertEqual(st["landed"], {})                                         # and so has its entry

    def test_the_seed_takes_the_queued_head_over_a_later_merged_line(self):
        """Queued at A, pushed to B and merged by hand, and the run died before saving: the merged line names B."""
        ev = [f"2026-11-10T12:00:00+08:00 merge-queued cua#7240 squash at {H[:9]}: green",
              f"2026-11-10T12:30:00+08:00 merged cua#7240 squash at {H2[:9]}, mergedAt x: green",
              f"2026-10-26T12:36:34+08:00 merged cua#5775 squash at {H[:9]}: green, verify PASS"]   # before #42: merged only
        self.assertEqual(mj.seed_landed(ev), {"7240": {"head": H[:9], "at": "2026-11-10T12:00:00+08:00"},
                                              "5775": {"head": H[:9], "at": "2026-10-26T12:36:34+08:00"}})
        self.assertEqual(mj.merge_audit([{"number": 7240, "headRefOid": H2, "mergedBy": {"login": "injaneity"}}],
                                        {int(k): v["head"] for k, v in mj.seed_landed(ev).items()}, {}), [(7240, "injaneity")])

    def test_merge_watch_keeps_its_window_when_a_fetch_is_cut_short(self):
        since = "2026-11-05T00:00:00+08:00"
        (self.d / "tick/state.json").write_text(json.dumps({"merges": {"since": since, "seen": {}}}))
        self.merged = [{"number": n, "headRefOid": H, "mergedBy": {"login": "zane"}, "mergedAt": "2026-11-05T01:00:00Z", "title": "t",
                        "mergeCommit": {"oid": "c" * 40}} for n in range(3)]
        with patch.object(mj, "WATCH_LIMIT", 3):
            self.run_()
        st = json.loads((self.d / "tick/state.json").read_text())
        self.assertEqual(st["merges"]["since"], since)
        self.assertEqual(sorted(st["merges"]["seen"]), ["0", "1", "2"])
        with patch.object(mj, "WATCH_LIMIT", 3):
            self.run_()
        self.assertEqual([b["kind"] for b in self.woken()], ["unexpected-merge"] * 3 + ["merge-watch-overflow"])
        self.assertIn("the fetch limit: check merges of PRs by injaneity by hand", self.woken()[-1]["reason"])
        self.assertTrue((self.d / "tick/last-run").read_text().endswith(" 1 error(s)\n"))

    def test_paused_logs_an_unexpected_merge_but_does_not_post(self):
        (self.d / "tick/state.json").write_text(json.dumps({"merges": {"since": "2026-11-05T00:00:00+08:00"}}))
        (self.d / "tick/PAUSED").write_text("manual\n")
        self.merged = [{"number": 10, "headRefOid": H, "mergedBy": {"login": "injaneity"}, "mergedAt": "2026-11-05T01:00:00Z", "title": "t",
                        "mergeCommit": {"oid": "c" * 40}}]
        with patch.object(mj, "post_webhook", Mock(side_effect=AssertionError("no POST while paused"))):
            self.run_()
        self.assertEqual([b["kind"] for b in self.woken()], ["unexpected-merge"])
        self.assertIn("urgent cua#10 not sent: paused", self.events())


class RunOnMain(unittest.TestCase):
    """Cron runs the checkout as it is, so a bare run moves it to origin/main first and restarts on the new code."""

    def test_a_bare_run_updates_the_package_then_restarts_once(self):
        with patch.object(mj.subprocess, "run", return_value=Mock(returncode=0)) as run, patch.object(mj.os, "execve") as ex, \
                patch.dict(os.environ, clear=False) as env:
            env.pop("ZSTACK_ENSURED", None)
            mj.run_on_main()
            self.assertTrue(run.call_args[0][0][0].endswith("/bin/ensure-package-main"))
            self.assertEqual(ex.call_args[0][1][1:], [str(VM / "merge_job.py")])
            self.assertEqual(ex.call_args[0][2]["ZSTACK_ENSURED"], "1")
            run.reset_mock(), ex.reset_mock()
            env["ZSTACK_ENSURED"] = "1"   # the restarted run: no second update, and the mark does not reach its children
            mj.run_on_main()
            run.assert_not_called(), ex.assert_not_called()
            self.assertNotIn("ZSTACK_ENSURED", os.environ)

    def test_a_checkout_that_cannot_move_still_runs(self):
        with patch.object(mj.subprocess, "run", return_value=Mock(returncode=1)), patch.object(mj.os, "execve") as ex, \
                patch.dict(os.environ, clear=False) as env:
            env.pop("ZSTACK_ENSURED", None)
            mj.run_on_main()
            ex.assert_not_called()


class CronEnv(unittest.TestCase):
    def test_gh_is_found_under_crons_bare_path(self):
        """cron runs the job with PATH=/usr/bin:/bin; gh is usually in /usr/local/bin."""
        r = subprocess.run([sys.executable, "-I", "-c", "import importlib.util as u, sys; s = u.spec_from_file_location('m', sys.argv[1]); "
                            "m = u.module_from_spec(s); s.loader.exec_module(m); print(m.ENV['PATH'])", str(VM / "merge_job.py")],
                           env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")}, capture_output=True, text=True, check=True)
        self.assertTrue({"/usr/local/bin", "/usr/bin", "/bin"} <= set(r.stdout.strip().split(os.pathsep)), r.stdout)


class Webhook(unittest.TestCase):
    def test_posts_with_the_key_and_never_echoes_it(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "k").write_text("sekrit\n")
            seen = {}

            class R:
                status = 200
                __enter__ = lambda self: self   # noqa: E731
                __exit__ = lambda self, *a: None   # noqa: E731

            def urlopen(req, timeout):
                seen.setdefault("calls", 0)
                seen["calls"] += 1
                seen["auth"], seen["body"], seen["timeout"] = req.get_header("Authorization"), json.loads(req.data), timeout
                if seen.get("down"):
                    raise OSError("down")
                return R()
            with patch.object(wake, "KEY_FILE", f"{d}/k"), patch.object(wake.urllib.request, "urlopen", urlopen):
                got = mj.post_webhook("unexpected-merge", "cua#10", "why")
                self.assertEqual(got, "webhook sent (200)")
                seen.update(calls=0, down=True)
                self.assertEqual(mj.post_webhook("unexpected-merge", "cua#10", "why"), "webhook failed: OSError")
            self.assertEqual((seen["calls"], seen["timeout"]), (1, 8))   # one try per urgent item, as before the shared sender
            self.assertEqual(seen["auth"], "Bearer sekrit")
            self.assertEqual((seen["body"]["kind"], seen["body"]["pr"]), ("unexpected-merge", 10))
            with patch.object(wake, "KEY_FILE", f"{d}/none"):
                self.assertEqual(mj.post_webhook("x", None, "y"), "webhook not configured")


class Cli(unittest.TestCase):
    def main(self, *argv):
        out, err, code = io.StringIO(), io.StringIO(), 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                mj.main(list(argv))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_unknown_args_exit_2_without_a_run(self):
        with patch.object(mj, "Job") as J, patch.object(mj, "post_webhook") as hook:
            for argv in (["--help"], ["-h"], ["--bogus"], ["--dry"], ["--disk-guard"], ["--explain", "1", "--webhook-test"],
                         ["--dry-run", "--webhook-test"], ["--dry-run", "--explain", "1"]):
                code, _, err = self.main(*argv)
                self.assertEqual(code, 2, argv)
                self.assertIn("usage:", err)
            J.assert_not_called()
            hook.assert_not_called()

    def test_modes(self):
        with patch.object(mj, "Job") as J, patch.object(mj, "post_webhook", return_value="sent") as hook:
            self.main()
            J.assert_called_once_with(dry=False)
            J.reset_mock()
            self.main("--dry-run")
            J.assert_called_once_with(dry=True)
            J.reset_mock()
            J.return_value.explain.return_value = False
            self.assertEqual(self.main("--explain", "7")[0], 1)
            J.assert_called_once_with(dry=True)
            J.return_value.explain.assert_called_once_with(7)
            J.reset_mock()
            self.main("--webhook-test")
            J.assert_not_called()
            self.assertEqual(hook.call_args[0][:2], ("test", None))

    def test_explain_records_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "h").write_text(HOLDS)
            p = pr(number=6400, body="risk: deep\n", mergeable="CONFLICTING", state="OPEN", author={"login": "injaneity"}, title="t")
            node = {**{k: p[k] for k in ("number", "headRefOid", "isDraft", "title", "body", "mergeable")},
                    "labels": {"totalCount": 0, "nodes": []},
                    "files": {"totalCount": 1, "nodes": [{"path": "README.md"}], "pageInfo": {"hasNextPage": False}},
                    "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"totalCount": 0, "nodes": [],
                                                                                          "pageInfo": {"hasNextPage": False}}}}}]}}
            gh = lambda args: {"data": {"repository": {"pullRequest": node}}} if args[:2] == ["api", "graphql"] else \
                {k: p[k] for k in args[-1].split(",")}   # noqa: E731
            with patch.multiple(mj, STATE=f"{d}/s", EVENTS=f"{d}/e", PAUSED=f"{d}/P", HOLDS_FILE=f"{d}/h",
                                gh_json=gh, sh=Mock(side_effect=AssertionError("no sh"))), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertFalse(mj.Job(dry=True).explain(6400))
            self.assertEqual(sorted(os.listdir(d)), ["h"])
        text = out.getvalue()
        self.assertIn("gate: fail", text)
        self.assertIn("gate [risk] declared deep", text)
        self.assertIn("blocker [conflict] merge conflict", text)
        self.assertIn("next action: wait who=zane: ", text)


class PatentPush(unittest.TestCase):
    """patent_push.py, cua's pre-push hook: pairing, Teleport and Keyvault never go to public trycua/cua."""

    def setUp(self):   # a held push wakes Zane: every hook these tests run prints its wake instead of POSTing it
        p = patch.dict(os.environ, {"ONIGIRI_WAKE_DRY": "1"})
        p.start()
        self.addCleanup(p.stop)

    def test_held_hits(self):
        self.assertEqual(pp.held_hits(["libs/cua-spacesd/src/pairing.rs"], ""), ["libs/cua-spacesd/src/pairing.rs"])
        self.assertTrue(pp.held_hits(["a.rs"], "+++ b/a.rs\n+let v = KeyVault::open();\n"))
        self.assertTrue(pp.held_hits(["a.rs"], "-// teleport the session\n"))
        self.assertEqual(pp.held_hits(["libs/cua-relay/src/lib.rs"], "+relay.unregister()\n"), [])
        self.assertEqual(pp.held_hits(["src/fix.rs"], "diff --git a/src/fix.rs b/src/fix.rs\n+// repairing the cache\n"), [])
        lock = "libs/cua-spacesd/tests/teleport-e2e/Cargo.lock"
        self.assertEqual(pp.held_hits([lock], f"diff --git a/{lock} b/{lock}\n+name = \"teleport-e2e\"\n"), [])

    def test_choose_remote(self):
        pub, stg = "https://github.com/trycua/cua", "https://github.com/trycua/cua-staging.git"
        self.assertEqual(pp.choose_remote(pub, [], lambda: False), "push")
        self.assertEqual(pp.choose_remote(pub, ["src/pairing.rs"], lambda: False), "hold")
        self.assertEqual(pp.choose_remote(pub, ["src/pairing.rs"], lambda: True), "staging")
        self.assertEqual(pp.choose_remote(stg, ["src/pairing.rs"], lambda: False), "push")

    def repo(self, tmp):
        def g(*a, cwd=None):
            return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout
        pub, work, root = f"{tmp}/public.git", f"{tmp}/work", f"{tmp}/root"
        os.makedirs(root)
        g("init", "-q", "--bare", pub)
        g("init", "-q", "-b", "main", work)
        for k, v in (("user.name", "t"), ("user.email", "t@t"), ("commit.gpgsign", "false")):
            g("config", k, v, cwd=work)
        Path(work, "README").write_text("x\n")
        g("add", ".", cwd=work)
        g("commit", "-qm", "base", cwd=work)
        g("remote", "add", "origin", pub, cwd=work)
        g("push", "-q", "origin", "main", cwd=work)
        g("fetch", "-q", "origin", cwd=work)
        hook = Path(work, ".git/hooks/pre-push")   # the real hook code, with staging access mocked off
        hook.write_text(f"#!{sys.executable}\nimport importlib.util, sys\n"
                        f"s = importlib.util.spec_from_file_location('pp', {str(VM / 'patent_push.py')!r})\n"
                        "pp = importlib.util.module_from_spec(s); s.loader.exec_module(pp)\n"
                        f"sys.exit(pp.main(sys.argv, sys.stdin, staging_ok=lambda: False, root={root!r}))\n")
        hook.chmod(0o755)
        return g, pub, work, root

    def push(self, tmp, branch, name, text):
        g, pub, work, root = self.repo(tmp)
        g("checkout", "-qb", branch, cwd=work)
        Path(work, name).parent.mkdir(parents=True, exist_ok=True)
        Path(work, name).write_text(text)
        g("add", ".", cwd=work)
        g("commit", "-qm", "x", cwd=work)
        r = subprocess.run(["git", "push", "origin", branch], cwd=work, capture_output=True, text=True)
        return r, g("ls-remote", pub, f"refs/heads/{branch}"), root

    def test_a_pairing_diff_is_not_pushed_publicly_and_wakes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, remote, root = self.push(tmp, "onigiri/x", "src/pairing.rs", "fn pair() {}\n")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("patent hold", r.stderr)
            self.assertEqual(remote, "")
            self.assertEqual([x.split(" ", 3)[1:3] for x in mj.read(f"{root}/events.log").splitlines()],
                             [["patent-held", "cua:onigiri/x"]])
            self.assertIn('"kind": "blocked"', r.stdout + r.stderr)
            self.assertFalse(os.path.exists(f"{root}/buffer.jsonl"))
            g = subprocess.run(["git", "push", "origin", "onigiri/x"], cwd=f"{tmp}/work", capture_output=True, text=True)
            self.assertNotEqual(g.returncode, 0)   # the same head again: still held, and no second wake
            self.assertNotIn('"kind": "blocked"', g.stdout + g.stderr)
            self.assertEqual(len(mj.read(f"{root}/events.log").splitlines()), 1)

    def test_a_patent_wake_that_failed_is_sent_again_on_the_next_push(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(pp.wake, "post", side_effect=["webhook failed: OSError", "webhook sent (200)"]) as post:
                self.assertFalse(pp.wake_hold("onigiri/z", H, ["src/pairing.rs"], root))
                self.assertTrue(pp.wake_hold("onigiri/z", H, ["src/pairing.rs"], root))
                self.assertFalse(pp.wake_hold("onigiri/z", H, ["src/pairing.rs"], root))   # sent: never again for this head
            self.assertEqual(post.call_count, 2)
            self.assertEqual([x.split(" ", 7)[6] for x in mj.read(f"{root}/events.log").splitlines()], ["failed:", "sent"])

    def test_the_installed_hook_is_a_symlink_and_logs_beside_the_clone(self):
        """Both hosts install hosts/vm/pre-push as a symlink. With no $ONIGIRI_ROOT (a push from any shell) the hold is
        logged in the directory the clone is in, also for a push from one of its worktrees."""
        with tempfile.TemporaryDirectory() as tmp:
            g, pub, work, _ = self.repo(tmp)
            hook = Path(work, ".git/hooks/pre-push")
            hook.unlink()
            hook.symlink_to(VM / "pre-push")
            Path(tmp, "bin").mkdir()
            Path(tmp, "bin/gh").write_text("#!/bin/sh\nexit 1\n")   # no staging access, and no network
            Path(tmp, "bin/gh").chmod(0o755)
            env = {"PATH": f"{tmp}/bin:/usr/bin:/bin", "HOME": tmp, "GH_CONFIG_DIR": f"{tmp}/gh", "ONIGIRI_WAKE_DRY": "1"}
            g("worktree", "add", "-qb", "onigiri/w", f"{tmp}/wt", cwd=work)
            Path(tmp, "wt/pairing.rs").write_text("fn pair() {}\n")
            g("add", ".", cwd=f"{tmp}/wt")
            g("commit", "-qm", "x", cwd=f"{tmp}/wt")
            r = subprocess.run(["git", "push", "origin", "onigiri/w"], cwd=f"{tmp}/wt", capture_output=True, text=True, env=env)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("patent hold", r.stderr)
            self.assertEqual(g("ls-remote", pub, "refs/heads/onigiri/w"), "")
            self.assertEqual([x.split(" ", 3)[1:3] for x in mj.read(f"{tmp}/events.log").splitlines()], [["patent-held", "cua:onigiri/w"]])
            g("checkout", "-qb", "onigiri/ok", cwd=work)
            Path(work, "relay.rs").write_text("fn relay() {}\n")
            g("add", ".", cwd=work)
            g("commit", "-qm", "y", cwd=work)
            r = subprocess.run(["git", "push", "origin", "onigiri/ok"], cwd=work, capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)

    def commits(self, tmp, branch, steps):
        """Commit each step (a dict of path -> text, None deletes, ("mv", a, b) renames), then push branch."""
        g, pub, work, root = self.repo(tmp)
        g("checkout", "-qb", branch, cwd=work)
        for i, step in enumerate(steps):
            if isinstance(step, tuple):
                g("mv", step[1], step[2], cwd=work)
            else:
                for name, text in step.items():
                    if text is None:
                        g("rm", "-q", name, cwd=work)
                        continue
                    Path(work, name).parent.mkdir(parents=True, exist_ok=True)
                    Path(work, name).write_text(text)
                    g("add", name, cwd=work)
            g("commit", "-qm", f"step {i}", cwd=work)
        r = subprocess.run(["git", "push", "origin", branch], cwd=work, capture_output=True, text=True)
        return r, g("ls-remote", pub, f"refs/heads/{branch}")

    def test_a_held_commit_reverted_later_in_the_push_is_still_held(self):
        with tempfile.TemporaryDirectory() as tmp:   # net diff is empty; the first commit still publishes the line
            r, remote = self.commits(tmp, "onigiri/rev", [{"a.rs": "// keyvault probe\n"}, {"a.rs": None}])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("patent hold", r.stderr)
            self.assertEqual(remote, "")

    def test_a_held_file_renamed_away_is_held(self):
        with tempfile.TemporaryDirectory() as tmp:   # net diff adds only src/link.rs; history has pairing.rs
            r, remote = self.commits(tmp, "onigiri/mv", [{"src/pairing.rs": "fn x() {}\n"}, ("mv", "src/pairing.rs", "src/link.rs")])
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(remote, "")

    def test_rename_source_and_target_paths_both_count(self):
        ns = "R100\tsrc/pairing.rs\tsrc/link.rs\nM\tREADME\n"
        self.assertEqual(pp.status_paths(ns), ["src/pairing.rs", "src/link.rs", "README"])
        self.assertEqual(pp.held_hits(pp.status_paths(ns), ""), ["src/pairing.rs"])
        self.assertEqual(pp.held_hits(pp.status_paths("R090\tsrc/a.rs\tsrc/teleport.rs\n"), ""), ["src/teleport.rs"])

    def test_a_combined_diff_counts_only_the_merges_own_lines(self):
        cc = ("diff --cc a.rs\nindex 1,2..3\n--- a/a.rs\n+++ b/a.rs\n@@@ -1,1 -1,1 +1,2 @@@\n"
              " +// teleport from main\n++// resolved\n")
        self.assertEqual(pp.held_hits([], cc), [])   # column 2 only: the line came from a parent, not the merge
        self.assertTrue(pp.held_hits([], cc.replace("++// resolved", "++// keyvault in the resolution")))
        self.assertTrue(pp.held_hits([], "diff --git a/x b/x\n@@ -1 +0,0 @@\n--- teleport removed\n"))

    def test_merging_main_that_already_has_held_work_is_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:   # main's own pairing.rs arrives by merge; the branch adds none
            g, pub, work, root = self.repo(tmp)
            g("checkout", "-qb", "onigiri/m", cwd=work)
            Path(work, "mine.rs").write_text("fn mine() {}\n")
            g("add", ".", cwd=work)
            g("commit", "-qm", "mine", cwd=work)
            up = f"{tmp}/upstream"   # someone else's clone, without the hook, lands held work on main
            g("clone", "-q", "-b", "main", pub, up)
            for k, v in (("user.name", "u"), ("user.email", "u@u"), ("commit.gpgsign", "false")):
                g("config", k, v, cwd=up)
            Path(up, "src").mkdir()
            Path(up, "src/pairing.rs").write_text("fn pair() {}\n")
            g("add", ".", cwd=up)
            g("commit", "-qm", "upstream", cwd=up)
            g("push", "-q", "origin", "HEAD:main", cwd=up)
            g("fetch", "-q", "origin", cwd=work)
            g("checkout", "-q", "onigiri/m", cwd=work)
            g("merge", "-q", "--no-edit", "origin/main", cwd=work)
            r = subprocess.run(["git", "push", "origin", "onigiri/m"], cwd=work, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_branch_whose_history_is_clean_is_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, remote = self.commits(tmp, "onigiri/ok", [{"a.rs": "fn a() {}\n"}, ("mv", "a.rs", "b.rs")])
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotEqual(remote, "")

    def test_a_relay_diff_is_pushed(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, remote, root = self.push(tmp, "onigiri/y", "relay.rs", "fn relay_unregister() {}\n")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotEqual(remote, "")
            self.assertFalse(os.path.exists(f"{root}/events.log"))


if __name__ == "__main__":
    unittest.main()
