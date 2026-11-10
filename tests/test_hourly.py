"""Tests for bin/hourly (the hourly pass's facts, fingerprint skip, pickup.log and launch), bin/new-brief and bin/queue.
They run the real scripts against a temp root, a fake `pi`, a fake `gh` and a fake peer.
Run: python3 -m unittest discover -s tests -p 'test_*.py'"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
BIN = PKG / "bin"
FAKE_PI = "#!/bin/bash\n[ \"$1\" = --list-models ] && echo claude-code m && exit 0\n" \
    "printf '%s\\n' \"$@\" > \"$T/args\"\nwhile [ -e \"$T/hang\" ]; do sleep 0.05; done\n"
# gh pr list: our PRs from $T/ours.json, everyone else's from $T/others.json
FAKE_GH = "#!/bin/bash\ncase \"$*\" in *--author*) cat \"$T/ours.json\";; *) cat \"$T/others.json\";; esac\n"
TASK = "Fix the thing in trycua/cua.\n"


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
            for _ in range(100):
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
        self.assertFalse((self.review("q1") / "QUEUED").exists())
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
        legacy = self.review("old")
        legacy.mkdir()
        (legacy / "run.rc").write_text("0\n")
        self.assertIn("legacy   old run.rc=0", self.hourly())
        self.hourly("done")
        self.assertIn("legacy   old", self.hourly())                                      # actionable: never skipped
        self.assertEqual(self.sh("hourly", "processed", "old").returncode, 0)
        self.assertTrue((legacy / "processed").exists())
        self.hourly("done")
        self.assertIn("skip:", self.hourly())

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
        self.assertNotIn("e1", self.hourly())

    def test_slots_and_launch_host_follow_the_peer(self):
        peer = self.t / "peer"
        peer.write_text("#!/bin/bash\n" + 'echo \'{"sha": "s", "at_main": true, "parity": [], "free_gb": 25, "runs": '
                        '{"v1": {"state": "running", "auto": true, "idle_min": 200}}, "legacy": []}\'\n')
        peer.chmod(0o755)
        out = self.hourly(ONIGIRI_PEERS=f"vm={peer}")
        self.assertIn("host vm: zstack s at main, free 25G", out)
        self.assertIn("running  v1 auto idle_min=200  HUNG?", out)
        self.assertIn("slots: running 1/5, auto 1/3: free 4, auto 2", out)
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

    def queued(self, root, sid, age, source="chat"):
        d = root / "review" / sid
        d.mkdir(parents=True)
        (d / "brief.md").write_text(f"task {sid}\nrole: coding\nsource: {source}\n")
        (d / "QUEUED").write_text("coding\n")
        t = time.time() - age
        os.utime(d / "QUEUED", (t, t))

    def test_queue_launches_every_hosts_queue_on_that_host_oldest_first_under_the_shared_slots(self):
        local, vm = self.t / "root", self.t / "vm-root"
        peers = self.peer_pkg("test-vm", vm)
        (self.t / "hang").touch()
        for sid in ("p1", "p2"):   # two auto runs already hold slots: 2/5 running, 2/3 auto
            self.queued(local, sid, 1000, "auto")
            (local / "review" / sid / "QUEUED").unlink()
            self.assertEqual(self.sh("pi-run", sid, "coding", str(local / "review" / sid / "brief.md")).returncode, 0)
        self.queued(local, "a", 500)
        self.queued(vm, "b", 400, "auto")   # takes the last auto slot
        self.queued(local, "c", 300, "auto")   # auto slots full: waits
        self.queued(vm, "d", 200)   # the fifth slot
        self.queued(local, "e", 100)   # every slot full: waits
        dry = self.hourly("queue", ONIGIRI_PEERS=peers, PI_RUN_DRY="1")
        self.assertEqual([x.split(":")[0] for x in dry.splitlines()],
                         ["would launch local a", "would launch test-vm b", "wait local c", "would launch test-vm d", "wait local e",
                          "queue"])
        self.assertFalse((local / "pickup.log").exists())   # a dry pass logs and starts nothing
        self.assertFalse((vm / "review" / "b" / "run.json").exists())
        r = self.sh("hourly", "queue", ONIGIRI_PEERS=peers)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = [x.split("\t")[1:5] for x in (local / "pickup.log").read_text().splitlines()[2:]]
        self.assertEqual([x[:3] for x in rows], [["local", "launch", "a"], ["test-vm", "launch", "b"], ["local", "wait", "c"],
                                                 ["test-vm", "launch", "d"], ["local", "wait", "e"]])
        self.assertIn("slots full: running 4/5, auto 3/3", rows[2][3])
        self.assertIn("slots full: running 5/5, auto 3/3", rows[4][3])
        for root_, sid in ((local, "a"), (vm, "b"), (vm, "d")):   # each started on the host whose queue held it
            self.assertTrue((root_ / "review" / sid / "run.json").exists(), sid)
            self.assertFalse((root_ / "review" / sid / "QUEUED").exists(), sid)
        self.assertFalse((local / "review" / "b").exists())
        for sid in ("c", "e"):
            self.assertTrue((local / "review" / sid / "QUEUED").exists(), sid)
        self.assertIn("queue: 5 queued, slots now running 5/5, auto 3/3", r.stdout)

    def test_a_peers_queue_waits_while_it_is_off_main_or_under_the_floor_or_unreachable(self):
        vm = self.t / "vm-root"
        peers = self.peer_pkg("test-vm", vm)
        self.queued(vm, "v", 100)
        parity = self.t / "test-vm-home/.pi/agent/git/github.com/injaneity/zstack/hosts/check-parity"
        parity.write_text("#!/bin/bash\necho 'FAIL  package: old'; exit 1\n")
        out = self.hourly("queue", ONIGIRI_PEERS=peers)
        self.assertIn("wait test-vm v: not at main", out)
        self.assertTrue((vm / "review" / "v" / "QUEUED").exists())
        (self.t / "dead").write_text("#!/bin/bash\necho 'ssh: connect timed out' >&2; exit 255\n")
        (self.t / "dead").chmod(0o755)
        self.assertIn("host vm: unreachable, its queue waits: ssh: connect timed out", self.hourly("queue", ONIGIRI_PEERS=f"vm={self.t}/dead"))

    def test_every_entry_point_prints_the_sha_it_runs(self):
        sha = subprocess.run(["git", "-C", str(PKG), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        for prog, argv in (("hourly", ["facts"]), ("queue", ["nothing"]), ("pi-run", ["list"]), ("model-for", ["coding"])):
            self.assertEqual(self.sh(prog, *argv).stderr.splitlines()[0], f"zstack {sha}", prog)
        r = subprocess.run([sys.executable, str(PKG / "hosts" / "vm" / "wake.py"), "failed", "x"], capture_output=True, text=True,
                           env=self.env, timeout=30)
        self.assertEqual(r.stderr.splitlines()[0], f"zstack {sha}")


if __name__ == "__main__":
    unittest.main()
