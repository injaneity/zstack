"""The merge gate: one repo's open PRs in, one decision each out.

Extracted from the onigiri tick, which ran this for trycua/cua with the repo slug and rules as module constants.
The decisions are the tick's, unchanged; what moved into Config is what differed per repo.

  decide    merge | ready | rerun | buffer | urgent | wait | skip, with the reason
  checks    what a status rollup says (empty | pending | red | green), ignoring non-required checks
  risk      declared (label or body line) and Clef's tier, which only ever escalates
  carry     may a verify PASS survive a merge of main into the branch
"""
import json
import os
import re
import subprocess
import time


TIERS = ["light", "standard", "deep", "human"]
OK_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
NEVER = re.compile(r"(?!)")  # stands in for a config that names no sensitive paths


class Config:
    """One repo's knobs. Load with `load(path)`; every field is required except where noted."""

    KNOWN = frozenset({
        "repo", "me", "merge_method", "label_prefix", "non_required_checks", "preapproved", "clef_script",
        "clef_cwd", "sensitive", "outside_sensitive", "holds", "pause_file", "checkouts",
    })

    def __init__(self, d):
        unknown = set(d) - self.KNOWN
        if unknown:
            # a typo'd key would otherwise just do nothing, and the knob it meant to set stays at its default
            raise ValueError(f"{d.get('repo')}: unknown config key(s) {sorted(unknown)}; known: {sorted(self.KNOWN)}")
        self.repo = d["repo"]
        self.me = d["me"]
        self.merge_method = d["merge_method"]
        self.label_prefix = d["label_prefix"]
        self.non_required_checks = frozenset(n.lower() for n in d.get("non_required_checks") or ())
        self.preapproved = frozenset(d.get("preapproved") or ())
        self.clef_script = d.get("clef_script") or None
        self.clef_cwd = d.get("clef_cwd") or None  # where Clef may read while scoring; None means the caller's cwd
        self.sensitive = re.compile(d["sensitive"], re.I) if d.get("sensitive") else NEVER
        self.outside_sensitive = re.compile(d["outside_sensitive"]) if d.get("outside_sensitive") else NEVER
        # Where this repo's holds and pause live. Named here so a deploy cannot forget them: --apply refuses to
        # run without a holds file, because "no holds given" and "nothing is held" must not look the same.
        self.holds = d.get("holds") or None
        self.pause_file = d.get("pause_file") or None
        # This repo's checkouts: `runs` reads .git/onigiri/v2 verdicts from them, `clone` fetches into them to
        # carry a PASS. Always the same directories, and in practice the same one, so it is one list.
        self.checkouts = tuple(d.get("checkouts") or ())

    def __repr__(self):
        return f"Config({self.repo})"


def load(path):
    with open(path) as f:
        return Config(json.load(f))


# ---------------- holds ----------------

HOLD_KINDS = ("hold", "no-move", "hands-off-user", "skip-author", "hands-off-path", "exception")


class HoldsUnreadable(RuntimeError):
    """The holds file exists but could not be parsed. Never act on a partial read: a dropped hold is a merge."""


def parse_holds(text):
    """The tick's holds format, unchanged: `kind | target | rules(yes/no) | note`.

    -> {"hold": {n}, "no_move": {n}, "users": {login}, "paths": [path], "exceptions": [...]}
    Raises HoldsUnreadable on a malformed row, exactly as the tick did.
    """
    h = {"hold": set(), "no_move": set(), "users": set(), "paths": [], "exceptions": []}
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        f = [x.strip() for x in line.split("|")]
        if len(f) < 4 or f[0] not in HOLD_KINDS or f[2] not in ("yes", "no") or not f[1]:
            raise HoldsUnreadable(f"holds file line {ln}: {line[:80]}")
        kind, target = f[0], f[1]
        if kind == "exception":
            if len(f) < 6 or not all(f[3:6]):
                raise HoldsUnreadable(f"holds file line {ln}: exception needs match | scope | source")
            h["exceptions"].append({"path": target, "match": f[3], "scope": f[4], "source": f[5]})
        elif kind in ("hold", "no-move"):
            try:
                (h["hold"] if kind == "hold" else h["no_move"]).add(int(target.lstrip("#")))
            except ValueError:
                raise HoldsUnreadable(f"holds file line {ln}: {kind} needs a PR number, got {target!r}")
        elif kind in ("hands-off-user", "skip-author"):
            h["users"].add(target)
        else:
            h["paths"].append(target)
    return h


