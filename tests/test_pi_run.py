"""Tests for bin/pi-run, the run lifecycle: start, exit, collect, relaunch, kill detection, signals and wakes.
step() is checked as a table; the rest runs the real launcher against a fake `pi` in a temp root.
Run: python3 -m unittest discover -s tests -p 'test_*.py'"""
import importlib.machinery
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
LAUNCHER = PKG / "bin" / "pi-run"
loader = importlib.machinery.SourceFileLoader("pi_run_under_test", str(LAUNCHER))
spec = importlib.util.spec_from_loader(loader.name, loader)
pr = importlib.util.module_from_spec(spec)
loader.exec_module(pr)

EXT = next(line.split('"')[1] for line in (PKG / "bin" / "ensure-extensions").read_text().splitlines() if line.startswith("packages=("))
# fake pi: `list` shows the pinned extension; a run records its args and env, then hangs while $T/hang exists
# and exits with $T/rc. It traps TERM like pi does, so a forwarded signal is visible.
FAKE_PI = f"""#!/bin/bash
case "$1" in
  list) echo "User packages:"; echo "  {EXT}"; echo >> "$T/lists"
        [ -e "$T/vanish" ] && [ "$(wc -l < "$T/lists")" -ge 2 ] && rm "$0"; exit 0;;
esac
trap 'echo TERM > "$T/got-sig"; exit 143' TERM
printf '%s\\n' "$@" > "$T/args"
echo "$ONIGIRI_READY_ALLOWED" > "$T/ready"
ONIGIRI_WAKE_DRY=1 "$ONIGIRI_WAKE" blocked "need a key" pr=9 > "$T/session-wake" 2>/dev/null
/usr/bin/python3 -c 'import signal; print(signal.getsignal(signal.SIGINT) == signal.SIG_IGN)' > "$T/int-ignored"
echo $$ > "$T/pi-pid"
while [ -e "$T/hang" ]; do sleep 0.05; done
exit $(cat "$T/rc" 2>/dev/null || echo 0)
"""


def wait_for(pred, secs=10):
    end = time.time() + secs
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


class Step(unittest.TestCase):
    """The transition function, one row per (state, event)."""
    R = lambda self, s, **k: {"state": s, "attempt": 1, "pid": 10, "start": "t", "rc": None, **k}   # noqa: E731

    def test_start_from_nothing_or_collected_opens_the_next_attempt(self):
        self.assertEqual(pr.step(None, "start", pid=5, start="s"), {"state": "running", "attempt": 1, "pid": 5, "start": "s", "rc": None})
        self.assertEqual(pr.step(self.R("collected", rc=0), "start", pid=6, start="u"),
                         {"state": "running", "attempt": 2, "pid": 6, "start": "u", "rc": None})

    def test_start_is_refused_until_the_last_result_is_collected(self):
        for run, alive, why in ((self.R("running"), True, "already running"), (self.R("exited", rc=0), False, "not collected"),
                                (self.R("running"), False, "was lost")):
            with self.assertRaisesRegex(pr.Refused, why):
                pr.step(run, "start", alive, pid=7, start="v")

    def test_exit_records_rc_only_for_the_recorded_supervisor(self):
        self.assertEqual(pr.step(self.R("running"), "exit", pid=10, rc=3)["state"], "exited")
        self.assertEqual(pr.step(self.R("running"), "exit", pid=10, rc=3)["rc"], 3)
        with self.assertRaises(pr.Refused):
            pr.step(self.R("running"), "exit", pid=11, rc=3)

    def test_collect_takes_an_exited_or_lost_run_once(self):
        self.assertEqual(pr.step(self.R("exited", rc=0), "collect")["state"], "collected")
        self.assertEqual(pr.step(self.R("running"), "collect", alive=False)["state"], "collected")
        for run, alive in ((self.R("running"), True), (self.R("collected", rc=0), False), (None, False)):
            with self.assertRaises(pr.Refused):
                pr.step(run, "collect", alive)


