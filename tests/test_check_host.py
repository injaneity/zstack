"""Tests for bin/check-host against a fake pi, gh and claude in an empty HOME: each missing dependency is one FAIL line
naming its fix and the exit is 1; with everything present no dependency line fails. check-host runs in process, with its
pi-claude-code pin set to a temp checkout's HEAD, since no temp repo can have the real pinned commit.
Run: python3 -m unittest discover -s tests -p 'test_*.py'"""
import importlib.machinery
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

PKG = Path(__file__).resolve().parent.parent
# fake pi: `pi list` prints $T/packages, `--list-models` prints $T/models, `-p` prints $T/answer-<provider> (exit 0) or fails.
FAKE_PI = """#!/bin/bash
case "$1" in
  list) cat "$T/packages" 2>/dev/null; exit 0;;
  --list-models) echo "provider model"; cat "$T/models" 2>/dev/null; exit 0;;
esac
while [ $# -gt 0 ]; do [ "$1" = --provider ] && p=$2; shift; done
[ -e "$T/answer-$p" ] || { echo "Error: refused" >&2; exit 1; }
cat "$T/answer-$p"
"""
FAKE_GH = '#!/bin/bash\n[ -e "$T/gh-ok" ] && exit 0\necho "You are not logged into any GitHub hosts." >&2; exit 1\n'
MODELS = "claude-code claude-opus-5-5\nopenai-codex gpt-6.1-terra\n"


def load():
    loader = importlib.machinery.SourceFileLoader("check_host", str(PKG / "bin" / "check-host"))
    ch = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(ch)
    return ch


class CheckHost(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.t = t = Path(td.name)
        (t / "bin").mkdir()
        for name, text in (("pi", FAKE_PI), ("gh", FAKE_GH), ("claude", "#!/bin/sh\n"), ("node", "#!/bin/sh\n")):
            (t / "bin" / name).write_text(text)
            (t / "bin" / name).chmod(0o755)
        self.env = {"PATH": f"{t}/bin:/usr/bin:/bin", "HOME": str(t), "T": str(t), "ONIGIRI_HOST": "",
                    "ONIGIRI_ROOT": str(t / "root"), "PI_CODING_AGENT_DIR": str(t / "agent")}
        # the pi-claude-code checkout `pi list` names: a real git repo, so its HEAD is a real sha
        self.claude = t / "claude-pkg"
        subprocess.run(["git", "init", "-q", str(self.claude)], check=True)
        subprocess.run(["git", "-C", str(self.claude), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"],
                       check=True)
        self.pin = subprocess.run(["git", "-C", str(self.claude), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def check(self, *args, pin=None, run=None):
        """(exit, stdout lines) of check-host's main(args), parity lines dropped (this temp host has no cua clone)."""
        ch = load()
        out = io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), patch.object(ch, "CLAUDE_PIN", pin or self.pin), \
                patch.object(ch, "run", run(ch.run) if run else ch.run), redirect_stdout(out), redirect_stderr(io.StringIO()):
            rc = ch.main(list(args))
        return rc, [x for x in out.getvalue().splitlines() if not x.startswith("FAIL  parity ")]

    def ready(self):
        (self.t / "packages").write_text(f"User packages:\n  git:github.com/injaneity/zstack\n    /z\n"
                                         f"  git:github.com/earendil-works/pi-claude-code@{self.pin}\n    {self.claude}\n"
                                         "  npm:@ff-labs/pi-fff@0.11.0\n    /f\n")
        (self.t / "models").write_text(MODELS)
        (self.t / "gh-ok").touch()
        for p in ("claude-code", "openai-codex"):
            (self.t / f"answer-{p}").write_text("ok\n")

    def fails(self, lines):
        return [x for x in lines if x.startswith("FAIL")]

    def test_an_empty_host_fails_each_missing_dependency_with_its_fix(self):
        rc, lines = self.check()
        self.assertEqual(rc, 1)
        text = "\n".join(self.fails(lines))
        for want in ("pi install npm:@ff-labs/pi-fff@0.11.0", "pi install git:github.com/earendil-works/pi-claude-code@",
                     "pi lists no claude-code model", "pi lists no openai-codex model", "gh auth login"):
            self.assertIn(want, text)
        self.assertIn("check-host: ", lines[-1])

    def test_a_ready_host_passes_offline_but_parity_is_still_checked(self):
        self.ready()
        rc, lines = self.check("--offline")
        self.assertEqual(self.fails(lines), [], lines)
        self.assertIn(f"ok    package pi-claude-code revision: the pinned {self.pin[:9]}", lines)
        self.assertFalse(any(x.startswith("ok    live") for x in lines))
        self.assertEqual(rc, 1)   # this temp host has no cua clone, so check-parity's hook line fails it

    def test_pi_claude_code_at_another_commit_fails_even_offline(self):
        """Its main after d070212 gets every Claude request refused as third-party usage, while pi still lists the models."""
        self.ready()
        _, lines = self.check("--offline", pin="d070212" + "0" * 33)
        self.assertTrue(any(x.startswith(f"FAIL  package pi-claude-code revision: {self.pin[:9]}") and "not the pinned d0702120" in x
                            for x in lines), lines)

    def test_a_listed_provider_that_refuses_a_real_call_fails(self):
        """The 11 Nov fresh run: pi listed claude-code models and every request came back as a billing refusal."""
        self.ready()
        os.unlink(self.t / "answer-claude-code")
        _, lines = self.check()
        self.assertTrue(any(x.startswith("FAIL  live claude-code claude-opus-5-5") and "refused" in x for x in lines), lines)
        self.assertTrue(any(x.startswith("ok    live openai-codex") for x in lines), lines)

    def test_claude_code_needs_the_claude_cli_on_path(self):
        self.ready()
        os.unlink(self.t / "bin" / "claude")
        _, lines = self.check("--offline")
        self.assertTrue(any("`claude` CLI is not on PATH" in x for x in self.fails(lines)), lines)

    def test_the_merge_host_needs_the_holds_file(self):
        self.ready()
        host = PKG / "hosts" / "checkhost-test"
        host.mkdir()
        self.addCleanup(lambda: (host / "host.sh").unlink() or host.rmdir())
        (host / "host.sh").write_text("export ONIGIRI_MERGE_HOST=checkhost-test\n")
        self.env["ONIGIRI_HOST"] = "checkhost-test"
        _, lines = self.check("--offline")
        self.assertTrue(any(x.startswith("FAIL  holds:") for x in lines), lines)
        (self.t / "root").mkdir()
        (self.t / "root" / "hold-prs.txt").write_text("")
        _, lines = self.check("--offline")
        self.assertTrue(any(x.startswith("ok    holds:") for x in lines), lines)

    def test_a_parity_check_that_dies_without_output_fails(self):
        """check-parity that cannot run (killed, timed out) prints nothing; that is a FAIL, not a pass."""
        self.ready()
        dead = lambda real: lambda cmd, env, **kw: (subprocess.CompletedProcess(cmd, 9, "", "")  # noqa: E731
                                                    if cmd[0].endswith("check-parity") else real(cmd, env, **kw))
        rc, lines = self.check("--offline", run=dead)
        self.assertEqual(rc, 1)
        self.assertIn("FAIL  parity: hosts/check-parity exited 9 without a FAIL line: exit 9", lines)

    def test_a_bad_argument_is_usage(self):
        r = subprocess.run([str(PKG / "bin" / "check-host"), "--nope"], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
