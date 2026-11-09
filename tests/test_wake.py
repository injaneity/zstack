"""Tests for hosts/vm/wake.py, the pi event sender. No test reaches the network.
Run: python3 -m unittest discover -s tests -p 'test_*.py'"""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

VM = Path(__file__).resolve().parent.parent / "hosts" / "vm"
spec = importlib.util.spec_from_file_location("wake_under_test", VM / "wake.py")
wake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wake)


class R:
    status = 200
    __enter__ = lambda self: self   # noqa: E731
    __exit__ = lambda self, *a: None   # noqa: E731


def run(*argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), patch.dict(os.environ, env or {}, clear=False):
        code = wake.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class Wake(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.key = Path(td.name, "k")
        k = patch.object(wake, "KEY_FILE", str(self.key))
        k.start()
        self.addCleanup(k.stop)
        s = patch.object(wake.time, "sleep", lambda _: None)
        s.start()
        self.addCleanup(s.stop)
        self.sent = []
        e = patch.dict(os.environ)
        e.start()
        self.addCleanup(e.stop)
        for k in ("ONIGIRI_WAKE_DRY", "PI_SESSION_ID"):
            os.environ.pop(k, None)

    def urlopen(self, *results):
        results = list(results)

        def f(req, timeout):
            self.sent.append((req.get_header("Authorization"), json.loads(req.data), timeout))
            r = results.pop(0)
            if isinstance(r, Exception):
                raise r
            return R()
        return patch.object(wake.urllib.request, "urlopen", f)

    def test_blocked_and_failed_post_the_message_pr_and_session(self):
        self.key.write_text("sekrit\n")
        with self.urlopen(None, None):
            code, out, err = run("blocked", "need a key on the mini", "pr=31", env={"PI_SESSION_ID": "cua-1"})
            run("failed", "cua-2 exited rc=3", env={"PI_SESSION_ID": "cua-2"})
        self.assertEqual(code, 0)
        auth, body, timeout = self.sent[0]
        self.assertEqual(auth, "Bearer sekrit")
        self.assertLessEqual(timeout, 10)
        self.assertEqual({k: body[k] for k in ("kind", "reason", "id", "pr", "source")},
                         {"kind": "blocked", "reason": "need a key on the mini", "id": "cua-1", "pr": 31, "source": "pi"})
        self.assertEqual((self.sent[1][1]["kind"], self.sent[1][1]["id"], self.sent[1][1]["pr"]), ("failed", "cua-2", None))
        self.assertEqual(err.strip(), "wake: blocked webhook sent (200)")
        self.assertNotIn("sekrit", out + err)

    def test_done_carries_rc_report_and_pr_links(self):
        self.key.write_text("k")
        with self.urlopen(None):
            run("done", "w exited rc=0", "rc=0", "report=/r/w/run.log", "prs=https://github.com/o/r/pull/1,https://github.com/o/r/pull/2",
                env={"PI_SESSION_ID": "w"})
        b = self.sent[0][1]
        self.assertEqual((b["kind"], b["id"], b["rc"], b["report"], b["prs"]),
                         ("done", "w", 0, "/r/w/run.log", ["https://github.com/o/r/pull/1", "https://github.com/o/r/pull/2"]))

    def test_retries_network_errors_and_5xx_but_not_4xx_and_always_exits_0(self):
        self.key.write_text("k")
        with self.urlopen(OSError("down"), urllib.error.HTTPError("u", 502, "bad", {}, None), None):
            code, _, err = run("failed", "build broke")
        self.assertEqual((code, len(self.sent)), (0, 3))
        self.assertIn("webhook sent (200)", err)
        self.sent.clear()
        with self.urlopen(urllib.error.HTTPError("u", 401, "no", {}, None)):
            code, _, err = run("failed", "build broke")
        self.assertEqual((code, len(self.sent)), (0, 1))
        self.assertIn("webhook failed: HTTP 401", err)
        self.sent.clear()
        with self.urlopen(OSError(), OSError(), OSError()):
            code, _, err = run("failed", "build broke")
        self.assertEqual((code, len(self.sent)), (0, 3))
        self.assertIn("webhook failed: OSError", err)

    def test_no_key_skips_without_a_post(self):
        with self.urlopen():
            code, _, err = run("blocked", "which branch?")
        self.assertEqual((code, self.sent), (0, []))
        self.assertEqual(err.strip(), "wake: blocked webhook not configured")

    def test_dry_prints_the_body_and_posts_nothing(self):
        self.key.write_text("sekrit")
        with self.urlopen():
            code, out, err = run("failed", "https://github.com/o/r/pull/9", "pr=9", env={"ONIGIRI_WAKE_DRY": "1"})
        self.assertEqual((code, self.sent), (0, []))
        self.assertEqual((json.loads(out)["kind"], json.loads(out)["pr"]), ("failed", 9))
        self.assertIn("not sent: dry run", err)
        self.assertNotIn("sekrit", out + err)

    def test_other_kinds_and_bad_usage_exit_2_and_post_nothing(self):
        self.key.write_text("k")
        with self.urlopen():
            for argv in ([], ["blocked"], ["pr-opened", "x"], ["question", "which branch?"], ["pi-done", "x"], ["Blocked", "x"],
                         ["blocked", " "], ["blocked", "x", "repo=y"], ["blocked", "x", "pr=x"], ["done", "x", "rc=a"], ["done", "x", "pr=1", "pr"],
                         ["--exit", "0", "pi-a"]):
                self.assertEqual(run(*argv)[0], 2, argv)
        self.assertEqual(self.sent, [])

    def test_runs_as_a_script_under_isolated_python(self):
        r = subprocess.run([sys.executable, "-I", str(VM / "wake.py"), "blocked", "x"], capture_output=True, text=True,
                           env={"ONIGIRI_WAKE_DRY": "1", "PATH": "/usr/bin:/bin", "HOME": str(self.key.parent)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["kind"], "blocked")


if __name__ == "__main__":
    unittest.main()
