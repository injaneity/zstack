import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

for _k in ("ZSTACK_CONFIG", "ZSTACK_MACHINE", "ZSTACK_REPO"):
    os.environ.pop(_k, None)

spec = importlib.util.spec_from_file_location("queue_wake", Path(__file__).resolve().parent.parent / "queue.py")
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)


class R:
    status = 200
    __enter__ = lambda self: self   # noqa: E731
    __exit__ = lambda self, *a: None   # noqa: E731


class Wake(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.key = Path(td.name, "k")
        self.pol = {"wake": {"url": "https://example.invalid/wake", "key_file": str(self.key)}}
        self.sent = []
        for p in (patch.object(q.time, "sleep", lambda _: None), patch.dict(os.environ)):
            p.start()
            self.addCleanup(p.stop)
        for k in ("ONIGIRI_WAKE_DRY", q.THREAD_VAR):
            os.environ.pop(k, None)

    def urlopen(self, *results):
        results = list(results)

        def f(req, timeout):
            self.sent.append((req.full_url, req.get_header("Authorization"), json.loads(req.data), timeout))
            r = results.pop(0)
            if isinstance(r, Exception):
                raise r
            return R()
        return patch.object(q.urllib.request, "urlopen", f)

    def test_a_wake_posts_the_kind_the_message_the_pr_and_the_thread(self):
        self.key.write_text("sekrit\n")
        os.environ[q.THREAD_VAR] = "t-1"
        with self.urlopen(None, None):
            said = q.wake(self.pol, "blocked", "app-1: PR 31 waits for the owner", 31)
            q.wake(self.pol, "done", "app-2: nothing to merge")
        url, auth, body, timeout = self.sent[0]
        self.assertEqual((url, auth, said), ("https://example.invalid/wake", "Bearer sekrit", "webhook sent (200)"))
        self.assertLessEqual(timeout, 10)
        self.assertEqual({k: body[k] for k in ("kind", "reason", "id", "pr")},
                         {"kind": "blocked", "reason": "app-1: PR 31 waits for the owner", "id": "t-1", "pr": 31})
        self.assertEqual((self.sent[1][2]["kind"], self.sent[1][2]["pr"]), ("done", None))

    def test_retries_network_errors_and_5xx_but_not_4xx(self):
        self.key.write_text("k")
        with self.urlopen(OSError("down"), urllib.error.HTTPError("u", 502, "bad", {}, None), None):
            self.assertEqual((q.wake(self.pol, "blocked", "x"), len(self.sent)), ("webhook sent (200)", 3))
        self.sent.clear()
        with self.urlopen(urllib.error.HTTPError("u", 401, "no", {}, None)):
            self.assertEqual((q.wake(self.pol, "blocked", "x"), len(self.sent)), ("webhook failed: HTTP 401", 1))
        self.sent.clear()
        with self.urlopen(OSError(), OSError(), OSError()):
            self.assertEqual((q.wake(self.pol, "blocked", "x"), len(self.sent)), ("webhook failed: OSError", 3))

    def test_no_key_skips_without_a_post(self):
        with self.urlopen():
            self.assertEqual((q.wake(self.pol, "blocked", "which branch?"), self.sent), ("webhook not configured", []))

    def test_dry_prints_the_body_and_posts_nothing(self):
        self.key.write_text("sekrit")
        os.environ["ONIGIRI_WAKE_DRY"] = "1"
        err = io.StringIO()
        with self.urlopen(), contextlib.redirect_stderr(err):
            self.assertEqual((q.wake(self.pol, "done", "x", 9), self.sent), ("not sent: dry run", []))
        self.assertEqual((json.loads(err.getvalue())["kind"], json.loads(err.getvalue())["pr"]), ("done", 9))
        self.assertNotIn("sekrit", err.getvalue())


if __name__ == "__main__":
    unittest.main()