def load_holds(path):
    """The holds file, parsed. None only when no path was given at all.

    A path that does not exist is an error, not "no holds": the tick opened the file directly, so a typo'd or
    unmounted /srv/onigiri/hold-prs.txt raised, skipped the merge pass and sent an urgent alert. Treating a
    missing file as empty would merge every patent-hold PR, which is the one thing this may never do.
    """
    if not path:
        return None
    try:
        with open(path) as f:
            return parse_holds(f.read())
    except OSError as e:
        raise HoldsUnreadable(f"{path}: {e}")


def path_hit(path, holds):
    """The hands-off path a changed file matches (by file name), or None."""
    return next((p for p in holds["paths"] if re.search(r"(^|/)" + re.escape(p) + "$", path)), None)


# ---------------- pure rules ----------------

def norm_tier(t):
    """Map any risk word to light|standard|deep|human, or None."""
    return {"low": "light", "light": "light", "standard": "standard", "high": "deep", "deep": "deep",
            "human": "human"}.get((t or "").strip().lower())


def declared_risk(labels, body, cfg):
    lab = next((l[len(cfg.label_prefix):] for l in labels if l.startswith(cfg.label_prefix)), None)
    found = re.findall(rf"^{re.escape(cfg.label_prefix)} *(\w+)", body or "", re.M)
    return norm_tier(lab or (found[-1] if found else None))


def clef_tier(raw, files, title, outside, cfg):
    """Clef prints low|standard|high. It has no human tier: high on a sensitive path (or, for outside PRs, the
    outside_sensitive paths) counts as human; any other high is deep."""
    t = norm_tier(raw)
    if t == "deep" and (any(cfg.sensitive.search(x) for x in [title, *files])
                        or outside and any(cfg.outside_sensitive.search(f) for f in files)):
        return "human"
    return t


def verify_pass(head, body, local_pass):
    return head in re.findall(r"^verify: PASS at ([0-9a-f]{40})", body or "", re.M) or head in local_pass


def carry_pass(git, head, passed, main_ref="refs/remotes/origin/main"):
    """May a verify PASS survive a merge of main into the branch?
    git(*args) -> (rc, stdout). passed: shas with a verify PASS. Returns (parent sha, why) when head is a 2-parent
    merge whose first parent passed verify, whose second parent is on main, and whose tree equals a fresh
    `git merge-tree` of the two parents (no conflict resolution, no extra edits); else (None, why not)."""
    rc, out = git("rev-list", "--parents", "-n", "1", head)
    shas = out.split() if rc == 0 else []
    if len(shas) != 3:
        return None, "head is not a two-parent merge" if shas else "head commit not found locally"
    _, p1, p2 = shas
    if p1 not in passed:
        return None, f"first parent {p1[:9]} has no verify PASS"
    if git("merge-base", "--is-ancestor", p2, main_ref)[0] != 0:
        return None, f"second parent {p2[:9]} is not on main"
    rc, out = git("merge-tree", "--write-tree", p1, p2)
    if rc != 0:
        return None, "merge of the parents conflicts"
    rc2, tree = git("rev-parse", f"{head}^{{tree}}")
    if rc2 != 0 or out.split()[0] != tree.strip():
        return None, "merge tree differs from a clean merge (conflict resolution or extra edits)"
    return p1, f"clean main merge of {p1[:9]} (PASS) and main {p2[:9]}"
# ---------------- checks ----------------

def latest_checks(rollup):
    """One entry per check name, the most recently started (a rerun supersedes the run it replaced)."""
    best = {}
    for c in rollup or []:
        key = (c.get("workflowName") or "", c.get("name") or c.get("context") or "")
        stamp = c.get("startedAt") or c.get("createdAt") or ""
        if key not in best or stamp >= (best[key].get("startedAt") or best[key].get("createdAt") or ""):
            best[key] = c
    return list(best.values())


