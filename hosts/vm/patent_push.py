#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_pol = os.path.join(PKG, "bin", "policy")
_spec = importlib.util.spec_from_file_location("policy", _pol, loader=importlib.machinery.SourceFileLoader("policy", _pol))
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)
try:
    POLICY = policy.load()
    PATENT, ME = POLICY["patent"], POLICY["people"]["me"]
except policy.PolicyError as e:
    sys.exit(f"patent hold: config/policy.toml is unreadable ({e}), so nothing is pushed until it is fixed. Do not retry "
             "the push, push elsewhere or skip the hook; say so in your report.")
HELD = re.compile(r"(?<![a-z0-9])(" + "|".join(map(re.escape, PATENT["held_words"])) + ")", re.I)
LOCKFILE = re.compile(r"(^|/)(" + "|".join(map(re.escape, PATENT["skip_lockfiles"])) + ")$")
PUBLIC, STAGING = PATENT["public_repo"], PATENT["private_repo"]
STAGING_URL = f"https://github.com/{STAGING}"
ZERO = "0" * 40
ROOT = os.path.abspath(os.environ.get("ONIGIRI_ROOT") or "/srv/onigiri")
SGT = ZoneInfo("Asia/Singapore")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wake  # noqa: E402


def on_github(repo, url):
    return bool(re.search(rf"github\.com[:/]{re.escape(repo)}(\.git)?/?$", url or ""))


def is_staging(url):
    return on_github(STAGING, url)


def held_hits(paths, diff):
    hits = [p for p in paths if not LOCKFILE.search(p) and HELD.search(p)]
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
        if not lock and line[:cols] in ("+" * cols, "-" * cols) and HELD.search(line[cols:]):
            hits.append(line[:100])
    return list(dict.fromkeys(hits))[:5]


def status_paths(name_status):
    return [p for line in name_status.splitlines() for p in line.split("\t")[1:]]


def choose_remote(url, hits, staging_ok):
    if not hits or is_staging(url):
        return "push"
    return "staging" if staging_ok() else "hold"


def git(*args, cwd=None):
    env = {k: v for k, v in os.environ.items() if k != "GIT_NO_LAZY_FETCH"}
    r = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    return r.returncode, r.stdout


def diff_hits(sha, cwd=None):
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
    ident, log = f"cua:{branch}", f"{root}/events.log"
    mark = f" patent-held {ident} at {sha} "
    try:
        if any(mark in line and line.split(mark, 1)[1].startswith(("webhook sent", "not sent: dry run")) for line in open(log)):
            return False
    except OSError:
        pass
    reason = (f"held for the private repo: the diff touches pairing, Teleport or Keyvault ({'; '.join(hits)[:200]}), "
              f"so it was not pushed to public {PUBLIC}. {STAGING} is not pushable as {ME}, so the branch "
              f"stays local. Zane: grant push on {STAGING} or say where it goes.")
    status = wake.post("blocked", f"{ident} {reason}")
    with open(log, "a") as f:
        f.write(f"{datetime.now(SGT).isoformat(timespec='seconds')}{mark}{status}: {reason}\n".replace("\r", " "))
    return status.startswith(("webhook sent", "not sent: dry run"))


def record_push(sid, refs, root=ROOT):
    d = os.path.join(root, "review", sid or "")
    if not refs or not sid or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", sid) or not os.path.isdir(d):
        return False
    at = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with open(os.path.join(d, "pushed"), "a") as f:
        f.write("".join(f"{sha} {branch} {at}\n" for sha, branch in refs))
    return True


def is_public(url):
    return on_github(PUBLIC, url)


def main(argv, stdin, cwd=None, staging_ok=None, root=ROOT, out=sys.stderr):
    url = argv[1] if len(argv) > 1 else ""
    refuse, ok = 0, []
    for line in stdin.read().splitlines():
        f = line.split()
        if len(f) != 4 or f[1] == ZERO:
            continue
        local_ref, sha, remote_ref, _ = f
        hits = diff_hits(sha, cwd)
        act = choose_remote(url, hits, staging_ok or staging_push_ok)
        if act == "push":
            ok.append((sha, remote_ref.removeprefix("refs/heads/")))
            continue
        refuse = 1
        branch = remote_ref.removeprefix("refs/heads/")
        if act == "staging":
            rc, _ = git("push", STAGING_URL, f"{sha}:refs/heads/{branch}", cwd=cwd)
            if rc == 0:
                out.write(f"patent hold: {branch} touches pairing, Teleport or Keyvault, so it was pushed to the private "
                          f"repo {STAGING}, not to {url}. Open or update its PR there: gh pr create -R {STAGING} "
                          f"--head {branch}. Never push it to {PUBLIC}.\n")
                continue
        woke = "Zane has been woken" if wake_hold(branch, sha, hits, root) else "Zane was woken earlier, or the wake failed (events.log)"
        out.write(f"patent hold: {branch} touches pairing, Teleport or Keyvault and must not go to public "
                  f"{PUBLIC}. It stays local, and {woke}. Do not retry the push, push "
                  f"elsewhere or open a PR; end the run with your verdict and say it is held for the private repo.\n")
    if not refuse and is_public(url):
        record_push(os.environ.get("PI_SESSION_ID"), ok, root)
    return refuse


if __name__ == "__main__":
    sys.exit(main(sys.argv, sys.stdin))
