"""Tests for bin/pi-run, the run lifecycle: start, exit, collect, relaunch, kill detection, signals and wakes.
step() is checked as a table; the rest runs the real launcher against a fake `pi` in a temp root.
Run: python3 -m unittest discover -s tests -p 'test_*.py'"""
import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
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

RULES = subprocess.run([sys.executable, str(PKG / "bin" / "policy"), "rules"], capture_output=True, text=True, check=True).stdout
# fake pi: `--list-models` lists a claude-code model only when $TEST_TOKEN (its credential) is set, and never openai-codex.
# A run records its args and env, then hangs while $T/hang exists and exits with $T/rc. It traps TERM like
# pi does, so a forwarded signal is visible.
FAKE_PI = """#!/bin/bash
if [ "$1" = --list-models ]; then echo "provider model"; [ -n "$TEST_TOKEN" ] && echo "claude-code m"; exit 0; fi
trap 'echo TERM > "$T/got-sig"; exit 143' TERM
printf '%s\\n' "$@" > "$T/args"
echo "${PI_RUN_DRAIN-unset}" > "$T/drain"
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

    def test_queue_then_start_opens_the_first_attempt_and_keeps_its_queue_place(self):
        q = pr.step(None, "queue", since="2026-11-10T01:00:00Z", role="research")
        self.assertEqual(q, {"state": "queued", "attempt": 0, "pid": None, "start": None, "rc": None, "wake_finish": False, "wake": None,
                             "resume_at": None, "since": "2026-11-10T01:00:00Z", "role": "research", "pool": "hand"})
        self.assertEqual(pr.step(q, "queue", since="later", role="coding"), q)   # queued twice: still the first place
        self.assertEqual(pr.step(q, "start", pid=5, start="s", wake_finish=True, now="2026-11-10T02:00:00Z"),
                         {**q, "state": "running", "attempt": 1, "pid": 5, "start": "s", "wake_finish": True})
        self.assertEqual(pr.step(None, "start", pid=5, start="s", wake_finish=False, now="n")["since"], "n")

    def test_start_is_refused_until_the_last_result_is_collected_and_after_it(self):
        """A retry is a fresh id: nothing restarts a session (onigiri-mode)."""
        for run, alive, why in ((self.R("running"), True, "already running"), (self.R("exited", rc=0), False, "not collected"),
                                (self.R("running"), False, "was lost"), (self.R("collected", rc=0), False, "a retry is a fresh id")):
            with self.assertRaisesRegex(pr.Refused, why):
                pr.step(run, "start", alive, pid=7, start="v", wake_finish=False, now="n")
        with self.assertRaisesRegex(pr.Refused, "a retry is a fresh id"):
            pr.step(self.R("collected", rc=0), "queue", since="n", role="coding")

    def test_exit_records_rc_only_for_the_recorded_supervisor(self):
        self.assertEqual(pr.step(self.R("running"), "exit", pid=10, rc=3)["state"], "exited")
        self.assertEqual(pr.step(self.R("running"), "exit", pid=10, rc=3)["rc"], 3)
        with self.assertRaises(pr.Refused):
            pr.step(self.R("running"), "exit", pid=11, rc=3)

    def test_exit_wakes_on_failure_always_and_on_finish_only_when_asked(self):
        for wake_finish, rc, want in ((False, 0, None), (False, 3, "failed"), (True, 0, "done"), (True, 3, "failed")):
            self.assertEqual(pr.step(self.R("running", wake_finish=wake_finish), "exit", pid=10, rc=rc)["wake"], want, (wake_finish, rc))

    def test_a_429_goes_back_to_the_queue_and_only_the_drain_resumes_it_after_resume_at(self):
        run = self.R("running", wake_finish=True, since="2026-11-10T01:00:00Z")
        back = pr.step(run, "exit", pid=10, rc=1, resume_at="2026-11-10T05:00:00Z", cap=3)
        self.assertEqual({k: back[k] for k in ("state", "attempt", "wake", "wake_finish", "resume_at", "since")},
                         {"state": "queued", "attempt": 1, "wake": None, "wake_finish": True, "resume_at": "2026-11-10T05:00:00Z",
                          "since": "2026-11-10T01:00:00Z"})   # no wake, the same attempt, and its first queue place
        go = dict(pid=8, start="w", wake_finish=False)
        for f, why in ((dict(go, now="2026-11-10T06:00:00Z"), "the hourly drain resumes it$"),   # a direct start, even after
                       (dict(go, now="2026-11-10T04:59:59Z", drain=True), "once that has passed")):
            with self.assertRaisesRegex(pr.Refused, why):
                pr.step(back, "start", **f)
        with self.assertRaisesRegex(pr.Refused, "nothing to collect"):
            pr.step(back, "collect")
        again = pr.step(back, "start", **dict(go, now="2026-11-10T05:00:00Z", drain=True))
        self.assertEqual({k: again[k] for k in ("state", "attempt", "pid", "rc", "resume_at", "wake_finish", "since", "resumes")},
                         {"state": "running", "attempt": 1, "pid": 8, "rc": None, "resume_at": None, "wake_finish": True,
                          "since": "2026-11-10T01:00:00Z", "resumes": 1})
        self.assertEqual(back["limit_resumes"], 1)
        # rc 0 is never a pause, whatever the log said
        self.assertEqual(pr.step(self.R("running"), "exit", pid=10, rc=0, resume_at="x")["state"], "exited")

    def test_an_upstream_limit_resumes_a_run_cap_times_and_the_next_one_fails_it(self):
        run = self.R("running", wake_finish=True)
        for n in (1, 2, 3):
            run = pr.step(run, "exit", pid=10, rc=1, resume_at="t", cap=3)
            self.assertEqual((run["state"], run["limit_resumes"], run["wake"]), ("queued", n, None))
            run = pr.step(run, "start", pid=10, start="s", wake_finish=False, now="t", drain=True)
        out = pr.step(run, "exit", pid=10, rc=1, resume_at="t", cap=3)
        self.assertEqual((out["state"], out["rc"], out["wake"], out["limit_resumes"]), ("exited", 1, "failed", 3))
        self.assertEqual(pr.step(out, "collect")["wake"], "failed")

    def test_collect_takes_an_exited_or_lost_run_once_and_a_lost_one_wakes(self):
        self.assertEqual(pr.step(self.R("exited", rc=0, wake="done"), "collect"), self.R("collected", rc=0, wake="done"))
        self.assertEqual(pr.step(self.R("running"), "collect", alive=False), self.R("collected", wake="failed"))
        for run, alive in ((self.R("running"), True), (self.R("collected", rc=0), False), (None, False)):
            with self.assertRaises(pr.Refused):
                pr.step(run, "collect", alive)


class CiStep(unittest.TestCase):
    """The thread that pushed a PR's head owns its CI: a red PR reopens the collected run, once per head, fix_attempts times."""
    R = Step.R

    def test_a_red_pr_reopens_its_collected_run_once_per_head_up_to_the_cap(self):
        done = self.R("collected", rc=0, wake="done", wake_finish=True, since="2026-11-10T01:00:00Z", role="coding")
        f = dict(pr="7338", why="ci-gate, contract clients", now="2026-11-11T05:00:00Z", cap=2)
        q = pr.step(done, "ci", head="a5d26a5", **f)
        self.assertEqual({k: q[k] for k in ("state", "attempt", "rc", "wake", "resume_at", "since", "ci", "ci_now")},
                         {"state": "queued", "attempt": 1, "rc": None, "wake": None, "resume_at": "2026-11-11T05:00:00Z",
                          "since": "2026-11-10T01:00:00Z", "ci": {"7338": ["a5d26a5"]},
                          "ci_now": {"pr": "7338", "head": "a5d26a5", "why": "ci-gate, contract clients", "n": 1, "cap": 2}})
        self.assertTrue(pr.continuation(q))
        with self.assertRaisesRegex(pr.Refused, "the hourly drain resumes it"):   # a direct start never takes it
            pr.step(q, "start", pid=8, start="w", wake_finish=False, now="2026-11-11T06:00:00Z")
        go = pr.step(q, "start", pid=8, start="w", wake_finish=False, now="2026-11-11T05:00:00Z", drain=True)
        self.assertEqual((go["state"], go["attempt"], go["resumes"], go["ci_now"], go["wake_finish"]), ("running", 1, 1, None, True))
        back = pr.step(pr.step(go, "exit", pid=8, rc=0), "collect")
        self.assertEqual(back["wake"], "done")   # the reopened thread reports back like the first time
        with self.assertRaisesRegex(pr.Refused, "already reopened for cua#7338 at a5d26a5"):
            pr.step(back, "ci", head="a5d26a5", **f)
        again = pr.step(back, "ci", head="b7", **f)
        self.assertEqual((again["ci"], again["ci_now"]["n"]), ({"7338": ["a5d26a5", "b7"]}, 2))
        spent = pr.step(pr.step(pr.step(again, "start", pid=9, start="x", wake_finish=False, now="9", drain=True), "exit", pid=9, rc=0), "collect")
        with self.assertRaisesRegex(pr.Refused, "fix attempts spent for cua#7338: 2 of 2"):
            pr.step(spent, "ci", head="c9", **f)
        self.assertEqual(pr.step(spent, "ci", head="c9", **dict(f, pr="7400"))["ci"]["7400"], ["c9"])   # the cap is per PR

    def test_only_a_collected_run_is_reopened(self):
        f = dict(pr="1", head="h", why="w", now="n", cap=3)
        for run, alive, why in ((self.R("running"), True, "still running"), (self.R("exited", rc=0), False, "not collected"),
                                (None, False, "no ci from absent"), (self.R("queued", resume_at="x"), False, "no ci from queued")):
            with self.assertRaisesRegex(pr.Refused, why):
                pr.step(run, "ci", alive, **f)


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
            if run["state"] == "queued":
                return ("queued for its PR's red CI" if run.get("ci_now") else "queued after a 429") if run.get("resume_at") else "queued"
            return {"exited": f"exited, {run['wake'] or 'no'} wake", "collected": "collected"}[run["state"]]
        start, collect = "pi-run <id> <role> <brief>, or the hourly drain", "pi-run collect <id>"
        resume = "hourly drain, after resume_at [same session]"
        edges, seen, todo = {("[*]", name(None, False), "")}, set(), [(None, False)]
        while todo:
            run, alive = todo.pop()
            key = (name(run, alive), bool(run and run["wake_finish"]), (run or {}).get("limit_resumes", 0))
            if key in seen:
                continue
            seen.add(key)
            asked = "brief has wake: finish" if key[1] else "no wake: finish"
            label = resume if key[0] in ("queued after a 429", "queued for its PR's red CI") else start
            moves = [(label, "start", dict(pid=1, start="t", wake_finish=wf, now="9", drain=dr), True)
                     for wf in (False, True) for dr in (False, True)]
            moves += [("pi-run queue <id>", "queue", dict(since="1", role="coding"), False)]
            moves += [(collect + (" [sends the failed wake]" if key[0] == "lost" else ""), "collect", {}, False)]
            moves += [("its PR is red after the rerun, at a head it pushed [fix attempts left]", "ci",
                       dict(pr="7", head=f"h{len(seen)}", why="ci-gate", now="1", cap=1), False)]
            if key[0] == "running":   # only a live supervisor exits, and only a live run can be killed
                limit = "resumes left, frees its slot" if key[2] < 1 else "resume.cap spent, sends the failed wake"
                moves += [("pi exits rc != 0", "exit", dict(pid=run["pid"], rc=3, cap=1), False),
                          (f"pi exits rc 0 [{asked}]", "exit", dict(pid=run["pid"], rc=0, cap=1), False),
                          (f"pi stops on an upstream 429 [{limit}]", "exit", dict(pid=run["pid"], rc=1, resume_at="5", cap=1), False)]
                edges.add((key[0], "lost", "KILL or OOM"))
                todo.append((run, False))
            for lbl, event, fields, alive_after in moves:
                try:
                    after = pr.step(run, event, alive, **fields)
                except pr.Refused:
                    continue
                if after != run:   # a second queue is no step
                    edges.add((key[0], name(after, alive_after), lbl))
                todo.append((after, alive_after))
        joined = {}   # the chart draws one edge per (from, to), its labels sorted and joined with " or "
        for a, b, lbl in edges:
            joined.setdefault((a, b), set()).add(lbl)
        self.assertEqual({(a, b, " or ".join(sorted(ls))) for (a, b), ls in joined.items()}, chart())


