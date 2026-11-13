#!/usr/bin/env python3
import ast
import io
import os
import re
import subprocess
import sys
import tokenize

PKG = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
ROOTS = ("bin/", "hosts/", "extensions/", "model/", "tests/", ".github/")
KEEP = re.compile(r"#!|#\s*(noqa|shellcheck |type:)|//\s*(@ts-|eslint)")
USAGE = "usage: tests/comments.py check | strip [file...]"


def files():
    out = subprocess.run(["git", "-C", PKG, "ls-files", *ROOTS], capture_output=True, text=True, check=True).stdout.split()
    return [f for f in out if kind(f)]


def kind(f):
    if f.endswith(".py"):
        return "py"
    if f.endswith((".ts", ".mts")):
        return "ts"
    if f.endswith((".tla", ".cfg")):
        return "tla"
    if f.endswith((".yml", ".yaml")):
        return "yml"
    if f.endswith(".sh"):
        return "sh"
    if f.endswith((".mmd", ".md", ".json", ".toml", ".txt")) or "/" not in f:
        return None
    try:
        with open(os.path.join(PKG, f), errors="replace") as fh:
            first = fh.readline()
    except OSError:
        return None
    if "python" in first:
        return "py"
    if first.startswith("#!") and ("sh" in first):
        return "sh"
    if f.startswith("hosts/") and f.endswith("host.sh"):
        return "sh"
    return None


def py(text):
    found, keep = [], []
    toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
    for t in toks:
        if t.type == tokenize.COMMENT and not KEEP.match(t.string) and not (t.start == (1, 0) and t.string.startswith("#!")):
            found.append((t.start, t.end))
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            d = node.body[0]
            if isinstance(d, ast.Expr) and isinstance(d.value, ast.Constant) and isinstance(d.value.value, str):
                keep.append(d)
    lines = text.splitlines(keepends=True)
    drop_lines, cuts = set(), {}
    for d in keep:
        for n in range(d.lineno, d.end_lineno + 1):
            drop_lines.add(n)
    for (r, c), _ in found:
        cuts[r] = min(c, cuts.get(r, c))
    hits = [(r, lines[r - 1][c:].rstrip()) for r, c in sorted(cuts.items())] + [(d.lineno, "docstring") for d in keep]
    out = []
    for n, line in enumerate(lines, 1):
        if n in drop_lines:
            continue
        if n in cuts:
            head = line[:cuts[n]].rstrip()
            if not head:
                continue
            line = head + "\n"
        out.append(line)
    new = "".join(out)
    for d in keep:
        parent = next(p for p in ast.walk(tree) if getattr(p, "body", None) and p.body and p.body[0] is d)
        if len(parent.body) == 1 and not isinstance(parent, ast.Module):
            raise SystemExit(f"a body that is only a docstring at line {d.lineno}")
    return new, hits


def scan_c(text, hash_tla=False):
    i, n, out, hits, prev = 0, len(text), [], [], ""
    line = 1
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if hash_tla and two == "\\*":
            j = text.find("\n", i)
            j = n if j < 0 else j
            hits.append((line, text[i:j]))
            out.append(None)
            i = j
            continue
        if hash_tla and two == "(*":
            j = text.find("*)", i) + 2
            hits.append((line, text[i:j]))
            line += text.count("\n", i, j)
            out += [None, *["\n", None] * text.count("\n", i, j)]
            i = j
            continue
        if not hash_tla and two == "//" and not KEEP.match(text[i:i + 12]):
            j = text.find("\n", i)
            j = n if j < 0 else j
            hits.append((line, text[i:j]))
            out.append(None)
            i = j
            continue
        if not hash_tla and two == "/*":
            j = text.find("*/", i) + 2
            hits.append((line, text[i:j]))
            line += text.count("\n", i, j)
            out += [None, *["\n", None] * text.count("\n", i, j)]
            i = j
            continue
        if c == '"' or (not hash_tla and c in "'`"):
            j = i + 1
            while j < n and text[j] != c:
                if text[j] == "\\":
                    j += 1
                elif c == "`" and text[j:j + 2] == "${":
                    depth, j = 1, j + 2
                    while j < n and depth:
                        if text[j] == "{":
                            depth += 1
                        elif text[j] == "}":
                            depth -= 1
                        elif text[j] in "\"'":
                            q = text[j]
                            j += 1
                            while text[j] != q:
                                j += 2 if text[j] == "\\" else 1
                        j += 1
                    continue
                j += 1
            out.append(text[i:j + 1])
            line += text.count("\n", i, j + 1)
            i = j + 1
            prev = "a"
            continue
        after_op = prev == "" or prev in "(,=:[!&|?{};+-*%<>~^"
        if not hash_tla and c == "/" and (after_op or re.search(r"\b(return|typeof|in|of)\s*$", "".join(x for x in out[-8:] if x))):
            j, cls = i + 1, False
            while j < n and (text[j] != "/" or cls):
                if text[j] == "\\":
                    j += 1
                elif text[j] == "[":
                    cls = True
                elif text[j] == "]":
                    cls = False
                j += 1
            m = re.match(r"[a-z]*", text[j + 1:])
            out.append(text[i:j + 1 + m.end()])
            i = j + 1 + m.end()
            prev = "a"
            continue
        out.append(c)
        if c == "\n":
            line += 1
        if not c.isspace():
            prev = c
        i += 1
    return out, hits