class Launcher(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.t = Path(td.name)
        (self.t / "bin").mkdir()
        (self.t / "bin" / "pi").write_text(FAKE_PI)
        (self.t / "bin" / "pi").chmod(0o755)
        (self.t / "brief.md").write_text("task\n")
        self.env = {"PATH": f"{self.t}/bin:/usr/bin:/bin", "HOME": str(self.t), "T": str(self.t), "ZSTACK_ENSURED": "1",
                    "ONIGIRI_ROOT": str(self.t / "root"), "ONIGIRI_WAKE_DRY": "1"}
        self.addCleanup(self.reap)

    def reap(self):
        """Kill every run a test left behind (each supervisor leads its own process group) and wait until it is gone."""
        for p in (self.t / "root").glob("**/run.json"):
            pid = json.loads(p.read_text())["pid"]
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                continue
            wait_for(lambda pid=pid: subprocess.run(["pgrep", "-g", str(pid)], capture_output=True).returncode != 0)

    def pi_run(self, *argv, **env):
        return subprocess.run([sys.executable, str(LAUNCHER), *argv], capture_output=True, text=True, env={**self.env, **env}, timeout=30)

    def run_json(self, sid):
        p = self.t / "root" / "review" / sid / "run.json"
        return json.loads(p.read_text()) if p.exists() else None

    def start(self, sid, rc=0, hang=False, brief="brief.md", **env):
        (self.t / "rc").write_text(str(rc))
        (self.t / "hang").touch() if hang else (self.t / "hang").unlink(missing_ok=True)
        for f in ("pi-pid", "args"):
            (self.t / f).unlink(missing_ok=True)
        r = self.pi_run(sid, "coding", str(self.t / brief), **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def finished(self, sid):
        self.assertTrue(wait_for(lambda: (self.run_json(sid) or {}).get("state") == "exited"), self.run_json(sid))
        return self.run_json(sid)

    def log(self, sid):
        return (self.t / "root" / "review" / sid / "run.log").read_text()

    def test_a_run_goes_running_then_exited_with_pis_rc(self):
        self.start("s1", rc=0, hang=True)
        self.assertEqual(self.run_json("s1")["state"], "running")
        self.assertEqual(self.pi_run("list").stdout, "s1 running attempt=1 rc=None\n")
        (self.t / "hang").unlink()
        self.assertEqual((self.finished("s1")["rc"], self.finished("s1")["attempt"]), (0, 1))
        self.assertEqual(self.pi_run("list", "exited").stdout, "s1 exited attempt=1 rc=0\n")

    def test_the_session_id_dir_and_wake_id_are_the_given_id(self):
        self.start("cua-1", rc=7)
        self.finished("cua-1")
        args = (self.t / "args").read_text().splitlines()
        self.assertEqual(args[args.index("--session-id") + 1], "cua-1")
        self.assertEqual(args[args.index("--name") + 1], "role:coding")
        self.assertEqual(args[args.index("--append-system-prompt") + 1:][0], "## Hard rules")
        wakes = [json.loads(line) for line in self.log("cua-1").splitlines() if line.startswith("{")]
        self.assertEqual([(w["kind"], w["id"]) for w in wakes], [("failed", "cua-1")])
        mid = json.loads((self.t / "session-wake").read_text())
        self.assertEqual((mid["kind"], mid["id"], mid["pr"]), ("blocked", "cua-1", 9))

    def test_pi_gets_exactly_the_hard_rules_and_a_working_ctrl_c(self):
        self.start("rules")
        self.finished("rules")
        args = (self.t / "args").read_text()
        rules = subprocess.run([PKG / "bin" / "hard-rules"], capture_output=True, text=True, check=True).stdout.rstrip("\n")
        self.assertIn(f"--append-system-prompt\n{rules}\n", args)
        self.assertEqual((self.t / "int-ignored").read_text().strip(), "False")

    def test_no_hard_rules_section_starts_nothing(self):
        pkg = self.t / "pkg"
        for sub in ("bin", "config", "hosts/vm", "skills/onigiri-mode"):
            (pkg / sub).mkdir(parents=True)
        for f in ("bin/pi-run", "bin/hard-rules", "bin/model-for", "bin/ensure-extensions", "config/models.json", "hosts/vm/wake.py"):
            (pkg / f).write_bytes((PKG / f).read_bytes())
            (pkg / f).chmod(0o755)
        (pkg / "skills/onigiri-mode/SKILL.md").write_text("# onigiri mode\n\n## Principles\n\n- x\n")
        r = subprocess.run([sys.executable, str(pkg / "bin/pi-run"), "norules", "coding", str(self.t / "brief.md")],
                           capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIsNone(self.run_json("norules"))
        self.assertFalse((self.t / "args").exists())

    def test_only_a_failed_run_wakes(self):
        self.start("ok", rc=0)
        self.finished("ok")
        self.assertNotIn("wake:", self.log("ok"))
        self.start("bad", rc=3)
        self.finished("bad")
        self.assertIn("wake: failed not sent: dry run", self.log("bad"))
        self.assertIn('"reason": "bad exited rc=3"', self.log("bad"))

    def test_collect_is_once_and_a_relaunch_needs_it(self):
        self.start("s", rc=2)
        self.finished("s")
        r = self.pi_run("s", "coding", str(self.t / "brief.md"))
        self.assertEqual((r.returncode, r.stderr.strip()), (3, "pi-run: s: attempt 1 exited rc=2 and is not collected"))
        self.assertEqual(self.pi_run("collect", "s").stdout, "s attempt=1 rc=2\n")
        self.assertEqual(self.pi_run("collect", "s").returncode, 3)
        self.assertEqual(self.pi_run("list", "exited", "lost").stdout, "")

    def test_a_relaunch_after_collect_is_collectable_again(self):
        # cua-3926: collected at 21:38, relaunched at 21:41; the second result must still reach the pickup
        self.start("cua-3926", rc=0)
        self.finished("cua-3926")
        self.pi_run("collect", "cua-3926")
        self.start("cua-3926", rc=1)
        self.assertEqual(self.finished("cua-3926")["attempt"], 2)
        self.assertEqual(self.pi_run("list", "exited").stdout, "cua-3926 exited attempt=2 rc=1\n")
        self.assertEqual(self.pi_run("collect", "cua-3926").stdout, "cua-3926 attempt=2 rc=1\n")

    def test_a_second_launch_of_a_live_id_is_refused(self):
        self.start("live", hang=True)
        r = self.pi_run("live", "coding", str(self.t / "brief.md"))
        self.assertEqual(r.returncode, 3)
        self.assertIn("already running", r.stderr)
        self.assertEqual(self.pi_run("collect", "live").returncode, 3)

    def test_concurrent_launches_start_exactly_one(self):
        (self.t / "hang").touch()
        procs = [subprocess.Popen([sys.executable, str(LAUNCHER), "race", "coding", str(self.t / "brief.md")], env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(4)]
        codes = sorted(p.wait(timeout=30) for p in procs)
        self.assertEqual(codes, [0, 3, 3, 3])
        self.assertEqual(self.run_json("race")["attempt"], 1)

    def test_a_killed_run_is_lost_then_collectable(self):
        self.start("oom", hang=True)
        wait_for(lambda: (self.t / "pi-pid").exists())
        sup, pi = self.run_json("oom")["pid"], int((self.t / "pi-pid").read_text())
        os.kill(sup, signal.SIGKILL)
        # pi outlives its supervisor: the run's process group is alive, so it still counts as running
        self.assertEqual(self.pi_run("list").stdout, "oom running attempt=1 rc=None\n")
        os.kill(pi, signal.SIGKILL)
        self.assertTrue(wait_for(lambda: self.pi_run("list").stdout == "oom lost attempt=1 rc=None\n"))
        self.assertEqual(self.run_json("oom")["state"], "running")   # nothing wrote it: lost is derived
        r = self.pi_run("oom", "coding", str(self.t / "brief.md"))
        self.assertEqual((r.returncode, r.stderr.strip()), (3, "pi-run: oom: attempt 1 was lost and is not collected"))
        self.assertEqual(self.pi_run("collect", "oom").stdout, "oom attempt=1 lost\n")
        self.start("oom")
        self.assertEqual(self.finished("oom")["attempt"], 2)

    def test_term_to_the_supervisor_reaches_pi_and_is_recorded(self):
        self.start("term", hang=True)
        wait_for(lambda: (self.t / "pi-pid").exists())
        os.kill(self.run_json("term")["pid"], signal.SIGTERM)
        self.assertEqual(self.finished("term")["rc"], 143)
        self.assertEqual((self.t / "got-sig").read_text().strip(), "TERM")
        self.assertIn("wake: failed", self.log("term"))

    def test_a_pi_that_cannot_start_is_a_failed_exit_not_a_loss(self):
        (self.t / "vanish").touch()   # the fake pi passes ensure-extensions' two `pi list` calls, then removes itself
        self.start("nopi")
        run = self.finished("nopi")
        self.assertEqual(run["rc"], 127)
        self.assertIn("cannot start pi", self.log("nopi"))
        self.assertIn("wake: failed", self.log("nopi"))

    def test_merge_allowed_comes_only_from_the_brief(self):
        (self.t / "m.md").write_text("task\nmerge: allowed\n")
        (self.t / "near.md").write_text("task\nmerge: allowed later\n")
        for sid, brief, env, want in (("r1", "m.md", {}, "1"), ("r2", "brief.md", {}, "0"),
                                      ("r3", "brief.md", {"ONIGIRI_READY_ALLOWED": "1"}, "0"), ("r4", "near.md", {}, "0")):
            self.start(sid, brief=brief, **env)
            self.finished(sid)
            self.assertEqual((self.t / "ready").read_text().strip(), want, sid)

    def test_dry_run_and_bad_input_change_nothing(self):
        r = self.pi_run("dry", "coding", str(self.t / "brief.md"), PI_RUN_DRY="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry run, would start dry", r.stdout)
        for argv, code in ((["x", "no-such-role", str(self.t / "brief.md")], 2), (["x", "coding", str(self.t / "missing")], 2),
                           (["a/b", "coding", str(self.t / "brief.md")], 2), (["x", "designer", str(self.t / "brief.md")], 2),
                           (["collect", "nothing"], 3)):
            self.assertEqual(self.pi_run(*argv).returncode, code, argv)
        self.assertFalse((self.t / "root").exists())

    def test_a_host_file_sets_the_root_and_brief_prefix(self):
        host = PKG / "hosts" / "test-host"
        host.mkdir()
        self.addCleanup(lambda: [p.unlink() for p in host.iterdir()] and host.rmdir())
        (host / "prefix.md").write_text("## Host: test\n")
        (host / "host.sh").write_text(f'export ONIGIRI_ROOT="{self.t}/root/h" ONIGIRI_BRIEF_PREFIX="{host}/prefix.md"\n')
        self.start("h", ONIGIRI_HOST="test-host")
        d = self.t / "root" / "h" / "review" / "h"
        self.assertTrue(wait_for(lambda: d.joinpath("run.json").exists() and json.loads(d.joinpath("run.json").read_text())["state"] == "exited"))
        self.assertEqual((d / "brief.full.md").read_text(), "## Host: test\ntask\n")
        args = (self.t / "args").read_text()
        self.assertTrue(args.rstrip("\n").endswith("## Host: test\ntask"), args[-60:])


if __name__ == "__main__":
    unittest.main()