def tomllib_load(path):
    return pr.policy().read(str(path))


class OneConfigPerProcess(unittest.TestCase):
    def test_a_package_moved_mid_process_leaves_its_policy_as_first_read(self):
        """13 Nov 21:40 SGT: a pickup pass loaded bin/pi-run, another launch fast-forwarded the package to a policy.toml with
        a new [run] table, and the pass's next caps() read it from disk and died on `unknown key run`. One process reads the
        policy once, so its code and config always match."""
        with tempfile.TemporaryDirectory() as t:
            pkg = Path(t)
            for sub in ("bin", "config"):
                (pkg / sub).mkdir()
            for f in ("bin/pi-run", "bin/policy", "config/policy.toml"):
                (pkg / f).write_bytes((PKG / f).read_bytes())
            shutil.copytree(PKG / "vendor", pkg / "vendor")
            ld = importlib.machinery.SourceFileLoader("pi_run_moved", str(pkg / "bin" / "pi-run"))
            mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(ld.name, ld))
            ld.exec_module(mod)
            # the package moves before this process reads anything from its policy: a newer bin/policy and policy.toml
            (pkg / "config/policy.toml").write_text((PKG / "config/policy.toml").read_text() + "\n[newer]\nx = 1\n")
            (pkg / "bin/policy").write_text("raise SystemExit('a newer bin/policy was loaded')\n")
            want = tomllib_load(PKG / "config/policy.toml")
            self.assertEqual((mod.caps(), mod.resume_cap(), mod.idle_limit()),
                             (want["slots"], want["resume"]["cap"], want["run"]["idle_min"] * 60))
            self.assertEqual(mod.config(), mod.config())


