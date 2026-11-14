import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
FAKE_PI = "#!/bin/bash\n[ \"$1\" = --list-models ] && echo claude-code m && exit 0\nwhile [ -e \"$T/hang\" ]; do sleep 0.05; done\n"
FAKE_GH = """#!/bin/bash
[ -e "$T/gh-sleep" ] && sleep 30
case "$*" in
  "issue list"*) cat "$T/issues.json";;
  "issue view "*) jq -n --arg n "$3" '{state: (if ($n | tonumber) % 2 == 0 then "OPEN" else "CLOSED" end)}';;
  *review-requested*) cat "$T/reviews.json";;
  "pr list"*in:body*) cat "$T/salvages.json" 2>/dev/null || echo '[]';;
  "pr view "*closingIssuesReferences*) jq -e --argjson n "$3" '.[] | select(.number == $n)' "$T/sources.json" 2>/dev/null \
    || { echo "no pull request $3" >&2; exit 1; };;
  "pr list"*isDraft*) cat "$T/open.json";;
  "pr view"*) echo '{"headRefOid": "'"$(printf 'f%.0s' $(seq 40))"'", "headRefName": "x"}';;
  *) echo "fake gh: $*" >&2; exit 1;;
esac
"""
FAKE_LUME = """#!/bin/bash
echo "$*" >> "$T/lume.log"
case "$1" in
  ls) [ -e "$T/lume-ls-fails" ] && { echo "lume: daemon down" >&2; exit 1; }; cat "$T/lume.json";;
  get) [ -e "$T/booted-$2" ] && st=running || st=stopped
       jq -e --arg n "$2" --arg st "$st" '[.[] | select(.name == $n) | .status = $st] | select(length > 0)' "$T/lume.json";;
  delete) jq --arg n "$2" '[.[] | select(.name != $n)]' "$T/lume.json" > "$T/lume.tmp" && mv "$T/lume.tmp" "$T/lume.json";;
  *) echo "fake lume: $*" >&2; exit 1;;
esac
"""
LATE_SCAN = """import importlib.machinery, importlib.util, json, os, sys
loader = importlib.machinery.SourceFileLoader("pickup", sys.argv[1])
m = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(m)
scan, d, rid = m.runs_naming, sys.argv[2], sys.argv[3]
def late(ids, since):
    got = scan(ids, since)
    if not os.path.isdir(d):
        os.makedirs(d)
        open(os.path.join(d, "run.json"), "w").write(json.dumps({"state": "running", "attempt": 1, "rc": None, "pid": None, "pool": "hand"}))
        open(os.path.join(d, "notes.txt"), "w").write("run=" + rid + "\\n")
    return got
m.runs_naming = late
print(m.prune_lume(False, m.read_state()))
"""
FAKE_WAKE = """#!/usr/bin/env python3
import json, os, sys
open(os.path.join(os.environ["T"], "wakes"), "a").write(json.dumps(sys.argv[1:]) + "\\n")
fail = os.path.exists(os.path.join(os.environ["T"], "wake-fails"))
print("zstack x\\nwake: " + sys.argv[1] + (" webhook failed: HTTP 500" if fail else " webhook sent (200)"), file=sys.stderr)
"""


def issue(n, created, labels=(), linked=(), login="someone"):
    return {"number": n, "title": f"issue {n}", "url": f"https://github.com/trycua/cua/issues/{n}", "createdAt": created,
            "labels": [{"name": x} for x in labels], "author": {"login": login},
            "closedByPullRequestsReferences": [{"number": x} for x in linked]}


def review(n, created, login="CMGS", bot=False):
    return {"number": n, "title": f"pr {n}", "url": f"https://github.com/trycua/cua/pull/{n}", "createdAt": created,
            "labels": [], "headRefOid": f"{n:x}".ljust(40, "a"), "author": {"login": login, "is_bot": bot}}


def live():
    ps = subprocess.run(["ps", "-o", "lstart=", "-p", str(os.getpid())], capture_output=True, text=True,
                        env={"LC_ALL": "C", "TZ": "UTC", "PATH": "/bin:/usr/bin"})
    return {"pid": os.getpid(), "start": ps.stdout.strip()}


