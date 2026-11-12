"""Tests for bin/pickup, the scheduled pickup pass (model/Pickup.tla). They run the real script against a temp root, a fake
`pi`, a fake `gh` (GitHub's issues and PRs from JSON files), a fake peer and a fake wake that records each call.
Run: python3 -m unittest discover -s tests -p 'test_*.py'"""
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
  *review-requested*) cat "$T/reviews.json";;
  "pr list"*isDraft*) cat "$T/open.json";;
  "pr view"*) echo '{"headRefOid": "'"$(printf 'f%.0s' $(seq 40))"'", "headRefName": "x"}';;
  *) echo "fake gh: $*" >&2; exit 1;;
esac
"""
# records each wake's argv, one JSON line each; $T/wake-fails makes the POST fail
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
        self.pkg = t / "pkg"   # the package, with parity stubbed ok: a test checkout is not main
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
        """The merge host as a fake peer: its facts (runs, ids, the holds file) and the merge job's --dry-run lines. It is
        not at main unless asked, so it is never the launch host (playbooks/hourly.md step 3) and launches stay here."""
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

    def test_picks_oldest_first_urgent_ahead_up_to_the_auto_cap_and_never_twice(self):
        (self.t / "hang").touch()
        self.github(issues=[issue(n, f"2026-10-{n:02d}T00:00:00Z", labels=["urgent"] if n == 9 else []) for n in range(1, 10)])
        out = self.pickup()
        launched = [r[1] for r in self.rows("launch")]
        self.assertEqual(launched, ["cua-9", "cua-1", "cua-2", "cua-3", "cua-4"])   # auto 5 (config/policy.toml [slots])
        self.assertIn("9 candidates (0 taken before), auto free 5", out)
        brief = (self.root / "review" / "cua-9" / "brief.md").read_text()
        for line in ("source: auto", "https://github.com/trycua/cua/issues/9", "wake: finish"):
            self.assertIn(line, brief)
        self.assertTrue(all(self.state(s) == "running" for s in launched))
        self.assertEqual(self.wakes(), [])   # launches alone are not output for the chat
        out = self.pickup()   # all five still run: no slot, and no candidate launched again
        self.assertIn("4 candidates (5 taken before), auto free 0", out)
        self.assertEqual(len(self.rows("launch")), 5)
        (self.t / "hang").unlink()
        for s in launched:
            self.assertTrue(self.wait(s, "exited"), s)
        self.pickup()   # collects the five, then takes the next four
        self.assertEqual([r[1] for r in self.rows("launch")][5:], ["cua-5", "cua-6", "cua-7", "cua-8"])

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
                  runs={"busy": {"state": "running", "auto": False, "nums": [104]}})
        self.github(issues=[issue(101, "2026-10-01T00:00:00Z", labels=["hold"]),           # held: a hold label
                            issue(102, "2026-10-02T00:00:00Z", linked=[900]),             # pr: an open PR fixes it
                            issue(103, "2026-10-03T00:00:00Z", linked=[901]),             # its linked PR closed: pickable
                            issue(104, "2026-10-04T00:00:00Z"),                           # overlap: a live run names it
                            issue(105, "2026-10-05T00:00:00Z", labels=["Linux"])],        # host: the VM can't launch
                    reviews=[review(301, "2026-10-06T00:00:00Z", login="cua-bot[bot]", bot=True),   # held: a bot
                             review(302, "2026-10-07T00:00:00Z"),                          # held: a hold row
                             review(303, "2026-10-08T00:00:00Z"),                          # zane: the merge job waits on him
                             review(304, "2026-10-09T00:00:00Z", login="f-trycua")],       # a teammate's first review
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

    def test_no_pick_while_a_host_is_unreachable_or_github_fails(self):
        self.github(issues=[issue(10, "2026-10-01T00:00:00Z")])
        bad = self.t / "down"
        bad.write_text("#!/bin/bash\necho 'ssh: connect to host: timed out' >&2; exit 255\n")
        bad.chmod(0o755)
        out = self.pickup(ONIGIRI_PEERS=f"vm={bad}")
        self.assertIn("auto free 0; no picks: vm", out)
        self.assertEqual(self.rows("launch"), [])
        self.assertEqual(len(self.wakes()), 1)   # an unreachable host is a needs-Zane item: the chat hears of it once
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
        self.assertEqual(self.state("chat-job"), "collected")   # collected once, its result kept in the outbox
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
        self.assertEqual(len(self.wakes()), 2)   # delivered: nothing new

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
        self.pickup()   # the lock is free and the next pass runs whole
        self.assertEqual([r[1] for r in self.rows("launch")], ["cua-10"])

    def test_dry_prints_the_plan_and_changes_nothing(self):
        self.github(issues=[issue(10, "2026-10-01T00:00:00Z")])
        self.peer(waits=["cua#7374 0b660ff71 wait who=zane: declared deep: needs Zane (no preapprove row)"], at_main=True)
        out = self.pickup("--dry")
        self.assertIn("would launch vm cua-10: issue https://github.com/trycua/cua/issues/10", out)
        self.assertIn("would send done: pickup needs-Zane 1 (1 new)", out)
        self.assertFalse((self.root / "pickup.log").exists())
        self.assertFalse((self.root / "review" / "cua-10").exists())
        self.assertFalse((self.root / "pickup" / "state.json").exists())
        self.assertEqual(self.wakes(), [])

    def test_the_peer_at_main_with_disk_free_is_the_launch_host_and_a_linux_item_goes_there(self):
        self.peer(at_main=True)
        (self.pkg / "hosts" / "mini" / "host.sh").write_text("")   # this host is the mini, which macOS items need
        self.github(issues=[issue(10, "2026-10-01T00:00:00Z", labels=["Linux"]), issue(11, "2026-10-02T00:00:00Z", labels=["macOS"])])
        out = self.pickup("--dry", ONIGIRI_HOST="mini")
        self.assertIn("would launch vm cua-10:", out)
        self.assertIn("would launch mini cua-11:", out)

    def test_an_unstick_is_picked_only_for_a_red_pr_no_thread_owns(self):
        self.peer(waits=["cua#7229 4ef9df015 wait who=session: red after rerun: ci-gate",
                         "cua#6975 f454fce4b wait who=session: ci-gate FAILURE; merge conflict"])
        self.pickup()
        self.assertEqual(sorted(r[1] for r in self.rows("launch")), ["cua-6975-unstick-f454fce", "cua-7229-unstick-4ef9df0"])
        self.assertIn("merge conflict", (self.root / "review" / "cua-6975-unstick-f454fce" / "brief.md").read_text())


if __name__ == "__main__":
    unittest.main()