class Launcher(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.t = Path(td.name)
        (self.t / "bin").mkdir()
        (self.t / "bin" / "pi").write_text(FAKE_PI)
        (self.t / "bin" / "pi").chmod(0o755)
        (self.t / "py").mkdir()
        (self.t / "py" / "python3").symlink_to(sys.executable)   # the interpreter the suite runs under, so a 3.9 run tests the mini's /usr/bin one
        (self.t / "bin" / "python3").symlink_to(sys.executable)
        (self.t / "brief.md").write_text("task\n")
        self.env = {"PATH": f"{self.t}/bin:/usr/bin:/bin", "HOME": str(self.t), "T": str(self.t), "ZSTACK_ENSURED": "1",
                    "ONIGIRI_ROOT": str(self.t / "root"), "ONIGIRI_WAKE_DRY": "1", "TEST_TOKEN": "t"}
        self.addCleanup(self.reap)

    def reap(self):
        """Kill every run a test left behind (each supervisor leads its own process group) and wait until it is gone."""
        for p in (self.t / "root").glob("**/run.json"):
            pid = json.loads(p.read_text())["pid"]
            if pid is None:   # queued: nothing started
                continue
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
        self.assertEqual(Path(args[args.index("--append-system-prompt") + 1]).read_text(), RULES)
        wakes = [json.loads(line) for line in self.log("cua-1").splitlines() if line.startswith("{")]
        self.assertEqual([(w["kind"], w["id"]) for w in wakes], [("failed", "cua-1")])
        mid = json.loads((self.t / "session-wake").read_text())
        self.assertEqual((mid["kind"], mid["id"], mid["pr"]), ("blocked", "cua-1", 9))

    def test_pi_gets_exactly_the_hard_rules_and_a_working_ctrl_c(self):
        self.start("rules")
        self.finished("rules")
        args = (self.t / "args").read_text().splitlines()
        self.assertEqual(args.count("--append-system-prompt"), 1)
        rules = Path(args[args.index("--append-system-prompt") + 1])   # rendered at the start, so pi reads main's text
        self.assertEqual(rules, self.t / "root" / "review" / "rules" / "rules.md")
        self.assertEqual(rules.read_text(), RULES)
        self.assertEqual((self.t / "int-ignored").read_text().strip(), "False")

    def test_the_skill_holds_no_second_copy_of_the_hard_rules(self):
        skill = (PKG / "skills" / "onigiri-mode" / "SKILL.md").read_text()
        heads = [x for x in RULES.splitlines() if x.startswith("### ")]
        self.assertEqual(len(heads), 7)
        self.assertEqual([h for h in heads if h in skill], [])
        self.assertIn("`config/rules.toml`", skill)

    def test_no_hard_rules_starts_nothing(self):
        """A missing or unreadable config/rules.toml starts no pi: a session never runs without its rules."""
        pkg = self.t / "pkg"
        for sub in ("bin", "config", "hosts/vm"):
            (pkg / sub).mkdir(parents=True)
        for f in ("bin/pi-run", "bin/hourly", "bin/model-for", "bin/policy", "config/policy.toml", "config/state.toml", "hosts/vm/wake.py"):
            (pkg / f).write_bytes((PKG / f).read_bytes())
            (pkg / f).chmod(0o755)
        shutil.copytree(PKG / "vendor", pkg / "vendor")   # bin/policy before python3 3.11
        for text in (None, "  \n", (PKG / "config/rules.toml").read_text().replace("trycua/cua-staging", "trycua/elsewhere")):
            if text is not None:
                (pkg / "config/rules.toml").write_text(text)
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
        # a brief without the line is failures-only
        (self.t / "say").unlink()
        self.start("w2")
        self.assertEqual(self.finished("w2")["wake"], None)
        self.assertEqual(self.log("w2").count('{"kind": "done"'), 0)

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

    def test_a_retry_is_a_fresh_id_and_its_result_reaches_the_pickup(self):
        # cua-3926 was relaunched under its own id after collect; a retry now takes a fresh id (cua-3308-r, 10 Nov)
        self.start("cua-3926", rc=0)
        self.finished("cua-3926")
        self.pi_run("collect", "cua-3926")
        self.assertEqual(self.pi_run("cua-3926", "coding", str(self.t / "brief.md")).returncode, 3)
        self.start("cua-3926-r", rc=1)
        self.assertEqual(self.finished("cua-3926-r")["attempt"], 1)
        self.assertEqual(self.pi_run("list", "exited").stdout, "cua-3926-r exited attempt=1 rc=1\n")
        self.assertEqual(self.pi_run("collect", "cua-3926-r").stdout, "cua-3926-r attempt=1 rc=1\n")
        args = (self.t / "args").read_text().splitlines()
        self.assertEqual(args[args.index("--session-id") + 1], "cua-3926-r")   # a new pi session

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
        self.assertEqual(self.run_json("oom")["wake"], "failed")

    def test_term_to_the_supervisor_reaches_pi_and_is_recorded(self):
        self.start("term", hang=True)
        wait_for(lambda: (self.t / "pi-pid").exists())
        os.kill(self.run_json("term")["pid"], signal.SIGTERM)
        self.assertEqual(self.finished("term")["rc"], 143)
        self.assertEqual((self.t / "got-sig").read_text().strip(), "TERM")
        self.assertIn("wake: failed", self.log("term"))

    def test_no_pi_starts_nothing_and_a_pi_gone_after_the_check_is_a_failed_exit_not_a_loss(self):
        r = self.pi_run("nopi", "coding", str(self.t / "brief.md"), PATH=f"{self.t}/py:/usr/bin:/bin")   # no pi on PATH
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("cannot run pi in the launch env", r.stderr)
        self.assertIsNone(self.run_json("nopi"))
        (self.t / "bin" / "pi").write_text('#!/bin/bash\necho "claude-code m"; rm "$0"\n')   # answers the check, then is gone
        self.start("nopi")
        run = self.finished("nopi")
        self.assertEqual(run["rc"], 127)
        self.assertIn("cannot start pi", self.log("nopi"))
        self.assertIn("wake: failed", self.log("nopi"))

    def idle_package(self):
        """A copy of the package whose config/policy.toml run.idle_min is 1, the smallest limit, so the supervisor's idle
        check fires in about a minute."""
        pkg = self.t / "pkg"
        for sub in ("bin", "config", "hosts/vm"):
            (pkg / sub).mkdir(parents=True, exist_ok=True)
        for f in ("bin/pi-run", "bin/hourly", "bin/model-for", "bin/policy", "config/rules.toml", "config/state.toml", "hosts/vm/wake.py"):
            (pkg / f).write_bytes((PKG / f).read_bytes())
            (pkg / f).chmod(0o755)
        shipped = (PKG / "config/policy.toml").read_text()
        self.assertEqual(len(re.findall(r"(?m)^idle_min = \d+$", shipped)), 1)
        (pkg / "config/policy.toml").write_text(re.sub(r"(?m)^idle_min = \d+$", "idle_min = 1", shipped))
        shutil.copytree(PKG / "vendor", pkg / "vendor")
        return pkg

    def test_a_run_with_no_progress_is_stopped_at_the_idle_limit_and_exits_failed(self):
        """cua-2879 held an auto slot for 20 h (13 Nov): a pi that writes nothing anywhere is stopped once run.idle_min
        passes, and the run exits failed with the failed wake, so the pickup collects it and the slot frees."""
        pkg = self.idle_package()
        (self.t / "rc").write_text("0")
        (self.t / "hang").touch()
        r = subprocess.run([sys.executable, str(pkg / "bin/pi-run"), "stuck", "coding", str(self.t / "brief.md")],
                           capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(wait_for(lambda: (self.run_json("stuck") or {}).get("state") == "exited", 120), self.run_json("stuck"))
        run = self.run_json("stuck")
        self.assertEqual((run["rc"], run["wake"]), (143, "failed"))
        self.assertEqual((self.t / "got-sig").read_text().strip(), "TERM")
        self.assertIn("pi-run: no progress for 1 min, past run.idle_min in config/policy.toml: stopping pi", self.log("stuck"))
        self.assertIn("stopped after 1 min with no progress (run.idle_min)", self.log("stuck"))

    def test_an_idle_stop_is_a_failure_even_when_pi_exits_0_on_term(self):
        pkg = self.idle_package()
        (self.t / "bin" / "pi").write_text(FAKE_PI.replace("trap 'echo TERM > \"$T/got-sig\"; exit 143' TERM",
                                                           "trap 'echo TERM > \"$T/got-sig\"; exit 0' TERM"))
        (self.t / "rc").write_text("0")
        (self.t / "hang").touch()
        (self.t / "w.md").write_text("task\nwake: finish\n")
        r = subprocess.run([sys.executable, str(pkg / "bin/pi-run"), "stuck0", "coding", str(self.t / "w.md")],
                           capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(wait_for(lambda: (self.run_json("stuck0") or {}).get("state") == "exited", 120), self.run_json("stuck0"))
        self.assertEqual((self.run_json("stuck0")["rc"], self.run_json("stuck0")["wake"]), (143, "failed"))

    def test_a_slow_run_that_keeps_writing_a_log_is_not_stopped(self):
        """A bench or a lume run that takes longer than the limit but appends to a log under its review dir is progress."""
        pkg = self.idle_package()
        (self.t / "bin" / "pi").write_text(FAKE_PI.replace('while [ -e "$T/hang" ]; do sleep 0.05; done',
                                                           'for i in $(seq 1 18); do echo "$i" >> bench/progress.log; sleep 5; done'
                                                           ).replace('echo $$ > "$T/pi-pid"', 'echo $$ > "$T/pi-pid"; mkdir -p bench'))
        (self.t / "rc").write_text("0")
        r = subprocess.run([sys.executable, str(pkg / "bin/pi-run"), "slow", "coding", str(self.t / "brief.md")],
                           capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(wait_for(lambda: (self.run_json("slow") or {}).get("state") == "exited", 150), self.run_json("slow"))
        self.assertEqual(self.run_json("slow")["rc"], 0)
        self.assertNotIn("no progress", self.log("slow"))

    def test_progress_ignores_cache_warming_and_counts_a_tool_call_inside_its_declared_timeout(self):
        """pi appends a cache_warm usage entry every few minutes while it waits, so the session file's mtime is no sign
        of work; a bash call still inside the timeout it declared is waiting on purpose (a CI loop, a lume run)."""
        d = self.t / "root" / "review" / "w"
        d.mkdir(parents=True)
        sess = self.t / ".pi" / "agent" / "sessions" / ("--" + str(d).strip("/").replace("/", "-") + "--")
        sess.mkdir(parents=True)
        f = sess / "2026-11-13T09-00-00-000Z_w.jsonl"
        call = lambda ts, **a: {"type": "message", "timestamp": ts, "message": {"role": "assistant", "content": [   # noqa: E731
            {"type": "toolCall", "id": "c1", "name": "bash", "arguments": {"command": "x", **a}}]}}
        warm = {"type": "usage", "kind": "cache_warm", "timestamp": "2026-11-13T09:50:00.000Z"}
        t0 = pr.calendar.timegm((2026, 11, 13, 9, 0, 0))
        env = {"PI_CODING_AGENT_DIR": str(self.t / ".pi" / "agent")}
        cases = [([call("2026-11-13T09:00:00.000Z"), warm], t0),
                 ([call("2026-11-13T09:00:00.000Z", timeout=3600), warm], t0 + 3600),
                 ([call("2026-11-13T09:00:00.000Z", timeout=3600),
                   {"type": "message", "timestamp": "2026-11-13T09:10:00.000Z", "message": {"role": "toolResult", "toolCallId": "c1"}}], t0 + 600)]
        old = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        os.environ.update(env)
        for entries, want in cases:
            with self.subTest(entries=entries):
                f.write_text("".join(json.dumps(e) + "\n" for e in entries))
                self.assertEqual(pr.session_progress(str(f)), want)
                self.assertEqual(pr.session_files(str(d), "w"), [str(f)])
        big = call("2026-11-13T09:00:00.000Z", timeout=3600)   # a tool call far larger than the tail read first
        big["message"]["content"][0]["arguments"]["command"] = "x" * 600000
        f.write_text(json.dumps(big) + "\n" + "".join(json.dumps(warm) + "\n" for _ in range(3)))
        self.assertEqual(pr.session_progress(str(f)), t0 + 3600)
        sub = self.t / ".pi" / "agent" / "sessions" / "subagents"
        sub.mkdir()
        for name in ("2026-11-13T09-40-00-000Z_w-verifier-AbC123.jsonl", "2026-11-13T09-40-00-000Z_w-designer-2-AbC123.jsonl",
                     "2026-11-13T09-40-00-000Z_w-x-verifier-AbC123.jsonl"):
            (sub / name).write_text(json.dumps({"type": "message", "timestamp": "2026-11-13T09:40:00.000Z", "message": {"role": "user"}}) + "\n")
        self.assertEqual(sorted(os.path.basename(p) for p in pr.session_files(str(d), "w")),
                         ["2026-11-13T09-00-00-000Z_w.jsonl", "2026-11-13T09-40-00-000Z_w-designer-2-AbC123.jsonl",
                          "2026-11-13T09-40-00-000Z_w-verifier-AbC123.jsonl"])

    def test_merge_allowed_comes_only_from_the_brief(self):
        (self.t / "m.md").write_text("task\nmerge: allowed\n")
        (self.t / "near.md").write_text("task\nmerge: allowed later\n")
        for sid, brief, env, want in (("r1", "m.md", {}, "1"), ("r2", "brief.md", {}, "0"),
                                      ("r3", "brief.md", {"ONIGIRI_READY_ALLOWED": "1"}, "0"), ("r4", "near.md", {}, "0")):
            self.start(sid, brief=brief, **env)
            self.finished(sid)
            self.assertEqual((self.t / "ready").read_text().strip(), want, sid)

    def test_an_upstream_429_requeues_with_no_wake_and_the_drain_continues_the_same_session(self):
        (self.t / "w.md").write_text("task\nwake: finish\n")
        (self.t / "say").write_text("Incomplete Claude Code upstream response (status 429, capture incomplete, upstream said: "
                                    "This request would exceed your account's rate limit. Please try again later.)\n")
        self.start("lim", rc=1, brief="w.md")
        self.assertTrue(wait_for(lambda: (self.run_json("lim") or {}).get("state") == "queued"), self.run_json("lim"))
        run = self.run_json("lim")
        self.assertEqual((run["attempt"], run["rc"], run["wake"]), (1, 1, None))
        self.assertNotIn("wake:", self.log("lim"))   # no failed wake
        self.assertEqual(self.pi_run("list", "queued").stdout, f"lim queued attempt=1 rc=1 since={run['since']} resume_at={run['resume_at']}\n")
        self.assertEqual(self.pi_run("list", "exited", "lost").stdout, "")   # nothing for the pickup to collect
        self.assertEqual(self.pi_run("collect", "lim").returncode, 3)
        r = self.pi_run("lim", "coding", str(self.t / "w.md"))   # a direct start never resumes it
        self.assertEqual(r.returncode, 3)
        self.assertIn("queued after a 429", r.stderr)
        self.assertEqual(self.pi_run("resume", "lim").returncode, 2)   # pi-run resume is gone
        (self.t / "say").unlink()
        (self.t / "rc").write_text("0")
        brief = (self.t / "root/review/lim/brief.full.md").read_text()
        r = self.pi_run("lim", "coding", str(self.t / "brief.md"), PI_RUN_DRAIN="1")   # what `bin/hourly recv --drain` runs
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("resumed lim attempt 1", r.stdout)
        run = self.finished("lim")
        self.assertEqual((run["attempt"], run["rc"], run["wake"], run["resumes"], run["wake_finish"]), (1, 0, "done", 1, True))
        args = (self.t / "args").read_text().splitlines()
        self.assertEqual(args[args.index("--session-id") + 1], "lim")
        self.assertEqual(args[args.index("--name") + 1], "role:coding")
        self.assertEqual(args[-1], pr.RESUME)
        self.assertEqual((self.t / "drain").read_text().strip(), "unset")   # the drain's mark never reaches the session
        self.assertEqual((self.t / "root/review/lim/brief.full.md").read_text(), brief)   # the first brief, kept
        self.assertEqual(self.log("lim").count('{"kind": "done"'), 1)

    def test_an_incomplete_upstream_response_requeues_like_a_429_with_no_wake(self):
        """The five 11 Nov failures (half-bounce-spaces, statechart-fixes, driver-macos-fixes, ci-fail-owner, ci-fail-owner-2)
        each passed when run again under a fresh id; these are their last lines, verbatim."""
        for sid, say in (("i1", "Incomplete Claude Code upstream response (status 200, capture incomplete, native retries denied 1)"),
                         ("i2", "Incomplete Claude Code upstream response (status 200, capture incomplete, relay failure Error, "
                                "native retries denied 1)"),
                         ("i3", "Incomplete Claude Code upstream response (status unknown, capture incomplete, relay failure Error)"),
                         ("i4", "Error: Incomplete Claude Code upstream response (status 529, capture incomplete)")):
            (self.t / "say").write_text(say + "\n")
            before = time.time()
            self.start(sid, rc=1)
            self.assertTrue(wait_for(lambda sid=sid: (self.run_json(sid) or {}).get("state") == "queued"), (sid, self.run_json(sid)))
            run = self.run_json(sid)
            self.assertEqual((run["attempt"], run["rc"], run["wake"]), (1, 1, None), sid)
            self.assertNotIn("wake:", self.log(sid), sid)
            at = time.mktime(time.strptime(run["resume_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
            self.assertLess(abs(at - before), 30, sid)   # resume at once: nothing names a wait
        (self.t / "say").unlink()
        (self.t / "args").unlink()
        r = self.pi_run("i1", "coding", str(self.t / "brief.md"), PI_RUN_DRAIN="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("resumed i1 attempt 1", r.stdout)
        self.assertEqual(self.finished("i1")["attempt"], 1)
        args = (self.t / "args").read_text().splitlines()
        self.assertEqual((args[args.index("--session-id") + 1], args[-1]), ("i1", pr.RESUME))

    def test_an_upstream_that_always_fails_stops_after_three_resumes_and_sends_the_failed_wake(self):
        """config/policy.toml resume.cap is 3: three resumes in the same session, then the fourth incomplete response ends
        the run failed, and its one wake names the cap, pi's last line and the log."""
        say = "Incomplete Claude Code upstream response (status 200, capture incomplete, native retries denied 1)"
        (self.t / "say").write_text(say + "\n")
        self.start("af", rc=1)
        for n in (1, 2, 3):
            self.assertTrue(wait_for(lambda: (self.run_json("af") or {}).get("state") == "queued"), self.run_json("af"))
            self.assertEqual((self.run_json("af")["limit_resumes"], self.run_json("af")["wake"]), (n, None))
            self.assertIn(f"back in the queue after an upstream limit ({n} of 3 resumes)", self.log("af"))
            self.assertNotIn('"kind"', self.log("af"))
            r = self.pi_run("af", "coding", str(self.t / "brief.md"), PI_RUN_DRAIN="1")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("resumed af attempt 1", r.stdout)
        run = self.finished("af")
        self.assertEqual((run["attempt"], run["rc"], run["wake"], run["resumes"], run["limit_resumes"]), (1, 1, "failed", 3, 3))
        wakes = [json.loads(x) for x in self.log("af").splitlines() if x.startswith("{")]
        self.assertEqual([(w["kind"], w["id"], w["rc"]) for w in wakes], [("failed", "af", 1)])
        self.assertIn("after 3 resumes, the resume.cap in config/policy.toml", wakes[0]["reason"])
        self.assertIn(say, wakes[0]["reason"])
        self.assertEqual(wakes[0]["report"], str(self.t / "root/review/af/run.log"))
        r = self.pi_run("af", "coding", str(self.t / "brief.md"), PI_RUN_DRAIN="1")
        self.assertEqual(r.returncode, 3)   # nothing resumes it again: the pickup collects it
        self.assertIn("not collected", r.stderr)
        self.assertEqual(self.pi_run("collect", "af").returncode, 0)
        self.assertEqual(self.run_json("af")["state"], "collected")

    def test_a_chatgpt_limit_resumes_after_the_wait_it_names_and_not_before(self):
        (self.t / "say").write_text("You have hit your ChatGPT usage limit (pro plan). Try again in ~42 min.\n")
        before = time.time()
        self.start("cx", rc=1)
        self.assertTrue(wait_for(lambda: (self.run_json("cx") or {}).get("state") == "queued"))
        at = time.mktime(time.strptime(self.run_json("cx")["resume_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        self.assertAlmostEqual(at - before, 42 * 60, delta=30)
        r = self.pi_run("cx", "coding", str(self.t / "brief.md"), PI_RUN_DRAIN="1")
        self.assertEqual(r.returncode, 3)
        self.assertIn("once that has passed", r.stderr)

    def cargo_target(self, sid, rel, mb=2):
        t = self.t / "root" / "review" / sid / rel
        (t / "debug").mkdir(parents=True)
        (t / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n")
        (t / "debug" / "blob").write_bytes(os.urandom(mb * 1_000_000))
        return t

    def test_collect_deletes_the_runs_idle_cargo_targets_keeps_held_ones_and_logs_the_gb(self):
        self.start("pr1")
        self.finished("pr1")
        d = self.t / "root" / "review" / "pr1"
        idle = self.cargo_target("pr1", "wt/libs/x/target", mb=3)
        idle2 = self.cargo_target("pr1", "wt2/target")
        held = self.cargo_target("pr1", "wt3/target")
        src = d / "wt" / "libs" / "x" / "src.rs"
        src.write_text("fn main() {}\n")
        notcargo = d / "wt" / "web" / "target"   # a dir named target that cargo did not make
        notcargo.mkdir(parents=True)
        outside = self.cargo_target("pr1", "scratch/target")   # not under a wt* worktree
        other = self.cargo_target("other", "wt/target")         # another run's
        (d / "wt4").symlink_to(self.t / "root" / "review" / "other" / "wt")   # a link out is never followed
        (d / "wt3" / "src").mkdir()
        holder = subprocess.Popen(["/bin/sh", "-c", "exec sleep 30"], cwd=d / "wt3" / "src")   # a live shell in that worktree
        self.addCleanup(holder.kill)
        r = self.pi_run("collect", "pr1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "pr1 attempt=1 rc=0\n")
        self.assertFalse(idle.exists() or idle2.exists())
        for kept in (held, src, notcargo, outside, other):
            self.assertTrue(kept.exists(), kept)
        line = next(x for x in r.stderr.splitlines() if x.startswith("pi-run: pr1: "))
        self.assertRegex(line, r"^pi-run: pr1: freed 0\.[0-9] GB: deleted wt/libs/x/target wt2/target; kept wt3/target ")
        self.assertIn("pi-run collect: freed ", self.log("pr1"))

    def test_collect_deletes_nothing_when_lsof_gives_no_answer_or_the_run_is_live(self):
        self.start("pr2", hang=True)
        t = self.cargo_target("pr2", "wt/target")
        self.assertEqual(self.pi_run("collect", "pr2").returncode, 3)   # running: refused, nothing touched
        self.assertTrue(t.exists())
        (self.t / "hang").unlink()
        self.finished("pr2")
        (self.t / "bin" / "lsof").write_text("#!/bin/sh\nexit 1\n")
        (self.t / "bin" / "lsof").chmod(0o755)
        r = self.pi_run("collect", "pr2", PATH=f"{self.t}/bin:/usr/bin:/bin")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(t.exists())
        self.assertIn("kept 1 target/ dir(s): lsof gave no answer", r.stderr)

    def test_a_worktree_entered_after_the_first_lsof_keeps_its_target(self):
        """The second lsof, after the target is renamed aside, sees a process that entered the worktree since the first."""
        self.start("pr3")
        self.finished("pr3")
        t = self.cargo_target("pr3", "wt/target")
        wt = os.path.realpath(t.parent)
        (self.t / "bin" / "lsof").write_text(f'#!/bin/sh\nn=$(cat "$T/lsof-n" 2>/dev/null || echo 0); echo $((n+1)) > "$T/lsof-n"\n'
                                             f'echo p1; [ "$n" -ge 1 ] && echo "n{wt}/src"; echo n/\n')
        (self.t / "bin" / "lsof").chmod(0o755)
        r = self.pi_run("collect", "pr3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((t / "CACHEDIR.TAG").exists())   # renamed back
        self.assertEqual(sorted(x.name for x in t.parent.iterdir()), ["target"])
        self.assertIn("freed 0.0 GB: deleted no target/ dir; kept wt/target", r.stderr)

    def test_a_build_started_after_the_second_lsof_gets_a_fresh_target_that_prune_never_deletes(self):
        """Once renamed aside, the old build is reachable only by its aside name: cargo started later makes a new target/."""
        self.start("pr5")
        self.finished("pr5")
        t = self.cargo_target("pr5", "wt/target")
        (self.t / "bin" / "lsof").write_text(   # the second snapshot is clean, then a build starts in the worktree
            '#!/bin/sh\nn=$(cat "$T/lsof-n" 2>/dev/null || echo 0); echo $((n+1)) > "$T/lsof-n"\n'
            f'[ "$n" -ge 1 ] && mkdir -p "{t}/debug" && echo new > "{t}/debug/fresh"\necho p1; echo n/\n')
        (self.t / "bin" / "lsof").chmod(0o755)
        r = self.pi_run("collect", "pr5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((t / "debug" / "fresh").read_text(), "new\n")   # the later build's target/ is untouched
        self.assertFalse((t / "debug" / "blob").exists())                 # the old build went
        self.assertEqual(sorted(x.name for x in t.parent.iterdir()), ["target"])

    def test_a_target_a_died_prune_left_aside_goes_at_the_next_collect(self):
        self.start("pr4")
        self.finished("pr4")
        left = self.cargo_target("pr4", "wt/.target.pruning-123")
        self.pi_run("collect", "pr4")
        self.assertFalse(left.exists())

    def test_a_lost_runs_collect_sends_one_failed_wake(self):
        self.start("gone", hang=True)
        wait_for(lambda: (self.t / "pi-pid").exists())
        os.killpg(self.run_json("gone")["pid"], signal.SIGKILL)
        self.assertTrue(wait_for(lambda: self.pi_run("list").stdout == "gone lost attempt=1 rc=None\n"))
        r = self.pi_run("collect", "gone")
        self.assertEqual(r.stdout, "gone attempt=1 lost\n")   # the hourly reads this line; the wake goes to stderr
        wakes = [json.loads(x) for x in r.stderr.splitlines() if x.startswith("{")]
        self.assertEqual([(w["kind"], w["id"]) for w in wakes], [("failed", "gone")])
        self.assertIn("was lost (KILL or OOM)", wakes[0]["reason"])
        self.assertEqual(self.run_json("gone")["wake"], "failed")
        self.assertEqual(self.pi_run("collect", "gone").returncode, 3)   # once

    def test_a_run_that_only_quotes_a_429_is_a_plain_failure(self):
        for sid, say in (("q1", "Incomplete Claude Code upstream response (status 429, x)\nreport done\n"),
                         ("q2", "Task failed; quoted: Incomplete Claude Code upstream response (status 429, example)\n"),
                         ("q3", "Incomplete Claude Code upstream response (status 400, upstream said: You're out of extra usage.)\n"),
                         ("q4", "Incomplete Claude Code response: assistant, message_stop and one result required\n")):
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

    def test_queue_is_a_run_state_in_list_oldest_first_and_a_start_takes_it(self):
        for sid in ("qa", "qb"):
            d = self.t / "root" / "review" / sid
            d.mkdir(parents=True)
            (d / "brief.md").write_text("task\nrole: research\n")
            self.assertEqual(self.pi_run("queue", sid).returncode, 0)
            time.sleep(1.1)
        self.assertEqual((self.run_json("qa")["state"], self.run_json("qa")["role"]), ("queued", "research"))
        self.assertFalse((self.t / "root/review/qa/QUEUED").exists())
        lines = self.pi_run("list", "queued").stdout.splitlines()
        self.assertEqual([x.split()[:3] for x in lines], [["qa", "queued", "attempt=0"], ["qb", "queued", "attempt=0"]])
        self.assertRegex(lines[0], r" since=\d{4}-\d\d-\d\dT")
        since = self.run_json("qa")["since"]
        self.start("qa", brief="root/review/qa/brief.md")
        self.assertEqual((self.finished("qa")["attempt"], self.run_json("qa")["since"]), (1, since))
        self.assertEqual(self.pi_run("list", "queued").stdout.split()[:1], ["qb"])
        self.assertEqual(self.pi_run("queue", "nobrief").returncode, 2)
        self.pi_run("collect", "qa")
        r = self.pi_run("qa", "coding", str(self.t / "brief.md"))   # a start after collected: a retry is a fresh id
        self.assertEqual((r.returncode, r.stderr.strip().splitlines()[-1]), (3, "pi-run: qa: attempt 1 was collected; a retry is a fresh id"))

    def test_a_paused_run_from_the_old_code_becomes_a_queued_continuation(self):
        d = self.t / "root" / "review" / "old-pause"
        d.mkdir(parents=True)
        (d / "brief.full.md").write_text("task\n")
        (d / "run.json").write_text(json.dumps({**Step.R(Step(), "paused", rc=1, start="Tue Nov 10 04:53:24 2026"), "pid": 99999999,
                                                "resume_at": "2026-11-10T06:00:00Z", "role": "coding", "extra": []}))
        self.assertEqual(self.pi_run("list").stdout,
                         "old-pause queued attempt=1 rc=1 since=2026-11-10T04:53:24Z resume_at=2026-11-10T06:00:00Z\n")
        self.assertEqual(self.pi_run("old-pause", "coding", str(self.t / "brief.md")).returncode, 3)   # only the drain resumes it
        r = self.pi_run("old-pause", "coding", str(self.t / "brief.md"), PI_RUN_DRAIN="1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("resumed old-pause attempt 1", r.stdout)

    def test_a_drain_that_set_up_a_fresh_start_refuses_a_run_that_became_a_continuation_meanwhile(self):
        d = self.t / "root" / "review" / "race"
        d.mkdir(parents=True)
        q = {"state": "queued", "attempt": 0, "pid": None, "start": None, "rc": None, "wake_finish": False, "wake": None,
             "resume_at": None, "since": "2026-11-10T01:00:00Z", "role": "coding"}
        (d / "run.json").write_text(json.dumps(q))
        # the fake pi's --list-models is the setup window: another launcher's run hits a 429 there and is queued again
        cont = {**q, "attempt": 1, "rc": 1, "resume_at": "2026-11-10T02:00:00Z", "extra": []}
        swap = f'echo {json.dumps(json.dumps(cont))} > "{d}/run.json";'
        (self.t / "bin" / "pi").write_text(FAKE_PI.replace('if [ "$1" = --list-models ]; then', f'if [ "$1" = --list-models ]; then {swap}'))
        r = self.pi_run("race", "coding", str(self.t / "brief.md"), PI_RUN_DRAIN="1")
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("its state changed during setup; nothing started", r.stderr)
        self.assertEqual(self.run_json("race"), cont)
        self.assertFalse((d / "brief.full.md").exists())

    def test_a_resume_uses_the_role_it_started_with(self):
        (self.t / "say").write_text("Incomplete Claude Code upstream response (status 429, capture incomplete)\n")
        self.start("rr", rc=1)
        self.assertTrue(wait_for(lambda: (self.run_json("rr") or {}).get("state") == "queued"))
        run = self.run_json("rr")
        (self.t / "root/review/rr/run.json").write_text(json.dumps({**run, "resume_at": "2000-01-01T00:00:00Z"}))
        (self.t / "say").unlink()
        r = self.pi_run("rr", "verifier", str(self.t / "brief.md"), PI_RUN_DRAIN="1")   # the brief's role changed since
        self.assertEqual(r.returncode, 0, r.stderr)   # verifier's provider has no credential here: coding's is used
        self.assertIn("as role coding: claude-code", r.stdout)

    def test_a_live_queued_marker_becomes_a_queued_run_with_its_place(self):
        """The QUEUED files before run.json had the queue: migrated, not deleted (ci-times-72h and lint-audit, 10 Nov)."""
        for sid, mark in (("old", ""), ("rq", "research\n")):
            d = self.t / "root" / "review" / sid
            d.mkdir(parents=True)
            (d / "brief.md").write_text("task\nrole: coding\n")
            (d / "QUEUED").write_text(mark)
            os.utime(d / "QUEUED", (1762743960, 1762743960))   # 2025-11-10T03:06:00Z
        done = self.t / "root" / "review" / "done"   # a marker beside a run that already started is left alone
        done.mkdir()
        (done / "QUEUED").write_text("")
        (done / "run.json").write_text(json.dumps(Step.R(Step(), "collected", rc=0)))
        self.assertEqual(self.pi_run("list").stdout.splitlines(), ["done collected attempt=1 rc=0",
                                                                    "old queued attempt=0 rc=None since=2025-11-10T03:06:00Z",
                                                                    "rq queued attempt=0 rc=None since=2025-11-10T03:06:00Z"])
        self.assertEqual((self.run_json("old")["role"], self.run_json("rq")["role"]), ("coding", "research"))
        self.assertFalse((self.t / "root/review/old/QUEUED").exists())
        self.assertTrue((done / "QUEUED").exists())
        self.assertEqual(self.pi_run("list").stdout.count("queued"), 2)   # twice: the same

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
        r = self.pi_run("nokey", "verifier", str(self.t / "brief.md"), PI_RUN_DRY="1")   # the role's own provider is checked
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("no openai-codex credential", r.stderr)
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
