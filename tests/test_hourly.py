"""Tests for bin/hourly (the hourly pass's facts, fingerprint skip, pickup.log and launch), bin/new-brief and bin/queue.
They run the real scripts against a temp root, a fake `pi`, a fake `gh` and a fake peer.
Run: python3 -m unittest discover -s tests -p 'test_*.py'"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
BIN = PKG / "bin"
FAKE_PI = "#!/bin/bash\n[ \"$1\" = --list-models ] && echo claude-code m && exit 0\n" \
    "printf '%s\\n' \"$@\" > \"$T/args\"\nwhile [ -e \"$T/hang\" ]; do sleep 0.05; done\n"
# gh pr list: our PRs from $T/ours.json, everyone else's from $T/others.json
FAKE_GH = "#!/bin/bash\ncase \"$*\" in *--author*) cat \"$T/ours.json\";; *) cat \"$T/others.json\";; esac\n"
TASK = "Fix the thing in trycua/cua.\n"


def wait_for_state(d, state, secs=10):
    end = time.time() + secs
    while time.time() < end:
        if json.loads((d / "run.json").read_text())["state"] == state:
            return True
        time.sleep(0.05)
    return False


class Hourly(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.t = t = Path(td.name)
        (t / "bin").mkdir()
        for name, text in (("pi", FAKE_PI), ("gh", FAKE_GH)):
            (t / "bin" / name).write_text(text)
            (t / "bin" / name).chmod(0o755)
        (t / "ours.json").write_text(json.dumps([{"number": 1, "headRefOid": "a" * 40}]))
        (t / "others.json").write_text(json.dumps([{"number": 2, "author": {"login": "f-trycua"}},
                                                   {"number": 3, "author": {"login": "app/dependabot", "is_bot": True}}]))
        self.env = {"PATH": f"{t}/bin:/usr/bin:/bin", "HOME": str(t), "T": str(t), "ZSTACK_ENSURED": "1",
                    "ONIGIRI_ROOT": str(t / "root"), "ONIGIRI_WAKE_DRY": "1", "PI_CODING_AGENT_DIR": str(t / "agent")}
        (t / "root" / "review").mkdir(parents=True)
        self.addCleanup(self.reap)

    def reap(self):
        (self.t / "hang").unlink(missing_ok=True)
        for p in (self.t / "root").glob("**/run.json"):
            pid = json.loads(p.read_text())["pid"]
            for _ in range(100 if pid else 0):
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.05)

    def sh(self, prog, *argv, stdin=None, **env):
        return subprocess.run([str(BIN / prog), *argv], input=stdin, capture_output=True, text=True, env={**self.env, **env}, timeout=60)

    def hourly(self, *argv, **env):
        r = self.sh("hourly", *argv, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def review(self, sid):
        return self.t / "root" / "review" / sid

    def test_new_brief_carries_every_standing_line_and_never_overwrites(self):
        r = self.sh("new-brief", "cua-1", "coding", "auto", stdin=TASK)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = Path(r.stdout.strip()).read_text()
        self.assertEqual(Path(r.stdout.strip()), self.review("cua-1") / "brief.md")
        for line in ("role: coding", "source: auto", TASK.strip(), "wake: finish",
                     "Follow onigiri-mode's Hard rules (skills/onigiri-mode/hard-rules.md in zstack; bin/pi-run appends them to your system prompt).",
                     "No invented limits: a restriction must trace to Zane's words or the policy. "
                     "If you think one is needed, put it under questions: instead of applying it."):
            self.assertIn(line, text.splitlines())
        self.assertIn("`verified <full 40-character head sha>: ran <what>; skipped <what>; result <pass/fail>`", text)
        self.assertIn("`follow-ups:` and `questions:`", text)
        self.assertTrue(r.stderr.startswith("zstack "))
        self.assertEqual(self.sh("new-brief", "cua-1", "coding", stdin=TASK).returncode, 3)
        self.assertEqual(self.sh("new-brief", "cua-2", "no-such-role", stdin=TASK).returncode, 1)
        self.assertEqual(self.sh("new-brief", "cua-3", "coding", stdin="").returncode, 2)

    def test_queue_then_hourly_shows_it_and_launch_starts_it_and_logs_it(self):
        self.sh("new-brief", "q1", "research", stdin=TASK)
        r = self.sh("queue", "q1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^q1 queued role=research since=\S+Z\n$")
        out = self.hourly()
        self.assertRegex(out, r"\n  queued   q1 \S*since=")
        (self.t / "hang").touch()
        r = self.sh("hourly", "launch", "q1", "local", "chat", "queue")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("started q1 attempt 1", r.stdout)
        self.assertEqual(json.loads((self.review("q1") / "run.json").read_text())["state"], "running")
        for _ in range(200):
            if (self.t / "args").exists():
                break
            time.sleep(0.05)
        args = (self.t / "args").read_text().splitlines()
        self.assertEqual(args[args.index("--name") + 1], "role:research")
        row = (self.t / "root" / "pickup.log").read_text().splitlines()
        self.assertEqual(row[:2], ["# onigiri hourly pickup decisions, append-only. one line per decision, tab-separated:",
                                   "# ts_sgt\thost\taction\titem\treason\tfree_gb"])
        self.assertEqual(row[2].split("\t")[1:5], ["local", "launch", "q1", "research; chat queue"])
        self.assertRegex(row[2].split("\t")[5], r"^\d+$")
        self.assertIn("running  q1", self.hourly())

    def test_nothing_changed_is_one_silent_skip_line_and_any_change_reports(self):
        self.hourly("done")
        out = self.hourly()
        if "preapprove batch: due" in out:   # after 08:00 SGT a pass is owed until the batch row is written
            self.hourly("log", "local", "preapprove-batch", "-", "none")
            self.hourly("done")
            out = self.hourly()
        self.assertRegex(out, r"^skip: nothing changed since \S+ \(fingerprint [0-9a-f]{16}\)\n$")
        self.assertEqual(self.hourly("--linear", "CUA-9").splitlines()[0][:7], "hourly ")   # a new Todo is work
        (self.t / "ours.json").write_text(json.dumps([{"number": 1, "headRefOid": "b" * 40}]))
        self.assertNotIn("skip:", self.hourly())                                          # a push to ours is work
        self.hourly("done")
        self.assertIn("skip:", self.hourly())
        bots = json.loads((self.t / "others.json").read_text()) + [{"number": 4, "author": {"login": "app/x", "is_bot": True}}]
        (self.t / "others.json").write_text(json.dumps(bots))
        self.assertIn("skip:", self.hourly())                                             # a bot PR is not
        legacy = self.review("old")   # a run.rc dir from before run.json is nobody's state: nothing reads it
        legacy.mkdir()
        (legacy / "run.rc").write_text("0\n")
        self.assertIn("skip:", self.hourly())
        self.assertEqual(self.sh("hourly", "processed", "old").returncode, 2)
        self.assertNotIn("legacy", json.loads(self.hourly("facts")))

    def test_the_merge_jobs_waits_are_printed_by_who_must_act(self):
        peer = self.t / "mj"
        peer.write_text("#!/bin/bash\ncase \"$1\" in *merge_job.py*) printf '%s\\n' "
                        "'dry-run cua#7145 872f49fb0 wait who=zane: declared deep: needs Zane (no preapprove row)' "
                        "'dry-run cua#7185 96f2fa0ea wait who=session: red after rerun: ci-gate' "
                        "'dry-run cua#7190 3c4132d84 merge: green, ci-gate SUCCESS, risk standard' 'dry-run cua#7191 1 wait: draft';;\n"
                        '*) echo \'{"sha": "s", "at_main": true, "parity": [], "free_gb": 25, "runs": {}, "merge_last": '
                        f'"{datetime.now(timezone.utc).isoformat(timespec="seconds")} zstack {"a" * 40} ok"}}\';; esac\n')
        peer.chmod(0o755)
        out = self.hourly(ONIGIRI_PEERS=f"vm={peer}", ONIGIRI_MERGE_HOST="vm")
        self.assertIn("merge job needs-Zane: 1\n  cua#7145 872f49fb0: declared deep: needs Zane (no preapprove row)\n"
                      "merge job unstick: 1\n  cua#7185 96f2fa0ea: red after rerun: ci-gate\n", out)
        self.assertNotIn("7190", out)
        self.assertNotIn("7191", out)
        self.assertIn("merge job needs-Zane: none", self.hourly(ONIGIRI_PEERS=f"vm={peer}"))   # no merge host: nothing asked
        self.hourly("done", ONIGIRI_PEERS=f"vm={peer}", ONIGIRI_MERGE_HOST="vm")
        out = self.hourly(ONIGIRI_PEERS=f"vm={peer}", ONIGIRI_MERGE_HOST="vm")
        if "preapprove batch: due" in out:
            self.hourly("log", "local", "preapprove-batch", "-", "none")
            self.hourly("done", ONIGIRI_PEERS=f"vm={peer}", ONIGIRI_MERGE_HOST="vm")
        self.assertIn("skip:", self.hourly(ONIGIRI_PEERS=f"vm={peer}", ONIGIRI_MERGE_HOST="vm"))
        peer.write_text(peer.read_text().replace("'dry-run cua#7190 3c4132d84 merge: green, ci-gate SUCCESS, risk standard'",
                                                 "'dry-run cua#7190 3c4132d84 wait who=session: red after rerun: ci-gate'"))
        out = self.hourly(ONIGIRI_PEERS=f"vm={peer}", ONIGIRI_MERGE_HOST="vm")   # same heads, a new wait: never skipped
        self.assertIn("merge job unstick: 2\n", out)
        peer.write_text(peer.read_text().replace("*merge_job.py*) printf", "*merge_job.py*) exit 1; printf"))
        self.assertIn("error: merge job dry run on vm: exit 1", self.hourly(ONIGIRI_PEERS=f"vm={peer}", ONIGIRI_MERGE_HOST="vm"))

    def test_the_host_file_puts_gh_on_path_and_a_missing_gh_is_an_error_line(self):
        hidden = self.t / "hidden"   # gh only where the host file's PATH says, as on the mini (env.sh)
        hidden.mkdir()
        (self.t / "bin" / "gh").rename(hidden / "gh")
        if not shutil.which("gh", path="/usr/bin:/bin"):   # a runner with a system gh cannot lose it
            r = self.sh("hourly")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("error: gh pr list --author injaneity: gh: not found on PATH (set it in the host file)", r.stdout)
        host = PKG / "hosts" / "test-env"
        host.mkdir()
        self.addCleanup(lambda: [p.unlink() for p in host.iterdir()] and host.rmdir())
        (host / "host.sh").write_text(f'export PATH="{hidden}:$PATH"\n')
        out = self.hourly(ONIGIRI_HOST="test-env")
        self.assertNotIn("error:", out)
        self.assertIn("prs: ours 1 open, others 1 open", out)

    def test_the_merge_jobs_last_run_is_shown_and_a_stale_or_failed_one_never_skips(self):
        peer = self.t / "mj"
        def facts(last):
            peer.write_text("#!/bin/bash\ncase \"$1\" in *merge_job.py*) ;;\n*) echo '" + json.dumps(
                {"sha": "s", "at_main": True, "parity": [], "free_gb": 25, "runs": {}, "merge_last": last}) + "';; esac\n")
            peer.chmod(0o755)
            return self.hourly(ONIGIRI_PEERS=f"vm={peer}", ONIGIRI_MERGE_HOST="vm")
        at = lambda m: datetime.fromtimestamp(time.time() - 60 * m, timezone.utc).isoformat(timespec="seconds")  # noqa: E731
        sha = "a" * 40
        self.assertRegex(facts(f"{at(3)} zstack {sha} ok"), r"\nmerge job: ok: \S+ zstack a{40} ok \(\d+m ago\)\n")
        self.hourly("done", ONIGIRI_PEERS=f"vm={peer}", ONIGIRI_MERGE_HOST="vm")
        if "preapprove batch: due" in facts(f"{at(3)} zstack {sha} ok"):
            self.hourly("log", "local", "preapprove-batch", "-", "none")
            self.hourly("done", ONIGIRI_PEERS=f"vm={peer}", ONIGIRI_MERGE_HOST="vm")
        self.assertIn("skip:", facts(f"{at(4)} zstack {sha} ok"))   # a newer healthy run is not news
        for last, state in ((f"{at(56)} zstack {sha} ok", "stale"), (f"{at(3)} zstack {sha} 2 error(s)", "not ok"),
                            (f"{at(3)} zstack {sha} ok paused", "paused"), (None, "no last-run"),
                            (f"{at(-600)} zstack {sha} ok", "unreadable"),          # a future run cannot vouch for now
                            (f"{at(3)} zstack {sha} ok and more", "unreadable"),    # not the merge job's line
                            (f"{at(3)} zstack s ok", "unreadable"), ("garbage", "unreadable")):
            out = facts(last)
            self.assertIn(f"\nmerge job: {state}: ", out)
            self.assertNotIn("skip:", out)

    def test_a_report_for_an_id_with_nothing_is_one_error_line(self):
        self.review("empty").mkdir()
        r = self.sh("hourly", "report", "empty")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(r.stderr.splitlines()[1:], ["pi-run: empty: no report.md or run.log in review/empty on local"])

    def test_a_failed_pr_query_is_reported_and_never_skips(self):
        self.hourly("done")
        (self.t / "bin" / "gh").write_text("#!/bin/bash\necho 'HTTP 502' >&2; exit 1\n")
        for _ in range(2):
            out = self.hourly()
            self.assertIn("error: gh pr list --author injaneity: HTTP 502: the PR lists are unknown this pass", out)
            self.hourly("done")

    def test_an_exited_run_is_reported_until_collected_and_the_report_drops_token_lines(self):
        self.sh("new-brief", "e1", "coding", stdin=TASK)
        r = self.sh("pi-run", "e1", "coding", str(self.review("e1") / "brief.md"))
        self.assertEqual(r.returncode, 0, r.stderr)
        for _ in range(200):
            if json.loads((self.review("e1") / "run.json").read_text())["state"] == "exited":
                break
            time.sleep(0.05)
        self.hourly("done")
        self.assertIn("exited   e1 rc=0", self.hourly())
        (self.review("e1") / "report.md").write_text("did it\nGH_TOKEN=x\nfollow-ups: none\n")
        self.assertEqual(self.hourly("report", "e1"), "did it\nfollow-ups: none\n")
        self.assertEqual(self.sh("pi-run", "collect", "e1").returncode, 0)
        self.hourly("done")
        self.assertNotRegex(self.hourly(), r"(?m)^  \w+ +e1 ")

    def test_slots_and_launch_host_follow_the_peer(self):
        peer = self.t / "peer"
        peer.write_text("#!/bin/bash\n" + 'echo \'{"sha": "s", "at_main": true, "parity": [], "free_gb": 25, "runs": '
                        '{"v1": {"state": "running", "auto": true, "idle_min": 200}}}\'\n')
        peer.chmod(0o755)
        out = self.hourly(ONIGIRI_PEERS=f"vm={peer}")
        self.assertIn("host vm: zstack s at main, free 25G", out)
        self.assertIn("running  v1 auto idle_min=200  HUNG?", out)
        self.assertIn("slots: running 1/7, auto 1/5: free 6, auto 4", out)
        self.assertIn("launch host: vm", out)
        peer.write_text(peer.read_text().replace('"free_gb": 25', '"free_gb": 19'))
        self.assertIn("launch host: local", self.hourly(ONIGIRI_PEERS=f"vm={peer}"))
        peer.write_text("#!/bin/bash\necho 'ssh: connect timed out' >&2; exit 255\n")
        out = self.hourly(ONIGIRI_PEERS=f"vm={peer}")
        self.assertIn("host vm: unreachable: ssh: connect timed out", out)
        self.assertIn("launch host: local", out)
        self.hourly("done", ONIGIRI_PEERS=f"vm={peer}")
        self.assertNotIn("skip:", self.hourly(ONIGIRI_PEERS=f"vm={peer}"))   # its runs are unknown, not idle

    def test_a_brief_queued_on_the_peer_launches_from_the_peers_copy(self):
        peer_root = self.t / "peer-root"
        (peer_root / "review" / "v9").mkdir(parents=True)
        (peer_root / "review" / "v9" / "brief.md").write_text("task\nrole: research\n")
        # the peer command runs the line it is given in a shell with the peer's root, like ssh to the VM
        peer = self.t / "peer"
        peer.write_text(f'#!/bin/bash\nexport ONIGIRI_ROOT={peer_root} HOME={self.t}/peer-home\neval "$1"\n')
        peer.chmod(0o755)
        home = self.t / "peer-home" / ".pi/agent/git/github.com/injaneity"
        home.mkdir(parents=True)
        (home / "zstack").symlink_to(PKG)
        host = PKG / "hosts" / "test-peer"   # a host file with no parity check, as tests/test_pi_run.py does
        host.mkdir()
        self.addCleanup(lambda: [p.unlink() for p in host.iterdir()] and host.rmdir())
        (host / "host.sh").write_text("")
        (self.t / "hang").touch()
        r = self.sh("hourly", "launch", "v9", "test-peer", "chat", "queue", ONIGIRI_PEERS=f"test-peer={peer}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("started v9 attempt 1", r.stdout)
        self.assertTrue((peer_root / "review" / "v9" / "run.json").exists())
        self.assertFalse(self.review("v9").exists())
        row = (self.t / "root" / "pickup.log").read_text().splitlines()[-1].split("\t")
        self.assertEqual(row[1:5], ["test-peer", "launch", "v9", "research; chat queue"])

    def peer_pkg(self, name, root):
        """A peer: its own copy of the package (parity stubbed ok) under its own HOME, reached by a command that runs one
        shell line there, like the VM's ssh wrapper."""
        home = self.t / f"{name}-home"
        pkg = home / ".pi/agent/git/github.com/injaneity/zstack"
        for sub in ("bin", "hosts", "config", "skills/onigiri-mode"):
            shutil.copytree(PKG / sub, pkg / sub, ignore=shutil.ignore_patterns("playbooks", "test-*"))
        (pkg / "hosts" / "check-parity").write_text("#!/bin/bash\necho ok\n")
        (pkg / "hosts" / name).mkdir()
        (pkg / "hosts" / name / "host.sh").write_text("")
        cmd = self.t / f"{name}-cmd"
        cmd.write_text(f'#!/bin/bash\nexport ONIGIRI_ROOT={root} HOME={home}\neval "$1"\n')
        cmd.chmod(0o755)
        return f"{name}={cmd}"

    def queued(self, root, sid, age, source="chat", resume_in=None):
        """A queued run.json, as `pi-run queue` writes it, `age` seconds ago; with resume_in, a run back from a 429."""
        d = root / "review" / sid
        d.mkdir(parents=True)
        (d / "brief.md").write_text(f"task {sid}\nrole: coding\nsource: {source}\n")
        utc = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))   # noqa: E731
        run = {"state": "queued", "attempt": 0, "pid": None, "start": None, "rc": None, "wake_finish": False, "wake": None,
               "resume_at": None, "since": utc(time.time() - age), "role": "coding"}
        if resume_in is not None:
            (d / "brief.full.md").write_text(f"task {sid}\n")
            run.update(attempt=1, rc=1, resume_at=utc(time.time() + resume_in), extra=[])
        (d / "run.json").write_text(json.dumps(run))

    def test_queue_launches_every_hosts_queue_on_that_host_oldest_first_under_the_shared_slots(self):
        local, vm = self.t / "root", self.t / "vm-root"
        peers = self.peer_pkg("test-vm", vm)
        (self.t / "hang").touch()
        for sid in ("p1", "p2", "p3", "p4"):   # four auto runs already hold slots: 4/7 running, 4/5 auto
            self.queued(local, sid, 1000, "auto")
            self.assertEqual(self.sh("pi-run", sid, "coding", str(local / "review" / sid / "brief.md")).returncode, 0)
        self.queued(local, "a", 500)
        self.queued(vm, "b", 400, "auto")   # takes the last auto slot
        self.queued(local, "c", 300, "auto")   # auto slots full: waits
        self.queued(vm, "d", 200)   # the seventh slot
        self.queued(local, "e", 100)   # every slot full: waits
        dry = self.hourly("queue", ONIGIRI_PEERS=peers, PI_RUN_DRY="1")
        self.assertEqual([x.split(":")[0] for x in dry.splitlines()],
                         ["would launch local a", "would launch test-vm b", "wait local c", "would launch test-vm d", "wait local e",
                          "queue"])
        self.assertFalse((local / "pickup.log").exists())   # a dry pass logs, locks and starts nothing
        self.assertFalse((local / "hourly-queue.lock").exists())
        self.assertEqual(json.loads((vm / "review" / "b" / "run.json").read_text())["state"], "queued")
        r = self.sh("hourly", "queue", ONIGIRI_PEERS=peers)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = [x.split("\t")[1:5] for x in (local / "pickup.log").read_text().splitlines()[2:]]
        self.assertEqual([x[:3] for x in rows], [["local", "launch", "a"], ["test-vm", "launch", "b"], ["local", "wait", "c"],
                                                 ["test-vm", "launch", "d"], ["local", "wait", "e"]])
        self.assertIn("slots full: running 6/7, auto 5/5", rows[2][3])
        self.assertIn("slots full: running 7/7, auto 5/5", rows[4][3])
        state = lambda r, sid: json.loads((r / "review" / sid / "run.json").read_text())["state"]   # noqa: E731
        for root_, sid in ((local, "a"), (vm, "b"), (vm, "d")):   # each started on the host whose queue held it
            self.assertEqual(state(root_, sid), "running", sid)
        self.assertFalse((local / "review" / "b").exists())
        for sid in ("c", "e"):
            self.assertEqual(state(local, sid), "queued", sid)
        self.assertIn("queue: 5 queued, slots now running 7/7, auto 5/5", r.stdout)

    def test_a_pr_red_after_its_rerun_reopens_the_thread_that_pushed_its_head_once_per_head(self):
        sha, other = "a5d26a5a" + "1" * 32, "b" * 40
        d = self.review("salvage-7255")
        d.mkdir()
        (d / "brief.md").write_text("task\nrole: coding\n")
        (d / "brief.full.md").write_text("task\nrole: coding\nwake: finish\n")
        (d / "pushed").write_text(f"{'c' * 40} fix/x 2026-11-10T03:00:00.000000Z\n{sha} salvage/7255 2026-11-10T03:30:00.000000Z\n")
        e = self.review("earlier")   # pushed the same head before (its push rejected): the later push owns it
        e.mkdir()
        (e / "pushed").write_text(f"{sha} salvage/7255 2026-11-10T02:00:00.000000Z\n")
        (d / "run.json").write_text(json.dumps({"state": "collected", "attempt": 1, "pid": 1, "start": "t", "rc": 0, "wake_finish": True,
                                                "wake": "done", "resume_at": None, "since": "2026-11-10T20:10:17Z", "role": "coding",
                                                "extra": []}))
        peer = self.t / "mj"
        peer.write_text("#!/bin/bash\ncase \"$1\" in *merge_job.py*) printf '%s\\n' "
                        f"'dry-run cua#7338 {sha[:9]} wait who=session: red after rerun: ci-gate, contract clients / result' "
                        f"'dry-run cua#7400 {other[:9]} wait who=session: red after rerun: ci-gate' "
                        "'dry-run cua#7401 1234567ab wait who=session: merge conflict';;\n"
                        '*) echo \'{"sha": "s", "at_main": true, "parity": [], "free_gb": 25, "runs": {}, "pushed": {}}\';; esac\n')
        peer.chmod(0o755)
        env = dict(ONIGIRI_PEERS=f"vm={peer}", ONIGIRI_MERGE_HOST="vm")
        self.assertIn(f"cua#7338 {sha[:9]}: red after rerun: ci-gate, contract clients / result  (thread salvage-7255 on local: "
                      "bin/hourly queue reopens it)", self.hourly(**env))
        dry = self.hourly("queue", PI_RUN_DRY="1", **env)
        self.assertIn(f"would reopen local salvage-7255 for cua#7338 at {sha[:9]}", dry)
        self.assertIn(f"red cua#7400 {other[:9]}: no run pushed this head; file an unstick", dry)
        self.assertEqual(json.loads((d / "run.json").read_text())["state"], "collected")   # a dry pass changes nothing
        r = self.sh("hourly", "queue", **env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("salvage-7255 queued for cua#7338 at", r.stdout)
        self.assertIn("pi-run: resumed salvage-7255 attempt 1", r.stdout)   # the drain takes it in the same pass
        args = (self.t / "args").read_text().splitlines()
        self.assertEqual(args[args.index("--session-id") + 1], "salvage-7255")
        self.assertIn(f"Your PR trycua/cua#7338 is red at {sha}", args[-1])
        self.assertIn("fix 1 of 3", args[-1])
        self.assertTrue(wait_for_state(d, "exited"))
        self.assertEqual(self.sh("pi-run", "collect", "salvage-7255").returncode, 0)
        r = self.sh("hourly", "queue", **env)   # the same red head on the next pass: the thread already had it
        self.assertIn(f"red cua#7338 {sha[:9]}: salvage-7255: already reopened for cua#7338 at {sha}", r.stdout)
        self.assertEqual(json.loads((d / "run.json").read_text())["state"], "collected")
        rows = [x.split("\t")[1:4] for x in (self.t / "root" / "pickup.log").read_text().splitlines()[2:]]
        self.assertEqual(rows[:2], [["local", "ci-reopen", "salvage-7255"], ["local", "resume", "salvage-7255"]])

    def test_a_429d_run_frees_its_slot_and_a_peer_under_the_floor_launches_nothing(self):
        def peer(free, runs):
            f = self.t / "facts"
            f.write_text("#!/bin/bash\necho '" + json.dumps({"sha": "s", "at_main": True, "parity": [], "free_gb": free, "runs": runs}) + "'\n")
            f.chmod(0o755)
            return f"vm={f}"
        q = {"state": "queued", "auto": False, "since": "2026-11-10T00:00:00Z"}
        held = {f"r{i}": {"state": "running", "auto": False} for i in range(6)}
        out = self.hourly("queue", ONIGIRI_PEERS=peer(19, {"v": q, **held}))
        self.assertIn("wait vm v: 19G free, under 20G", out)
        self.assertIn("slots: running 6/7", self.hourly(ONIGIRI_PEERS=peer(25, held)))
        back = {"state": "queued", "auto": False, "since": "2026-11-09T00:00:00Z", "resume_at": "2099-01-01T00:00:00Z"}
        out = self.hourly("queue", ONIGIRI_PEERS=peer(25, {"v": q, "p": back, **held}), PI_RUN_DRY="1")
        self.assertIn("wait vm p: after a 429, not before resume_at 2099-01-01T00:00:00Z", out)   # older, but not yet
        self.assertIn("would launch vm v", out)   # the 429'd run's slot went to the next queued run
        self.assertIn("slots: running 6/7", self.hourly(ONIGIRI_PEERS=peer(25, {"p": back, **held})))

    def test_a_429d_run_resumes_from_the_drain_in_its_own_session_at_its_first_queue_place(self):
        local = self.t / "root"
        self.queued(local, "back", 900, resume_in=-60)    # queued first, hit a 429, resume_at passed
        self.queued(local, "fresh", 300)
        self.queued(local, "later", 1200, resume_in=3600)   # oldest place, but not before its resume_at
        for i in range(5):   # five runs hold slots: two left
            self.queued(local, f"h{i}", 2000)
            self.assertEqual(self.sh("pi-run", f"h{i}", "coding", str(local / "review" / f"h{i}" / "brief.md")).returncode, 0)
        (self.t / "hang").touch()
        self.hourly("done")
        self.assertNotIn("skip:", self.hourly())   # a queued run whose resume_at passed is work
        r = self.sh("hourly", "queue")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("pi-run: resumed back attempt 1", r.stdout)
        self.assertIn("started fresh attempt 1", r.stdout)
        self.assertIn("wait local later: after a 429, not before resume_at", r.stdout)
        rows = [x.split("\t")[2:4] for x in (local / "pickup.log").read_text().splitlines()[2:]]
        self.assertEqual(rows, [["wait", "later"], ["resume", "back"], ["launch", "fresh"]])
        run = json.loads((local / "review" / "back" / "run.json").read_text())
        self.assertEqual((run["state"], run["attempt"], run["resumes"]), ("running", 1, 1))
        self.assertEqual((local / "review" / "back" / "brief.full.md").read_text(), "task back\n")   # the brief it started with
        self.assertIn("as role coding", r.stdout)

    def test_only_the_drain_resumes_a_429d_run_even_when_the_callers_env_says_drain(self):
        self.queued(self.t / "root", "back", 900, resume_in=-60)
        for argv in (("launch", "back", "local", "chat"), ("recv", "back")):   # a direct hourly launch is not the drain
            r = self.sh("hourly", *argv, stdin="", PI_RUN_DRAIN="1")
            self.assertEqual(r.returncode, 3, argv)
            self.assertIn("queued after a 429", r.stderr)
        self.assertEqual(json.loads((self.review("back") / "run.json").read_text())["state"], "queued")

    def test_a_queue_pass_on_a_fresh_host_with_no_root_launches_nothing(self):
        shutil.rmtree(self.t / "root")
        self.assertIn("queue: 0 queued, slots now running 0/7", self.hourly("queue", PI_RUN_DRY="1"))
        self.assertFalse((self.t / "root").exists())   # a dry pass makes nothing
        r = subprocess.run([str(BIN / "hourly"), "queue"], cwd=self.t, capture_output=True, text=True, timeout=60,
                           env={**self.env, "ONIGIRI_ROOT": "rel/root", "PI_RUN_DRY": "1"})   # a relative root not made yet
        self.assertIn("queue: 0 queued", r.stdout, r.stderr)
        self.assertIn("queue: 0 queued, slots now running 0/7", self.hourly("queue"))

    def test_a_second_queue_pass_launches_nothing_while_one_runs(self):
        self.queued(self.t / "root", "solo", 10)
        with open(self.t / "root" / "hourly-queue.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)   # a pass in progress
            r = self.sh("hourly", "queue")
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("another hourly queue pass is running", r.stderr)
        self.assertEqual(json.loads((self.review("solo") / "run.json").read_text())["state"], "queued")
        (self.t / "hang").touch()
        self.assertIn("started solo", self.hourly("queue"))

    def test_a_peers_queue_waits_while_it_is_off_main_or_its_runs_are_unknown(self):
        vm = self.t / "vm-root"
        peers = self.peer_pkg("test-vm", vm)
        self.queued(vm, "v", 100)
        parity = self.t / "test-vm-home/.pi/agent/git/github.com/injaneity/zstack/hosts/check-parity"
        parity.write_text("#!/bin/bash\necho 'FAIL  package: old'; exit 1\n")
        out = self.hourly("queue", ONIGIRI_PEERS=peers)
        self.assertIn("wait test-vm v: not at main", out)
        self.assertEqual(json.loads((vm / "review" / "v" / "run.json").read_text())["state"], "queued")
        (self.t / "dead").write_text("#!/bin/bash\necho 'ssh: connect timed out' >&2; exit 255\n")
        (self.t / "dead").chmod(0o755)
        bad = self.review("bad")   # a run.json pi-run list cannot read: this host's runs are unknown too
        bad.mkdir()
        (bad / "run.json").write_text("{")
        self.assertRegex(self.hourly("queue"), r"host local: runs unknown, so every queue waits: pi-run list: .*JSON")
        bad.joinpath("run.json").unlink()
        bad.rmdir()
        self.queued(self.t / "root", "here", 50)   # the peer may hold every slot, so a local run waits too
        out = self.hourly("queue", ONIGIRI_PEERS=f"vm={self.t}/dead")
        self.assertIn("host vm: runs unknown, so every queue waits: ssh: connect timed out", out)
        self.assertIn("wait local here: vm runs unknown", out)
        self.assertEqual(json.loads((self.review("here") / "run.json").read_text())["state"], "queued")

    def test_every_entry_point_prints_the_sha_it_runs(self):
        sha = subprocess.run(["git", "-C", str(PKG), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        for prog, argv in (("hourly", ["facts"]), ("queue", ["nothing"]), ("pi-run", ["list"]), ("model-for", ["coding"])):
            self.assertEqual(self.sh(prog, *argv).stderr.splitlines()[0], f"zstack {sha}", prog)
        r = subprocess.run([sys.executable, str(PKG / "hosts" / "vm" / "wake.py"), "failed", "x"], capture_output=True, text=True,
                           env=self.env, timeout=30)
        self.assertEqual(r.stderr.splitlines()[0], f"zstack {sha}")


if __name__ == "__main__":
    unittest.main()
