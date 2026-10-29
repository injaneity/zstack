"""The merge gate: one repo's open PRs in, one decision each out.

Extracted from the onigiri tick, which ran this for trycua/cua with the repo slug and rules as module constants.
The decisions are the tick's, unchanged; what moved into Config is what differed per repo.

  decide    merge | ready | rerun | buffer | urgent | wait | skip, with the reason
  checks    what a status rollup says (empty | pending | red | green), ignoring non-required checks
  risk      declared (label or body line) and Clef's tier, which only ever escalates
  retrigger the plan for a fresh pull_request event when main changed a required workflow
  carry     may a verify PASS survive a merge of main into the branch
"""
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

SGT = ZoneInfo("Asia/Singapore")

TIERS = ["light", "standard", "deep", "human"]
OK_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
NEVER = re.compile(r"(?!)")  # stands in for a config that names no sensitive paths


class Config:
    """One repo's knobs. Load with `load(path)`; every field is required except where noted."""

    def __init__(self, d):
        self.repo = d["repo"]
        self.me = d["me"]
        self.merge_method = d["merge_method"]
        self.label_prefix = d["label_prefix"]
        self.non_required_checks = frozenset(n.lower() for n in d.get("non_required_checks") or ())
        self.preapproved = frozenset(d.get("preapproved") or ())
        self.clef_script = d.get("clef_script") or None
        self.clef_cwd = d.get("clef_cwd") or None  # where Clef may read the repo; None means the caller's cwd
        self.sensitive = re.compile(d["sensitive"], re.I) if d.get("sensitive") else NEVER
        self.outside_sensitive = re.compile(d["outside_sensitive"]) if d.get("outside_sensitive") else NEVER

    @property
    def requires_clef(self):
        """Does a PR wait for a Clef score at its head before this repo's gate may move it?"""
        return bool(self.clef_script)

    def __repr__(self):
        return f"Config({self.repo})"


def load(path):
    with open(path) as f:
        return Config(json.load(f))


# ---------------- pure rules ----------------

def norm_tier(t):
    """Map any risk word to light|standard|deep|human, or None."""
    return {"low": "light", "light": "light", "standard": "standard", "high": "deep", "deep": "deep",
            "human": "human"}.get((t or "").strip().lower())


def declared_risk(labels, body, cfg):
    lab = next((l[len(cfg.label_prefix):] for l in labels if l.startswith(cfg.label_prefix)), None)
    found = re.findall(rf"^{re.escape(cfg.label_prefix)} *(\w+)", body or "", re.M)
    return norm_tier(lab or (found[-1] if found else None))


def stricter(*tiers):
    known = [t for t in tiers if t in TIERS]
    return max(known, key=TIERS.index) if known else None


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


def reverify_line(n, head, old, why, risk, where=""):
    """The backlog line that queues a re-verify at a moved head. `where` is the prefix that says where to run it."""
    return (f"- [ ] PRIORITY (tick, papercut 6 of 8: queued by itself): {where}fix pr #{n}: re-verify only. head moved to "
            f"{head[:9]} since verify PASS at {old[:9]} ({why}). run verify at the new head and update the PR body's "
            f"verify line; change code only if verify fails. risk: {risk or 'standard'}")


def gate_started(rollup):
    """startedAt of the latest ci-gate check run, or None."""
    times = [c.get("startedAt") or "" for c in rollup or [] if (c.get("name") or c.get("context")) == "ci-gate"]
    return max(times) if times else None


def retrigger_plan(prs, tracked, sha, when, done):
    """PRs to send a fresh pull_request event after main commit sha (at when, ISO Z) changed a required workflow.
    prs: open PRs with number, isCrossRepository, statusCheckRollup.
    -> [(n, "toggle" | "fork")]: forks also need a fork CI approval, so a caller buffers them."""
    out = []
    for p in sorted(prs, key=lambda p: p["number"]):
        n = p["number"]
        started = gate_started(p.get("statusCheckRollup"))
        if n not in tracked or f"{n}@{sha}" in done or (started and started >= when):
            continue
        out.append((n, "fork" if p.get("isCrossRepository") else "toggle"))
    return out


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
    if cfg.requires_clef and clef is None and not pre:
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

def sh(args, cwd=None, timeout=120, input=None):
    r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, input=input)
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


