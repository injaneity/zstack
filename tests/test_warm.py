import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

for _k in ("ZSTACK_CONFIG", "ZSTACK_MACHINE", "ZSTACK_REPO", "ONIGIRI_ROOT"):
    os.environ.pop(_k, None)

PKG = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("queue_under_test", PKG / "queue.py")
queue = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(queue)
BASE = queue.load()
SHARED = "shared from the clone copy-on-write, as they were there: "
BACK = "it is a Python virtualenv or a checkout, whose files point back at where they are"


def clones(where):
    (where / "a").write_text("x")
    try:
        queue.share(str(where / "a"), str(where / "b"))
    except OSError:
        return False
    return (where / "b").read_text() == "x"


class Warm(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.root = Path(os.path.realpath(td.name))
        self.clone, self.tree = self.root / "clone", self.root / "tree"
        self.git("init", "-q", str(self.clone), cwd=self.root)
        (self.clone / ".gitignore").write_text("node_modules/\nweb/dist/\n-cache*\n")
        (self.clone / "web").mkdir()
        (self.clone / "web" / "app.js").write_text("app")
        self.git("add", ".gitignore", "web/app.js", cwd=self.clone)
        self.git("commit", "-q", "-m", "one", cwd=self.clone)
        for d in ("node_modules/pkg", "web/dist"):
            (self.clone / d).mkdir(parents=True)
            (self.clone / d / "index.js").write_text(d)
        self.git("worktree", "add", "-q", "-b", "work", str(self.tree), cwd=self.clone)
        self.pol = json.loads(json.dumps(BASE))
        self.pol["host"]["clone"] = str(self.clone)
        self.copied = []

    def git(self, *a, cwd):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True)

    def warm(self, cwd, share=None):
        def copy(src, dst):
            self.copied.append(os.path.relpath(dst, self.tree))
            subprocess.run(["cp", "-R", src, dst], check=True)
        back = os.getcwd()
        os.chdir(cwd)
        try:
            with patch.object(queue, "share", share or copy):
                return queue.warm(self.pol)
        finally:
            os.chdir(back)

    def test_a_new_worktree_gets_what_git_ignores_in_the_clone_and_is_told_what(self):
        self.assertEqual(self.warm(self.tree / "web"), [SHARED + "node_modules, web/dist"])
        self.assertEqual([(self.tree / d / "index.js").read_text() for d in ("node_modules/pkg", "web/dist")], ["node_modules/pkg", "web/dist"])
        (self.tree / "web" / "dist" / "index.js").write_text("mine")
        self.assertEqual((self.warm(self.tree), (self.tree / "web" / "dist" / "index.js").read_text()), ([], "mine"))
        self.assertEqual((self.clone / "web" / "dist" / "index.js").read_text(), "web/dist")
        self.assertEqual(subprocess.run(["git", "status", "--porcelain"], cwd=self.tree, capture_output=True, text=True).stdout, "")

    def test_nothing_is_shared_into_the_clone_itself_or_another_repo(self):
        other = self.root / "other"
        self.git("init", "-q", str(other), cwd=self.root)
        self.git("commit", "-q", "--allow-empty", "-m", "one", cwd=other)
        self.git("worktree", "add", "-q", "-b", "work", str(self.root / "other-tree"), cwd=other)
        for cwd in (self.clone, self.root / "other-tree", self.root):
            self.assertEqual(self.warm(cwd), [], cwd)
        self.assertEqual(self.copied, [])

    def test_a_clone_that_is_itself_a_linked_worktree_warms_its_siblings(self):
        (self.tree / "node_modules").mkdir()
        (self.tree / "node_modules" / "mine.js").write_text("tree")
        self.pol["host"]["clone"] = str(self.tree)
        self.git("worktree", "add", "-q", "-b", "next", str(self.root / "next"), cwd=self.clone)
        self.assertEqual(self.warm(self.root / "next"), [SHARED + "node_modules"])
        self.assertEqual(os.listdir(self.root / "next" / "node_modules"), ["mine.js"])

    def test_what_points_back_at_the_clone_or_is_not_ignored_here_is_left_out(self):
        (self.clone / ".git" / "info" / "exclude").write_text(".venv/\nvendored/\nlocal.txt\n")
        for d, mark in ((".venv", "pyvenv.cfg"), ("vendored", ".git")):
            (self.clone / d).mkdir()
            (self.clone / d / mark).write_text("x")
        (self.clone / "local.txt").write_text("x")
        (self.clone / "-cache").write_text("c")
        (self.tree / ".gitignore").write_text("node_modules/\n-cache*\n")
        self.assertEqual(self.warm(self.tree), [SHARED + "-cache, local.txt, node_modules", f".venv is not shared from the clone: {BACK}",
                                                f"vendored is not shared from the clone: {BACK}",
                                                "web/dist is not shared from the clone: git does not ignore it here"])
        self.assertEqual((self.copied, (self.tree / "-cache").read_text()), (["-cache", "local.txt", "node_modules"], "c"))

    def test_a_link_on_the_way_never_sends_the_share_elsewhere(self):
        (self.root / "elsewhere").mkdir()
        (self.clone / "deep" / "cache").mkdir(parents=True)
        (self.clone / ".git" / "info" / "exclude").write_text("deep/\n")
        (self.tree / "deep").symlink_to(self.root / "elsewhere")
        self.assertEqual(self.warm(self.tree), [SHARED + "node_modules, web/dist"])
        with patch.object(queue, "git", lambda *a, **k: (0, "deep/cache/\0")):
            self.assertEqual(self.warm(self.tree), ["deep/cache is not shared from the clone: a link on the way to it leads elsewhere"])
        self.assertEqual(os.listdir(self.root / "elsewhere"), [])

    def test_a_long_list_is_cut_short(self):
        for i in range(25):
            (self.clone / f"-cache{i:02}").write_text("c")
        said = self.warm(self.tree)
        self.assertEqual((len(said), said[0].endswith("-cache19 and 7 more"), len(self.copied)), (1, True, 27))

    def test_a_path_that_cannot_be_cloned_by_reference_is_left_out_and_said_never_copied(self):
        def refuse(src, dst):
            raise OSError("Operation not supported")
        self.assertEqual(self.warm(self.tree, refuse),
                         [f"{p} is not shared from the clone: Operation not supported" for p in ("node_modules", "web/dist")])
        self.assertFalse((self.tree / "node_modules").exists() or (self.tree / "web" / "dist").exists())

    def test_share_clones_a_folder_by_reference_or_fails(self):
        if not clones(self.root):
            self.skipTest("this filesystem cannot clone by reference")
        queue.share(str(self.clone / "node_modules"), str(self.root / "copy"))
        self.assertEqual((self.root / "copy" / "pkg" / "index.js").read_text(), "node_modules/pkg")
        (self.root / "copy" / "pkg" / "index.js").write_text("changed")
        self.assertEqual((self.clone / "node_modules" / "pkg" / "index.js").read_text(), "node_modules/pkg")
        for src, dst in ((self.clone / "node_modules", self.root / "copy"), (self.clone / "gone", self.root / "new")):
            with self.assertRaises(OSError):
                queue.share(str(src), str(dst))
        self.assertFalse((self.root / "new").exists())
        (self.root / "link").symlink_to(self.clone / "node_modules")
        queue.share(str(self.root / "link"), str(self.root / "link-copy"))
        self.assertTrue((self.root / "link-copy").is_symlink())

    def test_the_command_prints_what_it_did_for_the_thread(self):
        if not clones(self.root):
            self.skipTest("this filesystem cannot clone by reference")
        back = os.getcwd()
        os.chdir(self.tree)
        try:
            with patch.object(queue, "load", return_value=self.pol), patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(queue.main(["warm"]), 0)
        finally:
            os.chdir(back)
        self.assertEqual(out.getvalue(), f"zstack: {SHARED}node_modules, web/dist\n")
        self.assertEqual((self.tree / "node_modules" / "pkg" / "index.js").read_text(), "node_modules/pkg")


if __name__ == "__main__":
    unittest.main()
