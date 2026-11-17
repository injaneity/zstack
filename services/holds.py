#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import os
import re
import sys

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_l = importlib.machinery.SourceFileLoader("policy", os.path.join(PKG, "bin", "policy"))
policy = importlib.util.module_from_spec(importlib.util.spec_from_loader(_l.name, _l))
_l.exec_module(policy)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wake  # noqa: E402
from wake import SGT, now, sha  # noqa: E402,F401

try:
    POLICY = policy.load()
except policy.PolicyError as e:
    sys.exit(f"holds: {e}")
ROOT = wake.root()
HOLDS_FILE = f"{ROOT}/hold-prs.txt"
PERSON = POLICY["people"]["owner"]
ENV = {**os.environ, "GH_CONFIG_DIR": os.environ.get("GH_CONFIG_DIR") or os.path.expanduser("~/.config/gh"),
       "PATH": os.pathsep.join([os.path.expanduser("~/.local/bin"), "/usr/local/bin", os.environ.get("PATH") or "/usr/bin:/bin"])}


def read(path):
    with open(path) as f:
        return f.read()


def parse_holds(text):
    h = {"hold": set(), "paths": [], "preapproved": {}}
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        f = [x.strip() for x in line.split("|")]
        kind, pr = f[0], re.fullmatch(r"#?(\d+)", f[1]) if len(f) > 1 else None
        if kind == "hands-off-path":
            mode = f[3][len("mode="):].strip() if len(f) > 3 and f[3].startswith("mode=") else "never"
            if len(f) < 4 or not f[1] or mode not in ("never", "split-undone"):
                raise ValueError(f"holds file line {ln}: {line[:80]}")
            if mode == "never":
                h["paths"].append(f[1])
            continue
        if kind not in ("hold", "preapprove"):
            if len(f) < 2 or not f[1] or pr:
                raise ValueError(f"holds file line {ln}: {line[:80]}")
            continue
        if len(f) < 4 or not pr:
            raise ValueError(f"holds file line {ln}: {kind} needs a PR number | rules | note")
        n = int(pr.group(1))
        if kind == "hold":
            h["hold"].add(n)
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", f[3]) or len(f) < 5 or not f[4]:
            raise ValueError(f"holds file line {ln}: preapprove needs #N | rules | <40-char sha> | source")
        if h["preapproved"].get(n, f[3]) != f[3]:
            raise ValueError(f"holds file line {ln}: two preapprove rows for #{n}")
        h["preapproved"][n] = f[3]
    return h


def authors(text):
    out = {"skip": set(), "review_only": set()}
    for line in text.splitlines():
        f = [x.strip() for x in line.split("|")]
        if not line.lstrip().startswith("#") and len(f) > 1 and f[0] in ("skip-author", "review-only"):
            out["skip" if f[0] == "skip-author" else "review_only"].add(f[1].lower())
    return out