def merge_ref_has(n, sha, cfg):
    """Does refs/pull/n/merge already contain main commit sha? True / False, or None when unknown (no merge ref,
    e.g. conflicts, or a lookup error). A toggle on a stale merge ref reruns the old workflow."""
    rc, out, _ = sh(["git", "ls-remote", f"https://github.com/{cfg.repo}", f"refs/pull/{n}/merge"])
    m = re.match(r"([0-9a-f]{40})\s", out or "") if rc == 0 else None
    if not m:
        return None
    if m.group(1) == sha:
        return True
    c = gh_json(["api", f"repos/{cfg.repo}/compare/{sha}...{m.group(1)}", "--jq", "{status: .status}"])
    st = (c or {}).get("status") if isinstance(c, dict) else None
    return None if st is None else st in ("ahead", "identical")


# ---------------- fingerprints and state files ----------------

def pr_fp(p):
    """Fingerprint of a PR (gh pr list/view JSON with headRefOid, state, isDraft, reviewDecision, labels, updatedAt):
    any push, state, draft, review, label change or other activity changes it."""
    labels = sorted(l["name"] if isinstance(l, dict) else l for l in p.get("labels") or [])
    return {"head": p.get("headRefOid"), "state": p.get("state"), "draft": p.get("isDraft"),
            "review": p.get("reviewDecision") or None, "labels": hashlib.sha1("\n".join(labels).encode()).hexdigest()[:12],
            "updated": p.get("updatedAt")}


def fp_match(old, new):
    """Is new the same PR state as old? A seeded fingerprint only has a (maybe 9-char) head and compares on that."""
    oh, nh = (old or {}).get("head") or "", (new or {}).get("head") or ""
    if len(oh) < 7 or not nh.startswith(oh):
        return False
    return bool(old.get("seeded")) or all(old.get(k) == new.get(k) for k in ("state", "draft", "review", "labels", "updated"))


def dedupe_key(kind, ident, head):
    return f"{kind}|{ident}|{head or '-'}"