class Pickup(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.t = t = Path(td.name)
        (t / "bin").mkdir()
        for name, text in (("pi", FAKE_PI), ("gh", FAKE_GH), ("wake", FAKE_WAKE)):
            (t / "bin" / name).write_text(text)
            (t / "bin" / name).chmod(0o755)
        self.github(issues=[], reviews=[], open_=[])
        self.pkg = t / "pkg"
        for sub in ("bin", "hosts", "config", "vendor", "skills/onigiri-mode"):
            shutil.copytree(PKG / sub, self.pkg / sub, ignore=shutil.ignore_patterns("playbooks"))
        (self.pkg / "hosts" / "check-parity").write_text("#!/bin/bash\necho ok\n")
        self.bin = self.pkg / "bin"
        self.root = t / "root"
        (self.root / "review").mkdir(parents=True)
        self.env = {"PATH": f"{t}/bin:/usr/bin:/bin", "HOME": str(t), "T": str(t), "ZSTACK_ENSURED": "1",
                    "ONIGIRI_ROOT": str(self.root), "ONIGIRI_WAKE": str(t / "bin" / "wake"), "PI_CODING_AGENT_DIR": str(t / "agent")}
        self.addCleanup(self.reap)

    def reap(self):
        (self.t / "hang").unlink(missing_ok=True)
        for p in self.root.glob("review/*/run.json"):
            pid = json.loads(p.read_text())["pid"]
            for _ in range(100 if pid else 0):
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.05)

    def github(self, issues=None, reviews=None, open_=None):
        for name, v in (("issues", issues), ("reviews", reviews), ("open", open_)):
            if v is not None:
                (self.t / f"{name}.json").write_text(json.dumps(v))

    def peer(self, waits=(), holds="", runs=None, ids=(), at_main=False):
        last = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} zstack {'a' * 40} ok"
        facts = {"sha": "s", "at_main": at_main, "parity": [], "free_gb": 25, "runs": runs or {}, "ids": list(ids),
                 "pushed": {}, "merge_last": last, "holds": holds}
        p = self.t / "vm"
        p.write_text("#!/bin/bash\ncase \"$1\" in *merge_job.py*) printf '%s\\n' " + " ".join(f"'dry-run {w}'" for w in waits)
                     + " ;;\n*) cat " + str(self.t / "vm-facts.json") + ";; esac\n")
        p.chmod(0o755)
        (self.t / "vm-facts.json").write_text(json.dumps(facts))
        self.env.update(ONIGIRI_PEERS=f"vm={p}", ONIGIRI_MERGE_HOST="vm")

    def pickup(self, *argv, rc=0, **env):
        r = subprocess.run([str(self.bin / "pickup"), *argv], capture_output=True, text=True, env={**self.env, **env}, timeout=120)
        self.assertEqual(r.returncode, rc, r.stdout + r.stderr)
        return r.stdout

    def wakes(self):
        p = self.t / "wakes"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def rows(self, action=None):
        p = self.root / "pickup.log"
        rows = [x.split("\t") for x in p.read_text().splitlines() if not x.startswith("#")] if p.exists() else []
        return [r[2:5] for r in rows if action is None or r[2] == action]

    def state(self, sid):
        return json.loads((self.root / "review" / sid / "run.json").read_text())["state"]

    def test_a_pass_with_nothing_to_do_never_wakes_and_logs_its_pass(self):
        self.assertIn("wake: none, nothing new", self.pickup())
        self.assertIn("wake: none, nothing new", self.pickup())
        self.assertEqual(self.wakes(), [])
        self.assertEqual([r[0] for r in self.rows()], ["pass", "pass-end", "pass", "pass-end"])

    def test_a_held_lock_is_a_pass_that_does_nothing(self):
        (self.root / "pickup").mkdir()
        fd = os.open(self.root / "pickup" / "pickup.lock", os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.github(issues=[issue(10, "2026-10-01T00:00:00Z")])
        self.assertIn("another pass holds the lock", self.pickup())
        os.close(fd)
        self.assertEqual(self.rows(), [["pass-locked", "-", "another pickup pass holds the lock; this one did nothing"]])
        self.assertFalse((self.root / "review" / "cua-10").exists())
        self.assertEqual(self.wakes(), [])

    def test_picks_oldest_first_urgent_ahead_up_to_the_auto_pool_and_never_twice(self):
        (self.t / "hang").touch()
        self.github(issues=[issue(n, f"2026-10-{n:02d}T00:00:00Z", labels=["urgent"] if n == 9 else []) for n in range(1, 7)]
                    + [issue(9, "2026-10-09T00:00:00Z", labels=["urgent"])])
        out = self.pickup()
        launched = [r[1] for r in self.rows("launch")]
        self.assertEqual(launched, ["cua-9", "cua-1", "cua-2"])
        self.assertIn("7 candidates (0 taken before), auto free 3", out)
        brief = (self.root / "review" / "cua-9" / "brief.md").read_text()
        for line in ("source: auto", "https://github.com/trycua/cua/issues/9", "wake: finish"):
            self.assertIn(line, brief)
        self.assertTrue(all(self.state(s) == "running" for s in launched))
        self.assertEqual(self.wakes(), [])
        out = self.pickup()
        self.assertIn("4 candidates (3 taken before), auto free 0", out)
        self.assertEqual(len(self.rows("launch")), 3)
        (self.t / "hang").unlink()
        for s in launched:
            self.assertTrue(self.wait(s, "exited"), s)
        self.pickup()
        self.assertEqual([r[1] for r in self.rows("launch")][3:], ["cua-3", "cua-4", "cua-5"])

    def test_a_brief_no_pickup_wrote_takes_its_id_and_a_died_pass_brief_launches_as_written(self):
        (self.t / "hang").touch()
        for sid, text in (("cua-4", "Task: Unstick trycua/cua#6561 at head fc0b2895. Linear CUA-4.\n"),
                          ("cua-5", "# cua-5 (coding)\nrole: coding\nsource: auto\ntrycua/cua#5: issue 5\n")):
            (self.root / "review" / sid).mkdir()
            (self.root / "review" / sid / "brief.md").write_text(text)
        self.github(issues=[issue(4, "2026-10-04T00:00:00Z"), issue(5, "2026-10-05T00:00:00Z")])
        self.assertIn("1 candidates (1 taken before)", self.pickup())
        self.assertEqual([r[1] for r in self.rows("launch")], ["cua-5"])
        self.assertIn("#6561", (self.root / "review" / "cua-4" / "brief.md").read_text())
        self.assertFalse((self.root / "review" / "cua-4" / "run.json").exists())

    def lume(self, workers, seed="seed-vm"):
        (self.t / "bin" / "lume").write_text(FAKE_LUME)
        (self.t / "bin" / "lume").chmod(0o755)
        home = self.t / ".local" / "share" / "cua-lume-e2e"
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.json").write_text(json.dumps({"seed": seed}))
        vms = [{"name": n, "status": st, "diskSize": {"allocated": 20 * 2**30}} for n, st in workers]
        (self.t / "lume.json").write_text(json.dumps(vms))

    def vms(self):
        return sorted(v["name"] for v in json.loads((self.t / "lume.json").read_text()))

    def run_dir(self, sid, state, names=(), where="notes.txt"):
        d = self.root / "review" / sid
        (d / os.path.dirname(where)).mkdir(parents=True, exist_ok=True)
        run = {"state": state, "attempt": 1, "rc": 0 if state != "running" else None, "pool": "hand", "pid": None}
        (d / "run.json").write_text(json.dumps({**run, **(live() if state == "running" else {})}))
        (d / where).write_text("".join(f"run={n}\n" for n in names))

    def wait(self, sid, state, secs=10):
        end = time.time() + secs
        while time.time() < end:
            if self.state(sid) == state:
                return True
            time.sleep(0.05)
        return False

    def test_each_of_the_five_rules_skips_with_its_own_row_and_nothing_else_skips(self):
        (self.t / "hang").touch()
        holds = "hold | 302 | no | note\nskip-author | app/cua-agents | no | bots\nreview-only | f-trycua | no | teammate\n"
        self.peer(waits=["cua#303 3030000aa wait who=zane: declared deep: needs Zane (no preapprove row)"], holds=holds,
                  runs={"busy": {"state": "running", "pool": "hand", "nums": [104]}})
        self.github(issues=[issue(101, "2026-10-01T00:00:00Z", labels=["hold"]),
                            issue(102, "2026-10-02T00:00:00Z", linked=[900]),
                            issue(103, "2026-10-03T00:00:00Z", linked=[901]),
                            issue(104, "2026-10-04T00:00:00Z"),
                            issue(105, "2026-10-05T00:00:00Z", labels=["Linux"])],
                    reviews=[review(301, "2026-10-06T00:00:00Z", login="cua-bot[bot]", bot=True),
                             review(302, "2026-10-07T00:00:00Z"),
                             review(303, "2026-10-08T00:00:00Z"),
                             review(304, "2026-10-09T00:00:00Z", login="f-trycua")],
                    open_=[{"number": 900, "isDraft": False}])
        self.pickup()
        self.assertEqual(self.rows("skip"), [
            ["skip", "cua-101", "held; https://github.com/trycua/cua/issues/101"],
            ["skip", "cua-102", "pr; https://github.com/trycua/cua/issues/102"],
            ["skip", "cua-104", "overlap; https://github.com/trycua/cua/issues/104"],
            ["skip", "cua-105", "host; https://github.com/trycua/cua/issues/105"],
            ["skip", "cua-301-review-12daaaa", "held; https://github.com/trycua/cua/pull/301"],
            ["skip", "cua-302-review-12eaaaa", "held; https://github.com/trycua/cua/pull/302"],
            ["skip", "cua-303-review-12faaaa", "zane; https://github.com/trycua/cua/pull/303"]])
        self.assertEqual([r[1] for r in self.rows("launch")], ["cua-103", "cua-304-review-130aaaa"])
        brief = (self.root / "review" / "cua-304-review-130aaaa" / "brief.md").read_text()
        self.assertIn("a teammate (review-only: a first review and nothing else)", brief)
        self.assertIn("`drafts:`", brief)

    def test_salvage_links_the_pass_lists_for_the_chat(self):
        self.peer(holds="hold | 604 | no | note\n")
        ref = lambda *ns: [{"number": n} for n in ns]   # noqa: E731
        (self.t / "salvages.json").write_text(json.dumps([
            {"number": 701, "state": "OPEN", "mergedAt": None, "body": "Salvaged from #601\n", "closingIssuesReferences": []},
            {"number": 702, "state": "OPEN", "mergedAt": None, "body": "Salvaged from #602\n\nFixes #12\n",
             "closingIssuesReferences": ref(12)},
            {"number": 703, "state": "MERGED", "mergedAt": "2026-11-13T09:00:00Z",
             "body": "Salvaged from https://github.com/trycua/cua/pull/603", "closingIssuesReferences": []},
            {"number": 704, "state": "MERGED", "mergedAt": "2026-11-14T09:00:00Z", "body": "Salvaged from #604",
             "closingIssuesReferences": ref(16)},
            {"number": 705, "state": "MERGED", "mergedAt": "2026-07-01T00:00:00Z", "body": "Salvaged from #605",
             "closingIssuesReferences": []},
            {"number": 706, "state": "CLOSED", "mergedAt": None, "body": "Salvaged from #606", "closingIssuesReferences": []},
            {"number": 707, "state": "OPEN", "mergedAt": None, "body": "Salvaged from #9999", "closingIssuesReferences": []}]))
        (self.t / "sources.json").write_text(json.dumps([
            {"number": 601, "state": "OPEN", "closingIssuesReferences": ref(10, 11)},
            {"number": 602, "state": "OPEN", "closingIssuesReferences": ref(12)},
            {"number": 603, "state": "OPEN", "closingIssuesReferences": ref(14, 15)},
            {"number": 604, "state": "OPEN", "closingIssuesReferences": ref(16)},
            {"number": 605, "state": "OPEN", "closingIssuesReferences": ref(18)},
            {"number": 606, "state": "OPEN", "closingIssuesReferences": ref(20)}]))
        self.pickup()
        report = Path(next(f for f in self.wakes()[0] if f.startswith("report="))[7:]).read_text()
        chat = report.split("chat:\n", 1)[1].split("\n\n", 1)[0].splitlines()
        self.assertEqual(sorted(x.removesuffix(" (new)") for x in chat), sorted([
            "- salvage cua#701 of cua#601 lacks `Fixes #10`, `Fixes #11` in its body: add it",
            "- cua#14 is still open: our merged salvage cua#703 fixed it but lacked `Fixes #14`; close it with a comment naming cua#703",
            "- cua#603 is still open after its salvage cua#703 merged: close it with a comment naming cua#703 (playbooks/hourly.md step 7)"]))
        self.assertNotIn("error:", report)
        self.pickup()
        self.assertEqual(len(self.wakes()), 1)

    def test_a_review_brief_asks_a_salvage_for_its_fixes_lines(self):
        self.github(reviews=[review(310, "2026-10-06T00:00:00Z", login="CMGS")])
        self.pickup()
        brief = (self.root / "review" / "cua-310-review-136aaaa" / "brief.md").read_text()
        self.assertIn("A salvage PR's body carries `Salvaged from #310` and a `Fixes #<issue>` line for each issue #310 fixes", brief)

    def test_no_pick_while_a_host_is_unreachable_or_github_fails(self):
        self.github(issues=[issue(10, "2026-10-01T00:00:00Z")])
        bad = self.t / "down"
        bad.write_text("#!/bin/bash\necho 'ssh: connect to host: timed out' >&2; exit 255\n")
        bad.chmod(0o755)
        out = self.pickup(ONIGIRI_PEERS=f"vm={bad}")
        self.assertIn("auto free 0; no picks: vm", out)
        self.assertEqual(self.rows("launch"), [])
        self.assertEqual(len(self.wakes()), 1)
        self.assertIn("host vm unreachable", self.wakes()[0][1])
        self.pickup(ONIGIRI_PEERS=f"vm={bad}")
        self.assertEqual(len(self.wakes()), 1)
        self.github(reviews=[])
        (self.t / "reviews.json").write_text("not json")
        out = self.pickup()
        self.assertIn("no picks: gh pr list -R", out)
        self.assertEqual(self.rows("launch"), [])

    def test_a_collected_result_wakes_once_and_a_failed_wake_is_retried_next_pass(self):
        d = self.root / "review" / "chat-job"
        d.mkdir()
        (d / "brief.md").write_text("task\nrole: coding\nsource: chat\n")
        r = subprocess.run([str(self.bin / "pi-run"), "chat-job", "coding", str(d / "brief.md")], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.wait("chat-job", "exited"))
        (d / "report.md").write_text("Opened https://github.com/trycua/cua/pull/7415.\n\nfollow-ups:\n- file the flake\n"
                                     "questions:\n- may I close #7000?\n")
        (self.t / "wake-fails").touch()
        self.pickup()
        self.assertEqual(self.state("chat-job"), "collected")
        self.assertEqual(len(self.wakes()), 1)
        (self.t / "wake-fails").unlink()
        self.pickup()
        self.assertEqual(len(self.wakes()), 2)
        kind, msg, *fields = self.wakes()[1]
        self.assertEqual(kind, "done")
        self.assertIn("chat-job: may I close #7000?", msg)
        self.assertIn("prs=https://github.com/trycua/cua/pull/7415", fields)
        report = Path(next(f for f in fields if f.startswith("report="))[7:]).read_text()
        self.assertIn("needs-Zane:\n- chat-job: may I close #7000?\n", report)
        self.assertIn("dumpling results:\n- chat-job on local: rc=0; PRs https://github.com/trycua/cua/pull/7415\n", report)
        self.assertIn("chat:\n- chat-job follow-up to file: file the flake\n", report)
        self.assertIn("outside-contributor drafts:\n- none\n", report)
        self.pickup()
        self.assertEqual(len(self.wakes()), 2)

    def test_a_run_whose_report_cannot_be_read_is_not_collected_and_loses_no_result(self):
        d = self.root / "review" / "chat-job"
        d.mkdir()
        (d / "brief.md").write_text("task\nrole: coding\nsource: chat\n")
        self.assertEqual(subprocess.run([str(self.bin / "pi-run"), "chat-job", "coding", str(d / "brief.md")], env=self.env,
                                        capture_output=True).returncode, 0)
        self.assertTrue(self.wait("chat-job", "exited"))
        (d / "run.log").unlink()
        out = self.pickup()
        self.assertEqual(self.state("chat-job"), "exited")
        self.assertIn("no picks: report chat-job on local:", out)
        self.assertEqual(len(self.wakes()), 1)
        self.assertIn("not collected", self.wakes()[0][1])
        (d / "report.md").write_text("Opened https://github.com/trycua/cua/pull/7415.\n")
        self.pickup()
        self.assertEqual(self.state("chat-job"), "collected")
        self.assertIn("prs=https://github.com/trycua/cua/pull/7415", self.wakes()[-1])

    def test_an_unknown_pr_head_blocks_every_pick(self):
        (self.t / "bin" / "gh").write_text(FAKE_GH.replace('"pr view"*) echo', '"pr view"*) exit 1; echo'))
        self.peer(waits=["cua#7229 4ef9df015 wait who=session: red after rerun: ci-gate"])
        self.github(issues=[issue(10, "2026-10-01T00:00:00Z")])
        out = self.pickup()
        self.assertIn("no picks: gh pr view 7229: its head and thread are unknown", out)
        self.assertEqual(self.rows("launch"), [])

    def test_a_standing_needs_zane_item_wakes_once_and_again_only_after_it_went_away(self):
        wait = "cua#7374 0b660ff71 wait who=zane: declared deep: needs Zane (no preapprove row)"
        self.peer(waits=[wait])
        self.pickup()
        self.pickup()
        self.assertEqual(len(self.wakes()), 1)
        self.assertIn("cua#7374 0b660ff71: declared deep", self.wakes()[0][1])
        self.peer(waits=[])
        self.pickup()
        self.peer(waits=[wait])
        self.pickup()
        self.assertEqual(len(self.wakes()), 2)

    def test_a_pass_past_its_deadline_is_killed_wakes_failed_and_frees_the_lock(self):
        self.github(issues=[issue(10, "2026-10-01T00:00:00Z")])
        (self.t / "gh-sleep").touch()
        t0 = time.time()
        out = self.pickup(rc=1, PICKUP_DEADLINE_S="3")
        self.assertLess(time.time() - t0, 25)
        self.assertIn("failed: the pass hit its 3s deadline and was killed", out)
        self.assertEqual([w[0] for w in self.wakes()], ["failed"])
        (self.t / "gh-sleep").unlink()
        self.pickup()
        self.assertEqual([r[1] for r in self.rows("launch")], ["cua-10"])

    def test_dry_prints_the_plan_and_changes_nothing(self):
        d = self.root / "review" / "chat-job"
        d.mkdir()
        (d / "brief.md").write_text("task\nrole: coding\nsource: chat\n")
        self.assertEqual(subprocess.run([str(self.bin / "pi-run"), "chat-job", "coding", str(d / "brief.md")], env=self.env,
                                        capture_output=True).returncode, 0)
        self.assertTrue(self.wait("chat-job", "exited"))
        (d / "report.md").write_text("Opened https://github.com/trycua/cua/pull/7415.\n")
        before = sorted(str(p) for p in self.root.rglob("*"))
        self.github(issues=[issue(10, "2026-10-01T00:00:00Z")])
        self.peer(waits=["cua#7374 0b660ff71 wait who=zane: declared deep: needs Zane (no preapprove row)"], at_main=True)
        out = self.pickup("--dry")
        self.assertIn("would launch vm cua-10: issue https://github.com/trycua/cua/issues/10", out)
        self.assertIn("would collect local chat-job: rc=0, prs 1", out)
        self.assertIn("would send done: pickup needs-Zane 1; dumpling results 1 (1 new)", out)
        self.assertEqual(sorted(str(p) for p in self.root.rglob("*")), before)
        self.assertEqual(self.state("chat-job"), "exited")
        self.assertEqual(self.wakes(), [])

    def test_the_peer_at_main_with_disk_free_is_the_launch_host_and_a_linux_item_goes_there(self):
        self.peer(at_main=True)
        (self.pkg / "hosts" / "mini" / "host.sh").write_text("")
        self.github(issues=[issue(10, "2026-10-01T00:00:00Z", labels=["Linux"]), issue(11, "2026-10-02T00:00:00Z", labels=["macOS"])])
        out = self.pickup("--dry", ONIGIRI_HOST="mini")
        self.assertIn("would launch vm cua-10:", out)
        self.assertIn("would launch mini cua-11:", out)

    def test_an_auto_pause_keeps_picks_off_the_peer_until_its_run_ends(self):
        (self.pkg / "hosts" / "mini" / "host.sh").write_text("")
        self.github(issues=[issue(10, "2026-10-01T00:00:00Z"), issue(11, "2026-10-02T00:00:00Z", labels=["Linux"])])
        pause = self.root / "auto-pause"
        pause.write_text("# bench on the vm\nvm until mini bench\n")
        self.peer(at_main=True)
        (self.root / "review" / "bench").mkdir()
        (self.root / "review" / "bench" / "run.json").write_text(json.dumps({"state": "running", "attempt": 1, "rc": None, "pool": "hand", **live()}))
        out = self.pickup("--dry", ONIGIRI_HOST="mini")
        self.assertIn("would launch mini cua-10:", out)
        self.assertIn("skip cua-11: host", out)
        (self.root / "review" / "bench" / "report.md").write_text("bench done\n")
        (self.root / "review" / "bench" / "run.json").write_text(json.dumps({"state": "exited", "attempt": 1, "pid": None, "rc": 0, "pool": "hand"}))
        out = self.pickup("--dry", ONIGIRI_HOST="mini")
        self.assertIn("would launch vm cua-10:", out)
        self.assertIn("would launch vm cua-11:", out)
        pause.write_text("vm\n")
        self.assertIn("would launch mini cua-10:", self.pickup("--dry", ONIGIRI_HOST="mini"))
        pause.write_text("vm until mini\n")
        self.assertIn("would launch mini cua-10:", self.pickup("--dry", ONIGIRI_HOST="mini"))
        pause.write_text("other-host\n")
        self.assertIn("would launch vm cua-10:", self.pickup("--dry", ONIGIRI_HOST="mini"))
        pause.write_text("vm\nmini\n")
        out = self.pickup("--dry", ONIGIRI_HOST="mini")
        self.assertNotIn("would launch", out)
        self.assertIn("skip cua-10: host", out)

    def test_an_unstick_is_picked_only_for_a_red_pr_no_thread_owns(self):
        self.peer(waits=["cua#7229 4ef9df015 wait who=session: red after rerun: ci-gate",
                         "cua#6975 f454fce4b wait who=session: ci-gate FAILURE; merge conflict"])
        self.pickup()
        self.assertEqual(sorted(r[1] for r in self.rows("launch")), ["cua-6975-unstick-f454fce", "cua-7229-unstick-4ef9df0"])
        self.assertIn("merge conflict", (self.root / "review" / "cua-6975-unstick-f454fce" / "brief.md").read_text())

    def test_the_schedule_is_a_plain_launchd_timer_at_the_policy_interval(self):
        r = subprocess.run([str(self.pkg / "hosts" / "mini" / "install-pickup.sh"), "print"], capture_output=True, text=True,
                           env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("<key>StartInterval</key><integer>1800</integer>", r.stdout)
        self.assertIn(f"<string>{self.pkg}/bin/pickup</string>", r.stdout)
        for banned in ("KeepAlive", "RunAtLoad", "StartCalendarInterval", "WatchPaths"):
            self.assertNotIn(banned, r.stdout)
        if shutil.which("plutil"):
            (self.t / "p.plist").write_text(r.stdout)
            self.assertEqual(subprocess.run(["plutil", "-lint", str(self.t / "p.plist")], capture_output=True).returncode, 0)

    def test_every_pass_prunes_old_stopped_workers_only_collected_runs_name(self):
        old = lambda h, tag: f"cua-e2e-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(time.time() - h * 3600))}-{tag}"  # noqa: E731
        gone, young, live_, unnamed, running, booted, seed, marked = (
            old(30, "0000000a"), old(2, "0000000b"), old(30, "0000000c"), old(30, "0000000d"), old(30, "0000000e"),
            old(30, "0000000f"), old(30, "00000010"), old(30, "00000011"))
        self.lume([(gone, "stopped"), (young, "stopped"), (live_, "stopped"), (unnamed, "stopped"), (running, "running"),
                   (booted, "stopped"), (seed, "stopped"), (marked, "stopped"), ("rmb-bench", "stopped"),
                   ("onigiri-linux", "running")], seed=seed)
        (self.t / ".local" / "share" / "cua-lume-e2e" / "unresolved").mkdir()
        (self.t / ".local" / "share" / "cua-lume-e2e" / "unresolved" / marked).write_text("x\n")
        (self.t / f"booted-{booted}").touch()
        ids = lambda *n: [x[8:] for x in n]  # noqa: E731
        self.run_dir("done-a", "collected", ids(gone, young, running, booted, seed, marked), where="scratch/lume.log")
        self.run_dir("done-b", "collected", ids(live_))
        self.run_dir("still-going", "running", ids(live_))
        self.assertGreaterEqual(free_gb(self.root), policy_floor())
        out = self.pickup()
        self.assertIn(f"prune {gone}:", out)
        self.assertEqual(self.vms(), sorted([young, live_, unnamed, running, booted, seed, marked, "rmb-bench", "onigiri-linux"]))
        self.assertEqual([r[:2] for r in self.rows("prune")], [["prune", gone]])
        self.assertIn("done-a collected", self.rows("prune")[0][2])
        self.assertIn(f"lume worker {booted} not pruned: it is no longer stopped", "".join(map(str, self.wakes())))
        self.run_dir("still-going", "collected", [live_[8:]])
        self.assertIn(f"prune {live_}:", self.pickup())
        self.assertNotIn(live_, self.vms())

    def test_a_run_that_named_a_worker_keeps_it_after_its_file_is_gone(self):
        name = f"cua-e2e-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(time.time() - 30 * 3600))}-000000aa"
        self.lume([(name, "stopped")])
        self.run_dir("done", "collected", [name[8:]])
        self.run_dir("going", "running", [name[8:]])
        self.pickup()
        self.assertEqual(self.vms(), [name])
        (self.root / "review" / "going" / "notes.txt").unlink()
        self.pickup()
        self.assertEqual(self.vms(), [name])

    def test_a_dir_that_cannot_be_read_holds_every_old_worker(self):
        name = f"cua-e2e-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(time.time() - 30 * 3600))}-000000dd"
        self.lume([(name, "stopped")])
        self.run_dir("done", "collected", [name[8:]])
        self.run_dir("going", "running", [], where="secret/notes.txt")
        (self.root / "review" / "going" / "secret").chmod(0)
        self.addCleanup((self.root / "review" / "going" / "secret").chmod, 0o755)
        self.pickup()
        self.assertEqual(self.vms(), [name])

    def test_a_file_that_cannot_be_read_holds_every_old_worker(self):
        name = f"cua-e2e-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(time.time() - 30 * 3600))}-000000de"
        self.lume([(name, "stopped")])
        self.run_dir("done", "collected", [name[8:]])
        self.run_dir("going", "running", [])
        (self.root / "review" / "going" / "notes.txt").chmod(0)
        self.addCleanup((self.root / "review" / "going" / "notes.txt").chmod, 0o644)
        self.pickup()
        self.assertEqual(self.vms(), [name])

    def test_a_worker_a_run_named_stays_held_while_it_runs_and_after(self):
        name = f"cua-e2e-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(time.time() - 30 * 3600))}-000000ee"
        self.lume([(name, "stopped")])
        self.run_dir("done", "collected", [name[8:]])
        self.run_dir("going", "running", [name[8:]])
        self.pickup()
        self.lume([(name, "running")])
        self.pickup()
        (self.root / "review" / "going" / "notes.txt").unlink()
        self.lume([(name, "stopped")])
        self.pickup()
        self.assertEqual(self.vms(), [name])

    def test_a_run_that_named_a_running_worker_still_holds_it_once_stopped(self):
        name = f"cua-e2e-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(time.time() - 30 * 3600))}-000000ef"
        self.lume([(name, "running")])
        self.run_dir("done", "collected", [name[8:]])
        self.run_dir("going", "running", [name[8:]])
        self.pickup()
        (self.root / "review" / "going" / "notes.txt").unlink()
        time.sleep(1.1)
        self.run_dir("done", "collected", [name[8:]])
        self.lume([(name, "stopped")])
        self.pickup()
        self.assertEqual(self.vms(), [name])

    def test_a_run_that_names_a_worker_during_the_pass_keeps_it(self):
        name = f"cua-e2e-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(time.time() - 30 * 3600))}-000000fe"
        self.lume([(name, "stopped")])
        self.run_dir("done", "collected", [name[8:]])
        (self.t / "late.py").write_text(LATE_SCAN)
        r = subprocess.run(["python3", str(self.t / "late.py"), str(self.bin / "pickup"), str(self.root / "review" / "late"), name[8:]],
                           capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.root / "review" / "late" / "notes.txt").exists())
        self.assertEqual(self.vms(), [name])

    def test_no_delete_while_a_lume_e2e_run_holds_its_lock(self):
        name = f"cua-e2e-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(time.time() - 30 * 3600))}-000000ff"
        self.lume([(name, "stopped")])
        self.run_dir("done", "collected", [name[8:]])
        fd = os.open(self.t / ".local" / "share" / "cua-lume-e2e" / "run.lock", os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.assertIn("holds", self.pickup())
        self.assertEqual(self.vms(), [name])
        os.close(fd)
        self.pickup()
        self.assertEqual(self.vms(), [])

    def test_lume_failing_prunes_nothing_and_tells_the_chat_once(self):
        name = f"cua-e2e-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(time.time() - 30 * 3600))}-000000bb"
        self.lume([(name, "stopped")])
        self.run_dir("done", "collected", [name[8:]])
        (self.t / "lume-ls-fails").touch()
        self.pickup()
        self.pickup()
        self.assertEqual(self.vms(), [name])
        self.assertEqual(len(self.wakes()), 1)
        self.assertIn("lume workers not pruned this pass: lume ls failed", self.wakes()[0][1])

    def test_dry_lists_the_prune_and_deletes_nothing(self):
        name = f"cua-e2e-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(time.time() - 30 * 3600))}-000000cc"
        self.lume([(name, "stopped")])
        self.run_dir("done", "collected", [name[8:]])
        self.assertIn(f"would prune {name}:", self.pickup("--dry"))
        self.assertEqual(self.vms(), [name])
        self.assertNotIn("delete", (self.t / "lume.log").read_text())


def free_gb(p):
    return shutil.disk_usage(p).free // 2**30


def policy_floor():
    return int(subprocess.run([str(PKG / "bin" / "policy"), "get", "hourly.floor_gb"], capture_output=True, text=True,
                              check=True).stdout)


if __name__ == "__main__":
    unittest.main()
