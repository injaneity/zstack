#!/usr/bin/env python3
import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pol = os.path.join(PKG, "bin", "policy")
_spec = importlib.util.spec_from_file_location("policy", _pol, loader=importlib.machinery.SourceFileLoader("policy", _pol))
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)
try:
    POLICY = policy.load()
    PATENT, ME, OWNER = POLICY["patent"], POLICY["people"]["me"], POLICY["people"]["owner"]
except policy.PolicyError as e:
    sys.exit(f"patent hold: the config is unreadable ({e}), so nothing is pushed until it is fixed. Do not retry "
             "the push, push elsewhere or skip the hook; say so in your report.")
HELD = re.compile(r"(?<![a-z0-9])(" + "|".join(map(re.escape, PATENT["held_words"])) + ")", re.I)
LOCKFILE = re.compile(r"(^|/)(" + "|".join(map(re.escape, PATENT["skip_lockfiles"])) + ")$")
PUBLIC, STAGING = POLICY["merge"]["repo"], PATENT["private_repo"]
WORDS = ", ".join(PATENT["held_words"][:-1]) + " or " + PATENT["held_words"][-1]
STAGING_URL = f"https://github.com/{STAGING}"
ZERO = "0" * 40
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wake  # noqa: E402

ROOT = wake.root()


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


SPDX = "SPDX-License-Identifier"


def has_spdx(sha, path, cwd):
    rc, text = git("show", f"{sha}:{path}", cwd=cwd)
    return rc == 0 and SPDX in "\n".join(text.splitlines()[:10])


TOOLS = r"claude|anthropic|openai|codex|chatgpt|gpt-|copilot|cursor|gemini|grok|devin|aider|\[bot\]"


def lint(sha, cwd=None):
    rc, base = git("merge-base", sha, "refs/remotes/origin/main", cwd=cwd)
    rc1, status = git("diff", "--name-status", "--no-renames", "--no-color", base.strip(), sha, cwd=cwd) if rc == 0 and base.strip() else (1, "")
    if rc1:
        return []
    out, added = [], []
    for row in status.splitlines():
        kind, _, path = row.partition("\t")
        if kind.startswith("D"):
            continue
        parts = path.split("/")
        if parts[-1] == ".onigiri.json":
            out.append(f"{path} is onigiri config and is never committed (Hard rules, scope-no-config)")
        if "target" in parts[:-1]:
            out.append(f"{path} is build output under target/ (Hard rules, checks-target)")
        if kind.startswith("A"):
            added.append(path)
    for path in added:
        folder, ext = os.path.dirname(path), os.path.splitext(path)[1]
        rc2, names = git("ls-tree", "--name-only", base.strip(), (folder + "/") if folder else ".", cwd=cwd)
        near = [n for n in names.splitlines() if ext and n.endswith(ext) and n != path][:20]
        if rc2 == 0 and not has_spdx(sha, path, cwd) and any(has_spdx(base.strip(), n, cwd) for n in near):
            out.append(f"{path} is new and lacks the SPDX header its neighbours carry (Hard rules, text-spdx)")
    _, ident = git("var", "GIT_COMMITTER_IDENT", cwd=cwd)
    me = ident[ident.find("<") + 1:ident.find(">")]
    rc3, log = git("log", "--no-merges", "--format=%H%x00%ce%x00%B%x01", sha, "--not", "refs/remotes/origin/main", cwd=cwd)
    for entry in log.split("\x01") if rc3 == 0 else []:
        c, _, rest = entry.strip().partition("\x00")
        who, _, body = rest.partition("\x00")
        if c and who == me and re.search(rf"^co-authored-by:.*({TOOLS})", body, re.I | re.M):
            out.append(f"commit {c[:12]} carries a Co-authored-by trailer for a model, agent or tool (Hard rules, text-no-trailers)")
    return list(dict.fromkeys(out))[:8]


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
    ident, log = f"{PUBLIC.split('/')[1]}:{branch}", f"{root}/events.log"
    mark = f" patent-held {ident} at {sha} "
    try:
        if any(mark in line and line.split(mark, 1)[1].startswith(("webhook sent", "not sent: dry run")) for line in open(log)):
            return False
    except OSError:
        pass
    reason = (f"held for the private repo: the diff touches {WORDS} ({'; '.join(hits)[:200]}), "
              f"so it was not pushed to public {PUBLIC}. {STAGING} is not pushable as {ME}, so the branch "
              f"stays local. {OWNER}: grant push on {STAGING} or say where it goes.")
    status = wake.post("blocked", f"{ident} {reason}")
    with open(log, "a") as f:
        f.write(f"{wake.now()}{mark}{status}: {reason}\n".replace("\r", " "))
    return status.startswith(("webhook sent", "not sent: dry run"))


def main(argv, stdin, cwd=None, staging_ok=None, root=ROOT, out=sys.stderr):
    url = argv[1] if len(argv) > 1 else ""
    refuse = 0
    for line in stdin.read().splitlines():
        f = line.split()
        if len(f) != 4 or f[1] == ZERO:
            continue
        local_ref, sha, remote_ref, _ = f
        if problems := lint(sha, cwd):
            refuse = 1
            out.write(f"push refused: {remote_ref.removeprefix('refs/heads/')} breaks the Hard rules: " + "; ".join(problems)
                      + ". Fix the commits and push again; do not skip the hook.\n")
            continue
        hits = diff_hits(sha, cwd)
        act = choose_remote(url, hits, staging_ok or staging_push_ok)
        if act == "push":
            continue
        refuse = 1
        branch = remote_ref.removeprefix("refs/heads/")
        if act == "staging":
            rc, _ = git("push", STAGING_URL, f"{sha}:refs/heads/{branch}", cwd=cwd)
            if rc == 0:
                out.write(f"patent hold: {branch} touches {WORDS}, so it was pushed to the private "
                          f"repo {STAGING}, not to {url}. Open or update its PR there: gh pr create -R {STAGING} "
                          f"--head {branch}. Never push it to {PUBLIC}.\n")
                continue
        woke = f"{OWNER} has been woken" if wake_hold(branch, sha, hits, root) else f"{OWNER} was woken earlier, or the wake failed (events.log)"
        out.write(f"patent hold: {branch} touches {WORDS} and must not go to public "
                  f"{PUBLIC}. It stays local, and {woke}. Do not retry the push, push "
                  f"elsewhere or open a PR; end the run with your verdict and say it is held for the private repo.\n")
    return refuse


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin))
