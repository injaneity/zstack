"""Tests for bin/pi-run, the run lifecycle: start, exit, collect, relaunch, kill detection, signals and wakes.
step() is checked as a table; the rest runs the real launcher against a fake `pi` in a temp root.
Run: python3 -m unittest discover -s tests -p 'test_*.py'"""
import importlib.machinery
import importlib.util
import json
import os
import re
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

RULES = PKG / "skills" / "onigiri-mode" / "hard-rules.md"
# fake pi: `--list-models <model> --provider <p>` lists the model only when $TEST_TOKEN (the provider credential) is set.
# A run records its args and env, then hangs while $T/hang exists and exits with $T/rc. It traps TERM like
# pi does, so a forwarded signal is visible.
FAKE_PI = """#!/bin/bash
if [ "$1" = --list-models ]; then echo "provider model"; [ -n "$TEST_TOKEN" ] && echo "$4 $2"; exit 0; fi
trap 'echo TERM > "$T/got-sig"; exit 143' TERM
printf '%s\\n' "$@" > "$T/args"
echo "$ONIGIRI_READY_ALLOWED" > "$T/ready"
ONIGIRI_WAKE_DRY=1 "$ONIGIRI_WAKE" blocked "need a key" pr=9 > "$T/session-wake" 2>/dev/null
/usr/bin/python3 -c 'import signal; print(signal.getsignal(signal.SIGINT) == signal.SIG_IGN)' > "$T/int-ignored"
echo $$ > "$T/pi-pid"
[ -e "$T/say" ] && cat "$T/say"
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
    R = lambda self, s, **k: {"state": s, "attempt": 1, "pid": 10, "start": "t", "rc": None, "wake_finish": False, "wake": None, **k}   # noqa: E731

    def test_start_from_nothing_or_collected_opens_the_next_attempt(self):
        self.assertEqual(pr.step(None, "start", pid=5, start="s", wake_finish=True),
                         {"state": "running", "attempt": 1, "pid": 5, "start": "s", "rc": None, "wake_finish": True, "wake": None, "resume_at": None})
        # a relaunch takes the new brief's choice and clears the last attempt's wake
        self.assertEqual(pr.step(self.R("collected", rc=0, wake_finish=True, wake="done"), "start", pid=6, start="u", wake_finish=False),
                         {"state": "running", "attempt": 2, "pid": 6, "start": "u", "rc": None, "wake_finish": False, "wake": None,
                          "resume_at": None})

    def test_start_is_refused_until_the_last_result_is_collected(self):
        for run, alive, why in ((self.R("running"), True, "already running"), (self.R("exited", rc=0), False, "not collected"),
                                (self.R("running"), False, "was lost")):
            with self.assertRaisesRegex(pr.Refused, why):
                pr.step(run, "start", alive, pid=7, start="v", wake_finish=False)

    def test_exit_records_rc_only_for_the_recorded_supervisor(self):
        self.assertEqual(pr.step(self.R("running"), "exit", pid=10, rc=3)["state"], "exited")
        self.assertEqual(pr.step(self.R("running"), "exit", pid=10, rc=3)["rc"], 3)
        with self.assertRaises(pr.Refused):
            pr.step(self.R("running"), "exit", pid=11, rc=3)

    def test_exit_wakes_on_failure_always_and_on_finish_only_when_asked(self):
        for wake_finish, rc, want in ((False, 0, None), (False, 3, "failed"), (True, 0, "done"), (True, 3, "failed")):
            self.assertEqual(pr.step(self.R("running", wake_finish=wake_finish), "exit", pid=10, rc=rc)["wake"], want, (wake_finish, rc))

    def test_an_upstream_limit_pauses_without_a_wake_and_resumes_the_same_attempt_after_resume_at(self):
        paused = pr.step(self.R("running", wake_finish=True), "exit", pid=10, rc=1, resume_at="2026-11-10T05:00:00Z")
        self.assertEqual((paused["state"], paused["wake"], paused["resume_at"]), ("paused", None, "2026-11-10T05:00:00Z"))
        for event, f in (("start", dict(pid=7, start="v", wake_finish=False)), ("collect", {}),
                         ("resume", dict(pid=7, start="v", now="2026-11-10T04:59:59Z"))):
            with self.assertRaisesRegex(pr.Refused, "paused"):
                pr.step(paused, event, **f)
        back = pr.step(paused, "resume", pid=8, start="w", now="2026-11-10T05:00:00Z")
        self.assertEqual((back["state"], back["attempt"], back["pid"], back["rc"], back["resume_at"]), ("running", 1, 8, None, None))
        # rc 0 is never a pause, whatever the log said
        self.assertEqual(pr.step(self.R("running"), "exit", pid=10, rc=0, resume_at="x")["state"], "exited")

    def test_collect_takes_an_exited_or_lost_run_once(self):
        self.assertEqual(pr.step(self.R("exited", rc=0), "collect")["state"], "collected")
        self.assertEqual(pr.step(self.R("running"), "collect", alive=False)["state"], "collected")
        for run, alive in ((self.R("running"), True), (self.R("collected", rc=0), False), (None, False)):
            with self.assertRaises(pr.Refused):
                pr.step(run, "collect", alive)


def chart():
    """bin/PiRun.mmd as {(from, to, label)}: the chart TLC draws from bin/PiRun.tla (tests/pirun-model.sh keeps them equal)."""
    names, edges = {}, set()
    for line in (PKG / "bin" / "PiRun.mmd").read_text().replace("#60;", "<").splitlines():
        if m := re.fullmatch(r"\s*(s\d+): (.+)", line):
            names[m.group(1)] = m.group(2)
        elif m := re.fullmatch(r"\s*(s\d+) --> (s\d+): (.+)", line):
            edges.add((names[m.group(1)], names[m.group(2)], m.group(3)))
        elif m := re.fullmatch(r"\s*\[\*\] --> (s\d+)", line):
            edges.add(("[*]", names[m.group(1)], ""))
    return edges


class Model(unittest.TestCase):
    """step() against the model: every transition the chart has, and no other."""

    def test_step_makes_exactly_the_charts_transitions(self):
        def name(run, alive):
            if run is None:
                return "no run"
            if run["state"] == "running":
                return "running" if alive else "lost"
            return {"exited": f"exited, {run['wake'] or 'no'} wake", "collected": "collected", "paused": "paused"}[run["state"]]
        start, collect, resume = "pi-run <id> <role> <brief>", "pi-run collect <id>", "pi-run resume <id> [after resume_at]"
        edges, seen, todo = {("[*]", name(None, False), "")}, set(), [(None, False)]
        while todo:
            run, alive = todo.pop()
            key = (name(run, alive), bool(run and run["wake_finish"]))
            if key in seen:
                continue
            seen.add(key)
            asked = "brief has wake: finish" if key[1] else "no wake: finish"
            moves = [(start, "start", dict(pid=1, start="t", wake_finish=wf), True) for wf in (False, True)]
            moves += [(collect, "collect", {}, False), (resume, "resume", dict(pid=2, start="u", now="9"), True)]
            if key[0] == "running":   # only a live supervisor exits, and only a live run can be killed
                moves += [("pi exits rc != 0", "exit", dict(pid=run["pid"], rc=3), False),
                          (f"pi exits rc 0 [{asked}]", "exit", dict(pid=run["pid"], rc=0), False),
                          ("pi stops on an upstream 429", "exit", dict(pid=run["pid"], rc=1, resume_at="5"), False)]
                edges.add((key[0], "lost", "KILL or OOM"))
                todo.append((run, False))
            for label, event, fields, alive_after in moves:
                try:
                    after = pr.step(run, event, alive, **fields)
                except pr.Refused:
                    continue
                edges.add((key[0], name(after, alive_after), label))
                todo.append((after, alive_after))
        self.assertEqual(edges, chart())


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
                    "ONIGIRI_ROOT": str(self.t / "root"), "ONIGIRI_WAKE_DRY": "1", "TEST_TOKEN": "t"}
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
        self.assertEqual(args[args.index("--append-system-prompt") + 1], str(RULES))
        wakes = [json.loads(line) for line in self.log("cua-1").splitlines() if line.startswith("{")]
        self.assertEqual([(w["kind"], w["id"]) for w in wakes], [("failed", "cua-1")])
        mid = json.loads((self.t / "session-wake").read_text())
        self.assertEqual((mid["kind"], mid["id"], mid["pr"]), ("blocked", "cua-1", 9))

    def test_pi_gets_exactly_the_hard_rules_and_a_working_ctrl_c(self):
        self.start("rules")
        self.finished("rules")
        args = (self.t / "args").read_text().splitlines()
        self.assertEqual(args.count("--append-system-prompt"), 1)
        self.assertEqual(args[args.index("--append-system-prompt") + 1], str(RULES))   # the file, so pi reads main's text
        self.assertEqual((self.t / "int-ignored").read_text().strip(), "False")

    def test_the_hard_rules_are_one_file_and_the_skill_holds_no_second_copy(self):
        rules, skill = RULES.read_text(), (PKG / "skills" / "onigiri-mode" / "SKILL.md").read_text()
        self.assertTrue(rules.startswith("## Hard rules\n"))
        self.assertEqual([x for x in rules.splitlines() if x.startswith("## ")], ["## Hard rules"])
        heads = [x for x in rules.splitlines() if x.startswith("### ")]
        self.assertEqual(heads, ["### Work", "### Patent hold", "### Commits and PR text", "### Checks", "### Scope",
                                 "### Merging", "### Changing these rules"])
        self.assertEqual([h for h in heads if h in skill], [])
        self.assertIn("(hard-rules.md)", skill)

    def test_no_hard_rules_file_starts_nothing(self):
        pkg = self.t / "pkg"
        for sub in ("bin", "config", "hosts/vm", "skills/onigiri-mode"):
            (pkg / sub).mkdir(parents=True)
        for f in ("bin/pi-run", "bin/model-for", "config/models.json", "hosts/vm/wake.py"):
            (pkg / f).write_bytes((PKG / f).read_bytes())
            (pkg / f).chmod(0o755)
        for text in (None, "  \n"):
            if text is not None:
                (pkg / "skills/onigiri-mode/hard-rules.md").write_text(text)
            r = subprocess.run([sys.executable, str(pkg / "bin/pi-run"), "norules", "coding", str(self.t / "brief.md")],
                               capture_output=True, text=True, env=self.env, timeout=30)
            self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIsNone(self.run_json("norules"))
        self.assertFalse((self.t / "args").exists())

    def test_wake_finish_sends_done_with_rc_report_and_prs(self):
        (self.t / "w.md").write_text("task\nwake: finish\n")
        (self.t / "say").write_text("opened https://github.com/o/r/pull/38 and https://github.com/o/r/pull/38\n")
        self.start("w", brief="w.md")
        run = self.finished("w")
        self.assertEqual((run["wake_finish"], run["wake"]), (True, "done"))
        body = next(json.loads(line) for line in self.log("w").splitlines() if line.startswith('{"kind": "done"'))
        self.assertEqual((body["id"], body["rc"], body["prs"], body["report"]),
                         ("w", 0, ["https://github.com/o/r/pull/38"], str(self.t / "root" / "review" / "w" / "run.log")))
        # a relaunch without the line is failures-only again, and PR links from attempt 1 are not attempt 2's
        self.pi_run("collect", "w")
        (self.t / "say").unlink()
        self.start("w")
        self.assertEqual(self.finished("w")["wake"], None)
        self.assertEqual(self.log("w").count('{"kind": "done"'), 1)

    def test_a_wake_that_cannot_start_still_records_the_exit(self):
        (self.t / "w.md").write_text("task\nwake: finish\n")
        (self.t / "say").write_text(" ".join(f"https://github.com/o/r/pull/{n}" for n in range(60000)) + "\n")   # over ARG_MAX
        self.start("big", brief="w.md")
        self.assertEqual(self.finished("big")["rc"], 0)
        self.assertIn("pi-run: wake not sent", self.log("big"))

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
        self.assertEqual((r.returncode, r.stderr.strip().splitlines()[-1]), (3, "pi-run: s: attempt 1 exited rc=2 and is not collected"))
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
        self.assertEqual((r.returncode, r.stderr.strip().splitlines()[-1]), (3, "pi-run: oom: attempt 1 was lost and is not collected"))
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

    def test_no_pi_starts_nothing_and_a_pi_gone_after_the_check_is_a_failed_exit_not_a_loss(self):
        r = self.pi_run("nopi", "coding", str(self.t / "brief.md"), PATH="/usr/bin:/bin")   # no pi on PATH
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("cannot run pi in the launch env", r.stderr)
        self.assertIsNone(self.run_json("nopi"))
        (self.t / "bin" / "pi").write_text('#!/bin/bash\necho "$4 $2"; rm "$0"\n')   # answers the check, then is gone
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

    def test_an_upstream_429_pauses_with_no_wake_and_resume_continues_the_same_session(self):
        (self.t / "w.md").write_text("task\nwake: finish\n")
        (self.t / "say").write_text("Incomplete Claude Code upstream response (status 429, capture incomplete, upstream said: "
                                    "This request would exceed your account's rate limit. Please try again later.)\n")
        self.start("lim", rc=1, brief="w.md")
        self.assertTrue(wait_for(lambda: (self.run_json("lim") or {}).get("state") == "paused"), self.run_json("lim"))
        run = self.run_json("lim")
        self.assertEqual((run["attempt"], run["rc"], run["wake"]), (1, 1, None))
        self.assertNotIn("wake:", self.log("lim"))   # no failed wake
        self.assertEqual(self.pi_run("list", "paused").stdout, f"lim paused attempt=1 rc=1 resume_at={run['resume_at']}\n")
        self.assertEqual(self.pi_run("list", "exited", "lost").stdout, "")   # nothing for the pickup to collect
        self.assertEqual(self.pi_run("collect", "lim").returncode, 3)
        self.assertEqual(self.pi_run("lim", "coding", str(self.t / "w.md")).returncode, 3)
        (self.t / "say").unlink()
        (self.t / "rc").write_text("0")
        r = self.pi_run("resume", "lim")
        self.assertEqual(r.returncode, 0, r.stderr)
        run = self.finished("lim")
        self.assertEqual((run["attempt"], run["rc"], run["wake"], run["resumes"]), (1, 0, "done", 1))
        args = (self.t / "args").read_text().splitlines()
        self.assertEqual(args[args.index("--session-id") + 1], "lim")
        self.assertEqual(args[args.index("--name") + 1], "role:coding")
        self.assertEqual(args[-1], pr.RESUME)
        self.assertEqual(self.log("lim").count('{"kind": "done"'), 1)
        self.assertEqual(self.pi_run("resume", "lim").returncode, 3)   # only a paused run resumes

    def test_a_chatgpt_limit_resumes_after_the_wait_it_names_and_not_before(self):
        (self.t / "say").write_text("You have hit your ChatGPT usage limit (pro plan). Try again in ~42 min.\n")
        before = time.time()
        self.start("cx", rc=1)
        self.assertTrue(wait_for(lambda: (self.run_json("cx") or {}).get("state") == "paused"))
        at = time.mktime(time.strptime(self.run_json("cx")["resume_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        self.assertAlmostEqual(at - before, 42 * 60, delta=30)
        r = self.pi_run("resume", "cx")
        self.assertEqual(r.returncode, 3)
        self.assertIn("paused until", r.stderr)

    def test_a_run_that_only_quotes_a_429_is_a_plain_failure(self):
        for sid, say in (("q1", "Incomplete Claude Code upstream response (status 429, x)\nreport done\n"),
                         ("q2", "Task failed; quoted: Incomplete Claude Code upstream response (status 429, example)\n"),
                         ("q3", "Incomplete Claude Code upstream response (status 200, capture incomplete)\n")):
            (self.t / "say").write_text(say)
            self.start(sid, rc=1)
            self.assertEqual(self.finished(sid)["wake"], "failed", sid)

    def test_a_pi_killed_by_a_signal_after_a_429_line_is_a_failure(self):
        (self.t / "say").write_text("Incomplete Claude Code upstream response (status 429, capture incomplete)\n")
        self.start("sig", hang=True)
        wait_for(lambda: (self.t / "pi-pid").exists())
        os.kill(int((self.t / "pi-pid").read_text()), signal.SIGKILL)
        run = self.finished("sig")
        self.assertEqual((run["state"], run["rc"], run["wake"]), ("exited", 137, "failed"))

    def test_queue_shows_in_list_oldest_first_and_a_start_clears_it(self):
        for sid in ("qa", "qb"):
            d = self.t / "root" / "review" / sid
            d.mkdir(parents=True)
            (d / "brief.md").write_text("task\nrole: research\n")
        self.assertEqual(self.pi_run("queue", "qa").returncode, 0)
        self.assertEqual(self.pi_run("queue", "qb").returncode, 0)
        self.assertEqual((self.t / "root/review/qa/QUEUED").read_text(), "research\n")
        lines = self.pi_run("list", "queued").stdout.splitlines()
        self.assertEqual([x.split()[:3] for x in lines], [["qa", "queued", "attempt=0"], ["qb", "queued", "attempt=0"]])
        self.assertRegex(lines[0], r" since=\d{4}-\d\d-\d\dT")
        self.start("qa", brief="root/review/qa/brief.md")
        self.assertFalse((self.t / "root/review/qa/QUEUED").exists())
        self.assertEqual(self.pi_run("list", "queued").stdout.split()[:1], ["qb"])
        self.assertEqual(self.pi_run("queue", "nobrief").returncode, 2)

    def test_every_entry_point_prints_its_zstack_sha(self):
        sha = subprocess.run(["git", "-C", str(PKG), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        for argv in (["list"], ["--help"], ["collect", "none"]):
            self.assertEqual(self.pi_run(*argv).stderr.splitlines()[0], f"zstack {sha}", argv)

    def test_dry_run_and_bad_input_change_nothing(self):
        r = self.pi_run("dry", "coding", str(self.t / "brief.md"), PI_RUN_DRY="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dry run, would start dry", r.stdout)
        for argv, code in ((["x", "no-such-role", str(self.t / "brief.md")], 2), (["x", "coding", str(self.t / "missing")], 2),
                           (["a/b", "coding", str(self.t / "brief.md")], 2), (["x", "designer", str(self.t / "brief.md")], 2),
                           (["collect", "nothing"], 3)):
            self.assertEqual(self.pi_run(*argv).returncode, code, argv)
        self.assertFalse((self.t / "root").exists())

    def host(self, name, text):
        host = PKG / "hosts" / name
        host.mkdir()
        self.addCleanup(lambda: [p.unlink() for p in host.iterdir()] and host.rmdir())
        (host / "host.sh").write_text(text)
        return host

    def test_a_launch_with_no_credential_fails_fast_with_no_state_and_no_wake(self):
        # the 13:04 10 Nov incident: pi started without the env file, so claude-code had no key
        env = {k: v for k, v in self.env.items() if k != "TEST_TOKEN"}
        for dry in ("", "1"):
            r = subprocess.run([sys.executable, str(LAUNCHER), "nokey", "coding", str(self.t / "brief.md")], capture_output=True,
                               text=True, env={**env, "PI_RUN_DRY": dry}, timeout=30)
            self.assertEqual(r.returncode, 4, r.stderr)
            self.assertIn("pi-run: nokey: no claude-code credential in the launch env", r.stderr)
            self.assertIn("Nothing started.", r.stderr)
        self.assertFalse((self.t / "root").exists())   # no run.json, so no exit and no wake
        self.assertFalse((self.t / "args").exists())   # pi never ran a session

    def test_the_host_file_named_in_config_is_loaded_without_onigiri_host(self):
        # a bare nohup or ssh launch: no ONIGIRI_HOST and no credential in the shell; the host file holds it
        self.host("test-cfg", 'export TEST_TOKEN=from-host-file ONIGIRI_ROOT="$HOME/root/cfg"\n')
        env = {k: v for k, v in self.env.items() if k != "TEST_TOKEN"}
        self.assertEqual(subprocess.run([sys.executable, str(LAUNCHER), "c", "coding", str(self.t / "brief.md")], capture_output=True,
                                        text=True, env=env, timeout=30).returncode, 4)
        (self.t / ".config" / "onigiri").mkdir(parents=True)
        (self.t / ".config" / "onigiri" / "host").write_text("test-cfg\n")
        r = subprocess.run([sys.executable, str(LAUNCHER), "c", "coding", str(self.t / "brief.md")], capture_output=True, text=True,
                           env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("started c attempt 1 pid", r.stdout)
        self.assertIn(f"in {self.t}/root/cfg/review/c ", r.stdout)
        # ONIGIRI_HOST set, even empty, wins over the config file
        r = subprocess.run([sys.executable, str(LAUNCHER), "list"], capture_output=True, text=True, env={**env, "ONIGIRI_HOST": ""}, timeout=30)
        self.assertEqual(r.stdout, "")

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
