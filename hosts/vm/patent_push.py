#!/usr/bin/env python3
"""Patent hold at publish time (Zane 27 Oct 14:12 SGT, t747s4293, corrected t748u).

The patent hold only decides which repo a change is pushed to. Work that touches pairing, Teleport or Keyvault is
built normally (models may see it), but it is never pushed to public trycua/cua: it goes to the
private repo trycua/cua-staging instead. Relay work in general is not held.

This is cua's pre-push hook (hosts/vm/pre-push, installed as /srv/onigiri/cua/.git/hooks/pre-push), so every push from
the cua clone and its run worktrees passes here: the single place where the remote is chosen. For each pushed ref it diffs the new head
against its merge base with origin/main (changed paths and changed lines). If that diff matches HELD and the remote
is not trycua/cua-staging, the public push is refused and:
  - when injaneity can push to trycua/cua-staging, the same commit is pushed there (never forced) and the run is told
    to open its PR with `gh pr create -R trycua/cua-staging`;
  - otherwise the branch stays local and a buffer item tells Zane it is held for the private repo.
A diff that cannot be read counts as held (fail closed).
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Not preceded by a letter or digit, so "repairing" passes; "src/pairing.rs", "ws_pairing" and "KeyVault" still hit
# (Zane 27 Oct t753u). Generated lockfiles are skipped entirely, path and lines (a crate named teleport-e2e is not
# patent work).
HELD = re.compile(r"(?<![a-z0-9])(teleport|keyvault|pairing)", re.I)
LOCKFILE = re.compile(r"(^|/)(Cargo\.lock|package-lock\.json|npm-shrinkwrap\.json|pnpm-lock\.yaml|yarn\.lock|uv\.lock"
                      r"|poetry\.lock|pdm\.lock|Pipfile\.lock|bun\.lockb?|composer\.lock|Gemfile\.lock|go\.sum"
                      r"|flake\.lock|Package\.resolved|packages\.lock\.json|gradle\.lockfile|pubspec\.lock|mix\.lock)$")
STAGING = "trycua/cua-staging"
STAGING_URL = f"https://github.com/{STAGING}"
ZERO = "0" * 40
ROOT = os.path.abspath(os.environ.get("ONIGIRI_ROOT") or "/srv/onigiri")   # buffer.jsonl and events.log live here
SGT = ZoneInfo("Asia/Singapore")


def is_staging(url):
    return bool(re.search(r"github\.com[:/]trycua/cua-staging(\.git)?/?$", url or ""))


def held_hits(paths, diff):
    """Changed paths and changed diff lines that match HELD (at most 5). Lockfiles are skipped."""
    hits = [p for p in paths if not LOCKFILE.search(p) and HELD.search(p)]
    lock = False
    for line in (diff or "").splitlines():
        if line.startswith("diff --git "):
            lock = bool(LOCKFILE.search(line.rsplit(" b/", 1)[-1]))
            continue
        if not lock and line[:1] in "+-" and not line.startswith(("+++", "---")) and HELD.search(line):
            hits.append(line[:100])
    return list(dict.fromkeys(hits))[:5]


def choose_remote(url, hits, staging_ok):
    """-> "push" (go ahead), "staging" (push to cua-staging instead) or "hold" (keep local, buffer for Zane)."""
    if not hits or is_staging(url):
        return "push"
    return "staging" if staging_ok() else "hold"


def git(*args, cwd=None):
    env = {k: v for k, v in os.environ.items() if k != "GIT_NO_LAZY_FETCH"}  # a diff may need a few base blobs
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout


def diff_hits(sha, cwd=None):
    rc, base = git("merge-base", sha, "refs/remotes/origin/main", cwd=cwd)
    base = base.strip() if rc == 0 and base.strip() else None
    if not base:
        return ["no merge base with origin/main (diff unreadable)"]
    rc1, paths = git("diff", "--name-only", base, sha, cwd=cwd)
    rc2, diff = git("diff", "-U0", "--no-color", base, sha, cwd=cwd)
    if rc1 or rc2:
        return ["diff unreadable"]
    return held_hits(paths.splitlines(), diff)


def staging_push_ok():
    home = os.environ.get("HOME") or "/home/box"
    env = {**os.environ, "GH_CONFIG_DIR": os.environ.get("GH_CONFIG_DIR") or f"{home}/.config/gh"}
    try:
        r = subprocess.run(["gh", "api", f"repos/{STAGING}", "--jq", ".permissions.push"], env=env,
                           capture_output=True, text=True, timeout=60)
    except Exception:
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def buffer_hold(branch, sha, hits, root=ROOT):
    """One buffer item per branch and head (buffer.jsonl, read by onigiri), plus an events.log line."""
    ident = f"cua:{branch}"
    path = f"{root}/buffer.jsonl"
    try:
        for line in open(path):
            if f'"id": "{ident}"' in line and f'"head": "{sha}"' in line and '"patent-held-publish"' in line:
                return False
    except OSError:
        pass
    when = datetime.now(SGT).isoformat(timespec="seconds")
    reason = (f"held for the private repo: the diff touches pairing, Teleport or Keyvault ({'; '.join(hits)[:200]}), "
              f"so it was not pushed to public trycua/cua. {STAGING} is not pushable as injaneity, so the branch "
              f"stays local. Zane: grant push on {STAGING} or say where it goes.")
    with open(path, "a") as f:
        f.write(json.dumps({"time": when, "kind": "patent-held-publish", "id": ident, "head": sha, "reason": reason}) + "\n")
    with open(f"{root}/events.log", "a") as f:
        f.write(f"{when} buffer {ident} patent-held-publish: {reason}\n".replace("\r", " "))
    return True


def main(argv, stdin, cwd=None, staging_ok=None, root=ROOT, out=sys.stderr):
    """pre-push hook: argv = [remote name, remote url], stdin = "<local ref> <local sha> <remote ref> <remote sha>"
    lines. Returns the exit code (non-zero refuses the whole push)."""
    url = argv[1] if len(argv) > 1 else ""
    refuse = 0
    for line in stdin.read().splitlines():
        f = line.split()
        if len(f) != 4 or f[1] == ZERO:  # malformed or a branch delete: nothing new is published
            continue
        local_ref, sha, remote_ref, _ = f
        hits = diff_hits(sha, cwd)
        act = choose_remote(url, hits, staging_ok or staging_push_ok)
        if act == "push":
            continue
        refuse = 1
        branch = remote_ref.removeprefix("refs/heads/")
        if act == "staging":
            rc, _ = git("push", STAGING_URL, f"{sha}:refs/heads/{branch}", cwd=cwd)
            if rc == 0:
                out.write(f"patent hold: {branch} touches pairing, Teleport or Keyvault, so it was pushed to the private "
                          f"repo {STAGING}, not to {url}. Open or update its PR there: gh pr create -R {STAGING} "
                          f"--head {branch}. Never push it to trycua/cua.\n")
                continue
        buffer_hold(branch, sha, hits, root)
        out.write(f"patent hold: {branch} touches pairing, Teleport or Keyvault and must not go to public "
                  f"trycua/cua. It stays local and onigiri has a buffer item for Zane. Do not retry the push, push "
                  f"elsewhere or open a PR; end the run with your verdict and say it is held for the private repo.\n")
    return refuse


if __name__ == "__main__":
    sys.exit(main(sys.argv, sys.stdin))
