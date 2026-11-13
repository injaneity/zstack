import importlib.util
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("comments", PKG / "tests" / "comments.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class Comments(unittest.TestCase):
    def test_the_package_code_has_none(self):
        found = [f"{f}:{n}: {w[:80]}" for f in c.files() for n, w in c.strip(f)[1]]
        self.assertEqual(found, [])

    def test_python_loses_comments_and_docstrings_and_keeps_shebangs_directives_and_strings(self):
        src = ('#!/usr/bin/env python3\n"""mod doc"""\nimport os  # noqa: F401\n\n\ndef f():\n    """doc"""\n'
               '    x = "# not a comment"  # a comment\n    # a line\n    return x\n')
        new, hits = c.py(src)
        self.assertEqual(new, '#!/usr/bin/env python3\nimport os  # noqa: F401\n\n\ndef f():\n    x = "# not a comment"\n    return x\n')
        self.assertEqual(len(hits), 4)

    def test_shell_keeps_quoted_hashes_heredocs_and_shellcheck_lines(self):
        src = ("#!/bin/bash\n# shellcheck disable=SC1091\n# a comment\necho \"a # b\" 'c # d' ${x#y} # tail\n"
               "cat <<EOF\n# data\nEOF\n")
        new, hits = c.sh(src)
        self.assertEqual(new, "#!/bin/bash\n# shellcheck disable=SC1091\necho \"a # b\" 'c # d' ${x#y}\ncat <<EOF\n# data\nEOF\n")
        self.assertEqual([n for n, _ in hits], [3, 4])

    def test_typescript_keeps_urls_regexes_and_template_strings(self):
        src = ('// head\nconst u = "http://x//y"; // tail\nconst r = /a\\/\\/b/g;\n/** doc\n * more */\n'
               'const t = `${a /* in */}//x`;\n')
        new, hits = c.c_like(src)
        self.assertEqual(new, 'const u = "http://x//y";\nconst r = /a\\/\\/b/g;\nconst t = `${a /* in */}//x`;\n')
        self.assertEqual([n for n, _ in hits], [1, 2, 4])

    def test_tla_primes_are_not_quotes(self):
        src = '---- MODULE M ----\n\\* head\nA == x\' = "a \\\\* b" /\\ y\' = 1   \\* tail\n(* block\n *)\n====\n'
        new, hits = c.c_like(src, tla=True)
        self.assertEqual(new, '---- MODULE M ----\nA == x\' = "a \\\\* b" /\\ y\' = 1\n====\n')
        self.assertEqual([n for n, _ in hits], [2, 3, 4])


if __name__ == "__main__":
    unittest.main()