def tidy(parts, original):
    text = "".join("\x00" if p is None else p for p in parts)
    res = []
    old_lines, new_lines = original.splitlines(keepends=True), text.splitlines(keepends=True)
    assert len(old_lines) == len(new_lines)
    for old, new in zip(old_lines, new_lines):  # noqa: B905
        if "\x00" in new:
            head = new.replace("\x00", "").rstrip()
            if not head.strip():
                continue
            res.append(head + "\n")
        elif new.strip() or old.strip() == "":
            res.append(new)
    return re.sub(r"\n{3,}", "\n\n", "".join(res))


def c_like(text, tla=False):
    parts, hits = scan_c(text, tla)
    return tidy(parts, text), hits


def sh(text):
    hits, heredoc = [], None
    lines = text.splitlines(keepends=True)
    quote = None
    res = []
    for ln, raw in enumerate(lines, 1):
        if heredoc:
            res.append(raw)
            if raw.strip() == heredoc:
                heredoc = None
            continue
        if ln == 1 and raw.startswith("#!"):
            res.append(raw)
            continue
        j, cut = 0, None
        while j < len(raw):
            c = raw[j]
            if quote:
                if c == "\\" and quote == '"':
                    j += 2
                    continue
                if c == quote:
                    quote = None
                j += 1
                continue
            if c == "\\":
                j += 2
                continue
            if c in "'\"":
                quote = c
            elif c == "#" and (j == 0 or raw[j - 1] in " \t;(|&") and not KEEP.match(raw[j:]):
                cut = j
                break
            j += 1
        m = re.search(r"<<-?\s*'?\"?(\w+)", raw if cut is None else raw[:cut])
        if m and not quote:
            heredoc = m.group(1)
        if cut is None:
            res.append(raw)
            continue
        hits.append((ln, raw[cut:].rstrip()))
        head = raw[:cut].rstrip()
        if head:
            res.append(head + "\n")
    return re.sub(r"\n{3,}", "\n\n", "".join(res)), hits


def yml(text):
    res, hits = [], []
    for ln, raw in enumerate(text.splitlines(keepends=True), 1):
        if raw.lstrip().startswith("#"):
            hits.append((ln, raw.strip()))
            continue
        res.append(raw)
    return "".join(res), hits


def strip(f):
    with open(os.path.join(PKG, f)) as fh:
        text = fh.read()
    k = kind(f)
    if k == "py":
        return py(text)
    if k == "ts":
        return c_like(text)
    if k == "tla":
        return c_like(text, tla=True)
    if k == "sh":
        return sh(text)
    return yml(text)


def main(argv):
    if not argv or argv[0] not in ("check", "strip"):
        print(USAGE, file=sys.stderr)
        return 2
    targets = argv[1:] or files()
    bad = 0
    for f in targets:
        new, hits = strip(f)
        if argv[0] == "check":
            for ln, what in hits:
                print(f"{f}:{ln}: {what[:100]}")
            bad += len(hits)
        elif hits:
            with open(os.path.join(PKG, f), "w") as fh:
                fh.write(new)
    if argv[0] == "check" and bad:
        print(f"{bad} comment(s): zstack code carries no comments (shebangs and tool directives excepted)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