def load_pending(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_pending(pend, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(pend, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def now(tz=None):
    """ISO timestamp with seconds in tz (default Asia/Singapore, the operator's zone)."""
    return datetime.now(tz or SGT).isoformat(timespec="seconds")


# ---------------- merge watch ----------------

def tick_merge_log(events, short):
    """PR numbers the gate itself merged, from "<short>#N" lines like "2026-10-27T01:00:00+08:00 merged cua#6124 ..."."""
    pat = re.compile(rf"^\S+ merged {re.escape(short)}#(\d+) ")
    return {int(m.group(1)) for l in events for m in [pat.search(l)] if m}


def merge_audit(merged, tick_merged, known, me):
    """Merges the gate didn't make. merged: gh pr list --state merged JSON (number, mergedBy, mergedAt);
    known: {str(n): record} already handled or acknowledged.
    -> [(n, action, by)]: alert (merged as `me`, not by the gate), log (another human), ignore (bot)."""
    out = []
    for p in sorted(merged, key=lambda p: p.get("mergedAt") or ""):
        n, by = p["number"], (p.get("mergedBy") or {}).get("login") or ""
        if n in tick_merged or str(n) in known:
            continue
        bot = (p.get("mergedBy") or {}).get("is_bot") or by.startswith("app/") or by.endswith("[bot]")
        out.append((n, "ignore" if bot else "alert" if by == me else "log", by))
    return out


# ---------------- backlog lines (a gate that runs on queued items) ----------------

def strip_negated(text):
    """Drop clauses that say what NOT to touch, so 'don't touch impl_.rs' doesn't read as touching it."""
    neg = re.compile(r"\b(never|don't|do not|not touch|untouched|hands-off|leave|outside|blocked|excluded|avoid|"
                     r"without|off limits|no code)\b", re.I)
    return " ".join(c for c in re.split(r"[;:()]|\.(?=\s|$)", text) if not neg.search(c))


def flip_failed(lines, failed):
    """Flip "- [started RID...] body" to "- [failed RID STAMP (gate: ERROR...)] body" for each run that ended failed.
    failed: {rid: (error, stamp)}. A note after the id ("; retry of failed X") is kept. Returns (new lines, [(idx, rid)])."""
    out, flipped = list(lines), []
    for i, line in enumerate(lines):
        m = re.match(r"^- \[started ([\w.-]+)([^\]]*)\] (.*)$", line)
        if not m or m.group(1) not in failed:
            continue
        err, stamp = failed[m.group(1)]
        why = re.sub(r"[\[\]\s]+", " ", err or "failed").strip()[:140]
        note = re.sub(r"^[,;]?\s*\d{1,2} \w{3} \d{2}:\d{2} SGT", "", m.group(2)).strip(" ,;")
        out[i] = f"- [failed {m.group(1)} {stamp} (tick: {why}{'; ' + note if note else ''})] {m.group(3)}"
        flipped.append((i, m.group(1)))
    return out, flipped


def flip_done(lines, done):
    """Flip "- [started RID...] body" to "- [done RID STAMP] body" ("[done RID STAMP: note]" when a note follows
    the id) for each run that ended done. done: {rid: stamp}. Returns (new lines, [(idx, rid)])."""
    out, flipped = list(lines), []
    for i, line in enumerate(lines):
        m = re.match(r"^- \[started ([\w.-]+)([^\]]*)\] (.*)$", line)
        if not m or m.group(1) not in done:
            continue
        rid, note, body = m.groups()
        note = re.sub(r"^[\s,;:]*(\d{1,2} \w{3} \d{2}:\d{2} SGT)?[\s,;:]*", "", note).rstrip()
        out[i] = f"- [done {rid} {done[rid]}{': ' + note if note else ''}] {body}"
        flipped.append((i, rid))
    return out, flipped


def replace_rules_block(text, block, begin, end):
    """text with only the lines strictly between `begin` and `end` replaced by block's inner lines.

    Every line outside the markers, and the marker lines themselves, stay byte for byte where they are, so a
    regeneration can never swallow, move or rewrite a hand-written line. None (nothing written) unless each
    marker appears exactly once, begin before end, and block itself is begin ... end."""
    lines = text.split("\n")
    if lines.count(begin) != 1 or lines.count(end) != 1:
        return None
    a, b = lines.index(begin), lines.index(end)
    if b < a or len(block) < 2 or block[0] != begin or block[-1] != end:
        return None
    inner = block[1:-1]
    if begin in inner or end in inner:
        return None
    return "\n".join(lines[:a + 1] + inner + lines[b:])


# ---------------- disk guard ----------------

DEFAULT_GUARD_FREE_GB = 10


def disk_guard_plan(free_gb, runs, limit=None):
    """Runs the disk guard stops: every live running run, when free_gb is under limit."""
    limit = DEFAULT_GUARD_FREE_GB if limit is None else limit
    if free_gb >= limit:
        return []
    return [r for r in runs if r.get("state") == "running" and r.get("alive")]


def pg_alive(pgid):
    """Is any process left in process group pgid?"""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


# ---------------- urgent webhook ----------------

def webhook_config(env_file, key_file, url=None, environ=None):
    """(url, key) or None. The key file (key only, 0600) wins; a legacy env file still works.
    The URL comes from `url`, then <ENV>_WEBHOOK_URL, then the env file. No URL is baked in: this repo is public."""
    cfg = {}
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    cfg[k.strip()] = v.strip().strip("'\"")
    key = cfg.get("WEBHOOK_KEY", "")
    if os.path.exists(key_file):
        with open(key_file) as f:
            key = f.read().strip()
    environ = os.environ if environ is None else environ
    url = url or environ.get("GATE_WEBHOOK_URL") or cfg.get("WEBHOOK_URL") or ""
    return (url, key) if key and url else None


def webhook_body(kind, ident, reason, when=None):
    m = re.search(r"#(\d+)$", ident or "")
    return {"kind": kind, "pr": int(m.group(1)) if m else None, "id": ident, "reason": reason,
            "time": when or now()}


def post_webhook(kind, ident, reason, cfg):
    """POST one urgent event. Returns a log-safe status line; never echoes the key or the body."""
    wh = webhook_config(cfg["env_file"], cfg["key_file"], cfg.get("url"), cfg.get("environ"))
    if not wh:
        return "webhook not configured"
    url, key = wh
    req = urllib.request.Request(url, data=json.dumps(webhook_body(kind, ident, reason)).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return f"webhook sent ({r.status})"
    except urllib.error.HTTPError as e:
        return f"webhook failed: HTTP {e.code}"
    except Exception as e:
        return f"webhook failed: {type(e).__name__}"