def check_state(rollup, cfg):
    """-> ('empty'|'pending'|'red'|'green', [failing names], {run ids of failing GitHub Actions runs})"""
    cs = latest_checks(rollup)
    if not cs:
        return "empty", [], set()
    pending, bad, runs, ok = [], [], set(), 0
    for c in cs:
        name = c.get("name") or c.get("context")
        if (name or "").lower() in cfg.non_required_checks:
            continue
        if c.get("__typename") == "StatusContext" or "state" in c and "status" not in c:
            st = c.get("state")
            if st in ("PENDING", "EXPECTED"):
                pending.append(name)
            elif st != "SUCCESS":
                bad.append(name)
            else:
                ok += 1
            continue
        if c.get("status") != "COMPLETED":
            pending.append(name)
        elif c.get("conclusion") not in OK_CONCLUSIONS:
            bad.append(name)
            m = re.search(r"/actions/runs/(\d+)", c.get("detailsUrl") or "")
            if m:
                runs.add(m.group(1))
        elif c.get("conclusion") == "SUCCESS":
            ok += 1
    if pending:
        return "pending", pending, set()
    if not bad and not ok:  # nothing passed for real (only non-required checks): not green
        return "empty", [], set()
    return ("red", bad, runs) if bad else ("green", [], set())


# ---------------- the decision ----------------

def merge_decision(pr, *, cfg, local_pass, clef, held, rerun_head):
    """Return (action, reason). action: merge | ready | rerun | buffer | urgent | wait | skip.
    pr: dict with number, headRefOid, isDraft, mergeable, statusCheckRollup, labels(list of names), body.
    The order of these checks is the rule; do not reorder them."""
    n, head = pr["number"], pr["headRefOid"]
    if held:
        return "skip", f"hold: {held}"
    if not verify_pass(head, pr.get("body"), local_pass):
        return "skip", "no verify PASS at head"
    pre = n in cfg.preapproved
    declared = declared_risk(pr.get("labels", []), pr.get("body"), cfg)
    if cfg.clef_script and clef is None and not pre:
        return "wait", "awaiting clef score at head"
    # Clef only escalates: human wakes the owner, deep buffers for them whatever the declared risk (a Clef
    # deep or human PR never merges without them unless preapproved).
    risk = declared
    if not pre:
        if clef == "human":
            return "urgent", f"clef human (declared {declared})"
        if clef == "deep":
            return "buffer", f"clef deep: needs Zane (declared {declared})"
        if declared in ("deep", "human"):
            return "buffer", f"declared {declared}: stays draft, needs Zane"
        if declared is None:
            risk = clef
            if risk is None:
                return "buffer", "risk unknown (no risk label or body line)"
    if pr["isDraft"]:
        return "ready", f"verify PASS, risk {risk if not pre else 'pre-approved'}"
    if pr["mergeable"] == "CONFLICTING":
        return "buffer", "merge conflict"
    state, names, runs = check_state(pr.get("statusCheckRollup"), cfg)
    if state in ("empty", "pending"):
        return "wait", f"checks {state}" + (f" ({len(names)})" if names else "")
    if state == "red":
        if rerun_head != head and runs:
            return "rerun", "red once: " + ", ".join(sorted(names))[:150]
        return "buffer", "red after rerun: " + ", ".join(sorted(names))[:150]
    if pr["mergeable"] != "MERGEABLE":
        return "wait", f"mergeable {pr['mergeable']}"
    return "merge", f"green, verify PASS, risk {risk if not pre else 'pre-approved'}"


# ---------------- git and gh ----------------

# GIT_NO_LAZY_FETCH=1: on a blob:none clone git will otherwise fetch missing objects on demand. One `git log -S`
# lazy-fetched for 35 minutes and filled the tick's disk (27 Oct). Every child gets this, and a pinned HOME and
# GH_CONFIG_DIR so `gh` acts as the configured account rather than whoever last logged in.
def child_env(env=None):
    """The environment every child gets. These are forced, not defaulted: a caller that already has
    GIT_NO_LAZY_FETCH=0 in its environment is exactly the case the 27 Oct disk incident came from, and a
    setdefault would have preserved it."""
    e = dict(os.environ if env is None else env)
    e["GIT_NO_LAZY_FETCH"] = "1"
    home = e.get("HOME")
    if home:
        e["GH_CONFIG_DIR"] = e.get("GH_CONFIG_DIR") or os.path.join(home, ".config", "gh")
    return e


def sh(args, cwd=None, timeout=120, input=None, env=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, input=input,
                       env=child_env(env))
    return r.returncode, r.stdout, r.stderr


def gh_json(args, cwd=None):
    for attempt in range(3):
        rc, out, err = sh(["gh", *args], cwd=cwd)
        if not rc or not re.search(r"HTTP 5\d\d|timeout|TLS|connection", err, re.I):
            break
        time.sleep(5 * (attempt + 1))
    if rc:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {err.strip()[:200]}")
    return json.loads(out) if out.strip() else None
