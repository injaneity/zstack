#!/usr/bin/env python3
"""Patent hold at publish time (Zane 27 Oct 14:12 SGT, t747s4293, corrected t748u).

The patent hold only decides which repo a change is pushed to. Work that touches pairing, Teleport or Keyvault is
built normally (models may see it), but it is never pushed to public trycua/cua: it goes to the
private repo trycua/cua-staging instead. Relay work in general is not held.

This is cua's pre-push hook (hosts/vm/pre-push, installed as /srv/onigiri/cua/.git/hooks/pre-push), so every push from
the cua clone and its run worktrees passes here: the single place where the remote is chosen. For each pushed ref it
checks the net diff from its merge base with origin/main and every commit in the push range (<head> --not origin/main),
changed paths and changed lines, with renames found so a rename's source and target paths both count. A held change
that a later commit reverts is still in the history being published. A merge commit is read as a combined diff, so only
what the merge itself adds counts, not the main it brings in. If any of that matches HELD and the remote is not
trycua/cua-staging, the public push is refused and:
  - when injaneity can push to trycua/cua-staging, the same commit is pushed there (never forced) and the run is told
    to open its PR with `gh pr create -R trycua/cua-staging`;
  - otherwise the branch stays local and a blocked wake (wake.py, once per branch and head) tells Zane it is held for
    the private repo.
A diff that cannot be read counts as held (fail closed).

A push that goes ahead to trycua/cua from a pi session (PI_SESSION_ID, set by bin/pi-run) appends `<sha> <branch>` to
<root>/review/<id>/pushed. That file names the thread that pushed a PR's head, which owns its CI: bin/hourly reopens it
when the PR goes red at that head (bin/pi-run ci).
"""
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
ROOT = os.path.abspath(os.environ.get("ONIGIRI_ROOT") or "/srv/onigiri")   # events.log lives here
SGT = ZoneInfo("Asia/Singapore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # wake.py sits beside this file
import wake  # noqa: E402


def is_staging(url):
    return bool(re.search(r"github\.com[:/]trycua/cua-staging(\.git)?/?$", url or ""))


def held_hits(paths, diff):
    """Changed paths and changed diff lines that match HELD (at most 5). Lockfiles are skipped. The diff may be a
    plain one (`diff --git`, one +/- column) or a merge's combined diff (`diff --cc`, one column per parent), where
    only the lines the merge itself adds or drops against every parent count."""
    hits = [p for p in paths if not LOCKFILE.search(p) and HELD.search(p)]
    # cols: the number of +/- columns, set by each hunk header (@@ is 1, @@@ is 2). The ---/+++ file names are
    # skipped only in a file header, so a changed line that starts with -- or ++ is still read.
    lock, cols, hunk = False, 1, False
    for line in (diff or "").splitlines():
        if line.startswith(("diff --git ", "diff --cc ", "diff --combined ")):
            path = line.rsplit(" b/", 1)[-1] if line.startswith("diff --git ") else line.split(" ", 2)[-1]
            lock, cols, hunk = bool(LOCKFILE.search(path)), 1, False
            continue
        if line.startswith("@@"):
            cols, hunk = len(line) - len(line.lstrip("@")) - 1, True
            continue
        if not hunk and line.startswith(("+++ ", "--- ")):
            continue
        # a changed line has + (or -) in every column; " +" in a merge came from a parent, not from the merge
        if not lock and line[:cols] in ("+" * cols, "-" * cols) and HELD.search(line[cols:]):
            hits.append(line[:100])
    return list(dict.fromkeys(hits))[:5]


def status_paths(name_status):
    """Every path in `git diff --name-status` output: both sides of a rename or copy, and every merge column."""
    return [p for line in name_status.splitlines() for p in line.split("\t")[1:]]


def choose_remote(url, hits, staging_ok):
    """-> "push" (go ahead), "staging" (push to cua-staging instead) or "hold" (keep local, wake Zane)."""
    if not hits or is_staging(url):
        return "push"
    return "staging" if staging_ok() else "hold"


def git(*args, cwd=None):
    env = {k: v for k, v in os.environ.items() if k != "GIT_NO_LAZY_FETCH"}  # a diff may need a few base blobs
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout


def diff_hits(sha, cwd=None):
    """HELD hits in the net diff from the merge base with origin/main, then in each commit of <sha> --not origin/main."""
    rc, base = git("merge-base", sha, "refs/remotes/origin/main", cwd=cwd)
    base = base.strip() if rc == 0 and base.strip() else None
    if not base:
        return ["no merge base with origin/main (diff unreadable)"]
    rc1, paths = git("diff", "--name-status", "--find-renames", "--no-color", base, sha, cwd=cwd)
    rc2, diff = git("diff", "-U0", "--find-renames", "--no-color", base, sha, cwd=cwd)
    rc3, revs = git("rev-list", "--reverse", sha, "--not", "refs/remotes/origin/main", cwd=cwd)
    if rc1 or rc2 or rc3:
        return ["diff unreadable"]
    hits = held_hits(status_paths(paths), diff)
    for c in revs.split():
        # diff-tree prints the commit id first; --cc gives a merge only the lines it adds, and nothing for a root commit
        rc1, paths = git("diff-tree", "-r", "--cc", "--root", "--no-commit-id", "--name-status", "--find-renames", c, cwd=cwd)
        rc2, diff = git("diff-tree", "-r", "--cc", "--root", "--no-commit-id", "-U0", "--find-renames", "--no-color", c, cwd=cwd)
        if rc1 or rc2:
            return hits + [f"commit {c[:12]} diff unreadable"]
        hits += [f"{c[:12]}: {h}" for h in held_hits(status_paths(paths), diff)]
    return list(dict.fromkeys(hits))[:5]


def staging_push_ok():
    home = os.environ.get("HOME") or "/home/box"
    env = {**os.environ, "GH_CONFIG_DIR": os.environ.get("GH_CONFIG_DIR") or f"{home}/.config/gh"}
    try:
        r = subprocess.run(["gh", "api", f"repos/{STAGING}", "--jq", ".permissions.push"], env=env,
                           capture_output=True, text=True, timeout=60)
    except Exception:
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def wake_hold(branch, sha, hits, root=ROOT):
    """One blocked wake per branch and head. Its events.log line (`patent-held cua:<branch> at <sha> <status>: ...`)
    records how the POST went; a push of the same head sends again until one line says it was sent."""
    ident, log = f"cua:{branch}", f"{root}/events.log"
    mark = f" patent-held {ident} at {sha} "
    try:
        if any(mark in line and line.split(mark, 1)[1].startswith(("webhook sent", "not sent: dry run")) for line in open(log)):
            return False
    except OSError:
        pass
    reason = (f"held for the private repo: the diff touches pairing, Teleport or Keyvault ({'; '.join(hits)[:200]}), "
              f"so it was not pushed to public trycua/cua. {STAGING} is not pushable as injaneity, so the branch "
              f"stays local. Zane: grant push on {STAGING} or say where it goes.")
    status = wake.post("blocked", f"{ident} {reason}")
    with open(log, "a") as f:
        f.write(f"{datetime.now(SGT).isoformat(timespec='seconds')}{mark}{status}: {reason}\n".replace("\r", " "))
    return status.startswith(("webhook sent", "not sent: dry run"))


def record_push(sid, sha, branch, root=ROOT):
    """Note that session `sid` pushed `sha` to trycua/cua, in its own review dir only (a push from no pi run records nothing)."""
    d = os.path.join(root, "review", sid or "")
    if not sid or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", sid) or not os.path.isdir(d):
        return False
    with open(os.path.join(d, "pushed"), "a") as f:
        f.write(f"{sha} {branch}\n")
    return True


def is_public(url):
    return bool(re.search(r"github\.com[:/]trycua/cua(\.git)?/?$", url or ""))


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
            if is_public(url):
                record_push(os.environ.get("PI_SESSION_ID"), sha, remote_ref.removeprefix("refs/heads/"), root)
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
        woke = "Zane has been woken" if wake_hold(branch, sha, hits, root) else "Zane was woken earlier, or the wake failed (events.log)"
        out.write(f"patent hold: {branch} touches pairing, Teleport or Keyvault and must not go to public "
                  f"trycua/cua. It stays local, and {woke}. Do not retry the push, push "
                  f"elsewhere or open a PR; end the run with your verdict and say it is held for the private repo.\n")
    return refuse


if __name__ == "__main__":
    sys.exit(main(sys.argv, sys.stdin))
