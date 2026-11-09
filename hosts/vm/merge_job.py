#!/usr/bin/env python3
"""merge_job.py: the one scheduled merger for trycua/cua on the VM. Cron runs it every 15 minutes through
hosts/vm/cron-wrap.sh (see docs/HOST.md). It replaced the onigiri tick's merge stage on 6 Nov 2026.

For every open PR by ME it runs gate() (exactly three checks at the PR head: ci-gate SUCCESS, no hold row, and a
preapprove row pinned to the head for a declared deep/human tier) and blockers() (what stops any merge: draft,
conflict, mergeability not MERGEABLE, PAUSED). next_action() turns both into one action: merge, rerun a red
ci-gate's failed runs once per head, buffer for onigiri, or skip/wait. A draft is a skip: draft means not ready, and
only a person or the session that opened it readies one, so this job never marks a PR ready. Then the merge watch raises an
urgent unexpected-merge wake for a merge as ME that this job did not make.

  merge_job.py             one real run (cron, under flock)
  merge_job.py --dry-run   decide every PR, print would-<action>, change and write nothing
  merge_job.py --explain N every reason for trycua/cua#N and the action a run would take; read-only
  merge_job.py --webhook-test   one wiring-test POST to the urgent webhook

PAUSED (first line = reason) stops merge and rerun, and holds back webhook POSTs (the buffer item is still
written, so the hourly routine sees it; nothing re-sends it after the pause). Outputs: $ROOT/events.log (one line per decision, only
when it changes), $ROOT/buffer.jsonl (needs onigiri; urgent ones also POST the webhook), $ROOT/tick/state.json and
$ROOT/tick/last-run. ROOT is $ONIGIRI_ROOT, default /srv/onigiri.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import namedtuple
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # wake.py sits beside this file, also under python3 -I
import wake  # noqa: E402  the urgent webhook's URL, key file and POST

ROOT = os.path.abspath(os.environ.get("ONIGIRI_ROOT") or "/srv/onigiri")
DIR = f"{ROOT}/tick"   # the data dir the hourly routine and the mini mirror read; no code lives there any more
EVENTS, BUFFER, HOLDS_FILE = f"{ROOT}/events.log", f"{ROOT}/buffer.jsonl", f"{ROOT}/hold-prs.txt"
STATE, PAUSED, LAST = f"{DIR}/state.json", f"{DIR}/PAUSED", f"{DIR}/last-run"
REPO, SHORT, ME = "trycua/cua", "cua", "injaneity"
GATE_CHECK = "ci-gate"   # the one check the gate reads: it owns red checks and which platform lanes a PR needs
NOT_RERUN = {"cla"}      # not required on trycua/cua main: never rerun, never a reason to wait
WATCH_LIMIT = 500        # merged PRs one merge-watch fetch may return; a full page means the window was cut short
WATCH_OVERLAP_S = 6 * 3600   # the next window starts this long before this run, for GitHub search lag
TIERS = ["light", "standard", "deep", "human"]
HOLD_KINDS = ("hold", "hands-off-user", "skip-author", "review-only", "hands-off-path", "exception", "preapprove")
PATH_MODES = ("never", "split-undone")
SPLIT_PR = 2886
SGT = ZoneInfo("Asia/Singapore")
# cron's PATH is /usr/bin:/bin and gh lives in /usr/local/bin, so the job sets its own (the first cron run failed without it).
ENV = {**os.environ, "GH_CONFIG_DIR": os.path.expanduser("~/.config/gh"),
       "PATH": os.pathsep.join([os.path.expanduser("~/.local/bin"), "/usr/local/bin", os.environ.get("PATH") or "/usr/bin:/bin"])}

Reason = namedtuple("Reason", "code text")
# gate codes: ci | holds-unreadable | hold | hands-off | risk. blocker codes: draft | conflict | mergeable | paused


def now():
    return datetime.now(SGT).isoformat(timespec="seconds")


def read(path):
    with open(path) as f:
        return f.read()


# ---------------- holds file ----------------

def parse_holds(text):
    """The rows the gate reads. Every row is validated: a malformed one raises ValueError, because a hold must never
    be dropped silently. Rows only pickup reads (hands-off-user, skip-author, review-only, exception) are checked,
    not kept."""
    h = {"hold": set(), "path_modes": {}, "preapproved": {}}
    for ln, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        f = [x.strip() for x in line.split("|")]
        if len(f) < 4 or f[0] not in HOLD_KINDS or f[2] not in ("yes", "no") or not f[1]:
            raise ValueError(f"holds file line {ln}: {line[:80]}")
        kind, target = f[0], f[1]
        if kind == "exception" and (len(f) < 6 or not all(f[3:6])):
            raise ValueError(f"holds file line {ln}: exception needs match | scope | source")
        if kind == "preapprove":
            if not re.fullmatch(r"#?\d+", target) or not re.fullmatch(r"[0-9a-f]{40}", f[3]) or len(f) < 5 or not f[4]:
                raise ValueError(f"holds file line {ln}: preapprove needs #N | rules | <40-char sha> | source")
            n = int(target.lstrip("#"))
            if h["preapproved"].get(n, f[3]) != f[3]:
                raise ValueError(f"holds file line {ln}: two preapprove rows for #{n}")
            h["preapproved"][n] = f[3]
        elif kind == "hold":
            if not re.fullmatch(r"#?\d+", target):
                raise ValueError(f"holds file line {ln}: hold needs a PR number")
            h["hold"].add(int(target.lstrip("#")))
        elif kind == "hands-off-path":
            mode = f[3][len("mode="):].strip() if f[3].startswith("mode=") else "never"
            if mode not in PATH_MODES:
                raise ValueError(f"holds file line {ln}: unknown hands-off-path mode {mode!r}")
            if h["path_modes"].get(target) != "never":   # two rows for one path: the stricter (never) wins
                h["path_modes"][target] = mode
    return h


def path_hits(files, holds):
    """The hands-off paths (matched by file name) that the PR's changed files hit, in row order."""
    return [p for p in holds["path_modes"] if any(re.search(r"(^|/)" + re.escape(p) + "$", f) for f in files)]


# split-undone (Zane 20 Oct): an impl_.rs PR is held only when its diff undoes the #2886 per-tool split.
_MOD_DECL = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(\w+)\s*;")
_WIRING = re.compile(r"^\s*(#\[path\b|include!)")
_USE_SUPER = re.compile(r"^\s*(pub(\([^)]*\))?\s+)?use\s+super::\{")
_TOOL_NAME = re.compile(r"\b([A-Z]\w*Tool)\b")
_TOOL_IMPORT = re.compile(r"^\s*([a-z_]\w*::)?\{?\s*([A-Z]\w*Tool\s*,\s*)*[A-Z]\w*Tool\s*,?\s*\}?,?\s*$")
_TOOL_DEF = re.compile(r"^\s*(impl(<[^>]*>)?\s+Tool\s+for\s+\w+Tool\b|(pub(\([^)]*\))?\s+)?struct\s+\w+Tool\b)")
MOVED_BACK_MIN = 3


def _lines(patch, sign):
    return [x[1:] for x in (patch or "").split("\n") if x.startswith(sign) and not x.startswith(sign * 3 + " ")]


def split_undone(files, path):
    """Why the diff undoes the #2886 split of `path`, or None. files: GitHub pulls/N/files entries (filename,
    status, patch, previous_filename). With D the changed impl_.rs's directory, the diff undoes the split if it
    deletes or renames impl_.rs or a file under D, drops a `mod x;` from impl_.rs or D/mod.rs, adds or drops a
    #[path]/include! line, drops impl_.rs's `use super::{` line or a tool import, adds a tool type to impl_.rs, or
    moves MOVED_BACK_MIN lines from files under D into impl_.rs. A missing patch cannot be checked and counts."""
    for h in [f for f in files if re.search(r"(^|/)" + re.escape(path) + "$", f["filename"])]:
        if h.get("status") in ("removed", "renamed"):
            return f"{h['status']} {h.get('previous_filename') or h['filename']}"
        d = h["filename"].rsplit("/", 1)[0] + "/" if "/" in h["filename"] else ""
        sib = [f for f in files if f is not h and (f["filename"].startswith(d) or (f.get("previous_filename") or "").startswith(d))]
        for f in sib:
            old = f.get("previous_filename") or f["filename"]
            if f.get("status") == "removed" or (f.get("status") == "renamed" and old.startswith(d)):
                return f"{'deletes' if f.get('status') == 'removed' else 'renames'} split module {old}"
        for f in [h] + sib:
            if f.get("patch") is None:
                return f"no diff from GitHub for {f['filename']} (cannot check; buffered to be safe)"
        add, rem = _lines(h["patch"], "+"), _lines(h["patch"], "-")
        wiring = [(h["filename"], add, rem)] + [(f["filename"], _lines(f["patch"], "+"), _lines(f["patch"], "-"))
                                                for f in sib if f["filename"] == d + "mod.rs"]
        for name, a, r in wiring:
            mods = {m.group(1) for x in r if (m := _MOD_DECL.match(x))} - {m.group(1) for x in a if (m := _MOD_DECL.match(x))}
            if mods:
                return f"rewrites the split's module wiring in {name} (drops mod {', '.join(sorted(mods))})"
            bad = next((x for x in a + r if _WIRING.match(x)), None) or \
                next((x for x in r if name == h["filename"] and _USE_SUPER.match(x)), None)
            if bad:
                return f"rewrites the split's module wiring in {name} ({bad.strip()[:80]!r})"
        dropped = {t for x in rem if _TOOL_IMPORT.match(x) for t in _TOOL_NAME.findall(x)} - \
            {t for x in add if _TOOL_IMPORT.match(x) or re.match(r"\s*(pub(\([^)]*\))?\s+)?use\s", x) for t in _TOOL_NAME.findall(x)}
        if dropped:
            return f"rewrites the split's module wiring in {h['filename']} (drops the import of {', '.join(sorted(dropped))})"
        bad = next((x for x in add if _TOOL_DEF.match(x)), None)
        if bad:
            return f"re-inlines a tool into {h['filename']} ({bad.strip()[:80]!r})"
        gone = {x.strip() for f in sib for x in _lines(f.get("patch"), "-")}
        back = {x.strip() for x in add if len(re.sub(r"//.*|[\s{}()\[\];,]", "", x)) >= 8} & gone
        if len(back) >= MOVED_BACK_MIN:
            return f"re-inlines {len(back)} lines removed from its per-tool modules into {h['filename']}"
    return None


def hands_off_verdict(paths, patches, holds):
    """(held, why) for the hands-off paths a PR hits. patches() gives pulls/N/files; it raising means held."""
    allowed = []
    for p in paths:
        if holds["path_modes"].get(p, "never") == "never":
            return True, f"touches {p} (hands-off path, mode never; see the holds file)"
        try:
            why = split_undone(patches(), p)
        except Exception as e:
            return True, f"touches {p}: diff unreadable, cannot check the #{SPLIT_PR} per-tool split ({str(e)[:100]}); buffered to be safe"
        if why:
            return True, f"touches {p}: undoes the #{SPLIT_PR} per-tool split: {why} (hands-off path, mode split-undone; see the holds file)"
        allowed.append(f"touches {p}: does not undo the #{SPLIT_PR} per-tool split (hands-off path, mode split-undone)")
    return False, "; ".join(allowed)


# ---------------- checks ----------------

def norm_tier(t):
    """A declared tier; a word that is not one counts as human, so a typo or an old name never skips a preapproval."""
    t = (t or "").strip().lower()
    return t if t in TIERS else "human"


def declared_risk(labels, body):
    """The strictest tier among risk:<tier> labels and `risk:` body lines, or None."""
    found = [norm_tier(x.split(":", 1)[1]) for x in labels if x.startswith("risk:")]
    found += [norm_tier(t) for t in re.findall(r"^risk: *(\w+)", body or "", re.M)]
    known = [t for t in found if t]
    return max(known, key=TIERS.index) if known else None


def check_name(c):
    return c.get("name") or c.get("context") or ""


def is_status(c):
    return c.get("__typename") == "StatusContext" or ("state" in c and "status" not in c)


def latest_checks(rollup):
    """One entry per (workflow, name), the latest started: a rerun supersedes the run it replaced."""
    best = {}
    for c in rollup or []:
        key, stamp = (c.get("workflowName") or "", check_name(c)), c.get("startedAt") or c.get("createdAt") or ""
        if key not in best or stamp >= (best[key].get("startedAt") or best[key].get("createdAt") or ""):
            best[key] = c
    return list(best.values())


def conclusion(c):
    if is_status(c):
        return "PENDING" if c.get("state") in ("PENDING", "EXPECTED") else c.get("state")
    return c.get("conclusion") if c.get("status") == "COMPLETED" else "PENDING"


def check_state(rollup):
    """('empty'|'pending'|'failing'|'red'|'green', [names], {failing Actions run ids}) over the whole rollup. Only
    next_action() reads it, to choose between a rerun and a wait; the gate reads ci-gate alone."""
    pending, bad, runs, ok = [], [], set(), 0
    for c in latest_checks(rollup):
        if check_name(c).lower() in NOT_RERUN:
            continue
        st = conclusion(c)
        if st == "PENDING":
            pending.append(check_name(c))
        elif st not in ("SUCCESS", "NEUTRAL", "SKIPPED") or (is_status(c) and st != "SUCCESS"):
            bad.append(check_name(c))
            m = None if is_status(c) else re.search(r"/actions/runs/(\d+)", c.get("detailsUrl") or "")
            if m:
                runs.add(m.group(1))
        elif st == "SUCCESS":
            ok += 1
    if pending:
        return ("failing", bad, set()) if bad else ("pending", pending, set())
    if not bad:
        return ("green", [], set()) if ok else ("empty", [], set())
    return "red", bad, runs


def gate(pr, holds, hands_off=None, notes=None):
    """The merge gate: exactly three checks at the PR head, every one run, returning [Reason] (empty = pass).
    1. ci-gate SUCCESS (SKIPPED, NEUTRAL, pending or missing is not; other checks are ci-gate's business).
    2. No hold row: hold, or a hands-off-path the diff hits (hands_off() -> None or (held, why)).
       holds None (the file is unreadable) holds every PR.
    3. A declared deep/human tier has a preapprove row at exactly this head. An undeclared tier does not block."""
    notes = [] if notes is None else notes
    R, n, head = [], pr.get("number"), pr["headRefOid"]
    ci = [conclusion(c) for c in latest_checks(pr.get("statusCheckRollup")) if check_name(c) == GATE_CHECK]
    if ci and all(s == "SUCCESS" for s in ci):
        notes.append("ci-gate SUCCESS")
    else:
        R.append(Reason("ci", f"{GATE_CHECK} {'/'.join(sorted(set(ci))) or 'missing'}"))
    if holds is None:
        R.append(Reason("holds-unreadable", "holds file unreadable: every PR counts as held"))
    elif n in holds["hold"]:
        R.append(Reason("hold", f"hold: #{n} has a hold row in the holds file"))
    elif hands_off and (hit := hands_off()):
        if hit[0]:
            R.append(Reason("hands-off", hit[1]))
        else:
            notes.append(hit[1])
    tier, pre = declared_risk(pr.get("labels") or [], pr.get("body")), (holds or {}).get("preapproved", {}).get(n)
    if tier in ("deep", "human") and pre != head:
        R.append(Reason("risk", f"declared {tier}: needs Zane"
                        + (f" (preapproved at {pre[:9]}, head is {head[:9]})" if pre else " (no preapprove row)")))
    else:
        notes.append(f"risk {tier or 'undeclared'}" + (f", preapproved at {head[:9]}" if tier in ("deep", "human") else ""))
    return R


def blockers(pr, paused):
    """What stops this job acting at all, outside the gate: a draft is not ready, GitHub cannot merge a conflict or
    an unknown mergeability, and PAUSED stops every action."""
    R = [Reason("draft", "draft")] if pr.get("isDraft") else []
    if pr.get("mergeable") == "CONFLICTING":
        R.append(Reason("conflict", "merge conflict"))
    elif pr.get("mergeable") != "MERGEABLE":
        R.append(Reason("mergeable", f"mergeable {pr.get('mergeable')}"))
    if paused:
        R.append(Reason("paused", f"tick paused: {paused}"))
    return R


SKIP_CODES = {"holds-unreadable", "hold", "draft", "paused"}   # logged, nobody woken
BUFFER_CODES = {"hands-off", "risk", "conflict"}      # needs onigiri or Zane, buffered once per head


def next_action(reasons, notes, pr, rerun_head=None):
    """(action, why): merge | rerun | buffer | wait | skip. why names every blocking reason."""
    codes = {r.code for r in reasons}
    why = "; ".join(r.text for r in reasons) or ", ".join(notes)
    if not reasons:
        return "merge", "green, " + ", ".join(notes)
    if codes & SKIP_CODES:
        return "skip", why
    if codes & BUFFER_CODES:
        return "buffer", why
    if "ci" not in codes:
        return "wait", why
    state, names, runs = check_state(pr.get("statusCheckRollup"))
    if state == "red":
        if rerun_head != pr["headRefOid"] and runs:
            return "rerun", "red once: " + ", ".join(sorted(names))[:150]
        return "buffer", "red after rerun: " + ", ".join(sorted(names))[:150]
    return "wait", why


def merge_audit(merged, job_merged, known, me=ME):
    """Merges this job did not make -> [(n, alert|log|ignore, by)]: alert for one as `me` (a hand merge or GitHub
    auto-merge), log for another human, ignore a bot."""
    out = []
    for p in sorted(merged, key=lambda p: p.get("mergedAt") or ""):
        n, by = p["number"], (p.get("mergedBy") or {}).get("login") or ""
        if n in job_merged or str(n) in known:
            continue
        bot = (p.get("mergedBy") or {}).get("is_bot") or by.startswith("app/") or by.endswith("[bot]")
        out.append((n, "ignore" if bot else "alert" if by == me else "log", by))
    return out


def job_merges(events):
    """PR numbers this job (or the tick before it) merged: its `merged cua#N` events."""
    return {int(m.group(1)) for x in events if (m := re.search(r"^\S+ merged cua#(\d+) ", x))}


# ---------------- side effects ----------------

def sh(args, timeout=120):
    r = subprocess.run(args, env=ENV, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def gh_json(args):
    for attempt in range(3):
        rc, out, err = sh(["gh", *args])
        if not rc or not re.search(r"HTTP 5\d\d|timeout|TLS|connection", err, re.I):
            break
        time.sleep(5 * (attempt + 1))
    if rc:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {err.strip()[:200]}")
    return json.loads(out) if out.strip() else None


def post_webhook(kind, ident, reason):
    """POST one urgent event. Returns a log-safe status line; never echoes the key."""
    m = re.search(r"#(\d+)$", ident or "")
    return wake.send({"kind": kind, "pr": int(m.group(1)) if m else None, "id": ident, "reason": reason, "time": now()},
                     attempts=1, timeout=8)


class Job:
    def __init__(self, dry):
        self.dry = dry
        self.paused = (read(PAUSED).strip().splitlines() or ["paused"])[0] if os.path.exists(PAUSED) else None
        self.can_act = not dry and not self.paused   # merge, rerun
        s = json.loads(read(STATE)) if os.path.exists(STATE) else {}   # a corrupt state file stops the run
        self.s = {k: s.get(k, {}) for k in ("buffered", "rerun", "last_reason", "merges")}
        self.errors = 0

    def event(self, kind, ident, reason):
        line = f"{now()} {kind} {ident} {reason}".replace("\n", " ")
        print(line)
        if not self.dry:
            with open(EVENTS, "a") as f:
                f.write(line + "\n")

    def decide(self, kind, ident, head, reason):
        """Log a recurring decision only when it changes for this id."""
        sig = f"{kind}|{head}|{reason}"
        if self.s["last_reason"].get(ident) != sig:
            self.s["last_reason"][ident] = sig
            self.event(kind, ident, reason)

    def buffer(self, kind, ident, head, reason, urgent=False):
        key = f"{kind}|{ident}|{head or '-'}"
        if key in self.s["buffered"]:
            return False
        m = re.fullmatch(rf"{SHORT}#(\d+)", ident or "")
        if m and kind != "unexpected-merge":   # re-read: a PR can merge or close mid-run (#6026)
            try:
                st = (gh_json(["pr", "view", m.group(1), "-R", REPO, "--json", "state"]) or {}).get("state")
            except Exception:
                st = None   # unknown counts as open, so no alert is lost
            if st in ("CLOSED", "MERGED"):
                self.decide("skipped", ident, head, f"{kind}: PR {st.lower()}, no alert")
                return False
        self.s["buffered"][key] = now()
        item = {"time": now(), "kind": kind, "id": ident, "head": head, "reason": reason, **({"urgent": True} if urgent else {})}
        if not self.dry:
            with open(BUFFER, "a") as f:
                f.write(json.dumps(item) + "\n")
        self.event("urgent" if urgent else "buffer", ident, f"{kind}: {reason}")
        if urgent:
            sent = "not sent: dry run" if self.dry else "not sent: paused" if self.paused else post_webhook(kind, ident, reason)
            self.event("urgent", ident, sent)
        return True

    def holds(self):
        try:
            return parse_holds(read(HOLDS_FILE))
        except Exception as e:   # never act without holds: gate() holds every PR until the file reads again
            self.errors += 1
            self.event("error", "holds", f"{type(e).__name__}: {str(e)[:200]}")
            self.buffer("holds-unreadable", "holds", now()[:13], f"{HOLDS_FILE}: {str(e)[:150]}", urgent=True)
            return None

    def evaluate(self, p, holds):
        """(reasons, notes) for one PR: gate() then blockers(). The run and --explain both call this."""
        n = p["number"]
        p["labels"] = [x["name"] if isinstance(x, dict) else x for x in p.get("labels") or []]
        p.update(gh_json(["pr", "view", str(n), "-R", REPO, "--json", "mergeable,statusCheckRollup,files"]) or {})
        p["files"] = [f["path"] if isinstance(f, dict) else f for f in p.get("files") or []]
        cache = {}

        def patches():
            if "f" not in cache:
                pages = gh_json(["api", f"repos/{REPO}/pulls/{n}/files?per_page=100", "--paginate", "--slurp"]) or []
                cache["f"] = [f for page in pages for f in page]
            return cache["f"]
        hits = path_hits(p["files"], holds) if holds else []
        notes = []
        reasons = gate(p, holds, (lambda: hands_off_verdict(hits, patches, holds)) if hits else None, notes)
        return reasons + blockers(p, self.paused), notes

    def merges(self, holds):
        prs = gh_json(["pr", "list", "-R", REPO, "--state", "open", "--author", ME, "--limit", "100",
                       "--json", "number,headRefOid,isDraft,labels,body,title"]) or []
        for p in prs:
            ident = f"{SHORT}#{p['number']}"
            try:
                reasons, notes = self.evaluate(p, holds)
                act, why = next_action(reasons, notes, p, self.s["rerun"].get(ident))
                if self.dry:   # one line per PR, even when the decision has not changed since the last run
                    print(f"dry-run {ident} {p['headRefOid'][:9]} {act}: {why}"[:400])
                self.apply(ident, p["number"], p["headRefOid"], act, why)
            except Exception as e:   # one PR's failure must not skip the rest
                self.errors += 1
                self.event("error", ident, f"merge: {type(e).__name__}: {str(e)[:150]}")

    def apply(self, ident, n, head, act, why):
        if act in ("skip", "wait"):
            return self.decide("skipped", ident, head, why)
        if act == "buffer":
            return self.buffer("merge-blocked", ident, head, why)
        if not self.can_act:
            return self.decide("would-" + act, ident, head, why)
        if act == "rerun":
            _, _, runs = check_state(gh_json(["pr", "view", str(n), "-R", REPO, "--json", "statusCheckRollup"])["statusCheckRollup"])
            for rid in sorted(runs):
                sh(["gh", "run", "rerun", rid, "--failed", "-R", REPO])
            self.s["rerun"][ident] = head
            return self.event("rerun", ident, f"{len(runs)} run(s) at {head[:9]}: {why}")
        rc, _, err = sh(["gh", "pr", "merge", str(n), "-R", REPO, "--squash", "--match-head-commit", head], timeout=180)
        if rc:
            return self.buffer("merge-failed", ident, head, f"gh pr merge: {err.strip()[:200]}")
        return self.event("merged", ident, f"squash at {head[:9]}: {why}")

    def merge_watch(self):
        """Urgent unexpected-merge wake for a trycua/cua PR merged as ME that this job did not merge. Its `merged
        cua#N` events are the whole allow list. Covers merges since state merges.since: set on the first run, and moved
        up to WATCH_OVERLAP_S before this run only after a fetch that was not cut short, so no merge falls between two
        windows; merges.seen keeps the ones already handled inside the overlap."""
        mw = self.s["merges"]
        known = mw.setdefault("seen", {})
        if not mw.get("since"):
            mw["since"] = now()
            return
        since = datetime.fromisoformat(mw["since"]).astimezone(ZoneInfo("UTC"))
        start = datetime.fromtimestamp(time.time() - WATCH_OVERLAP_S, SGT).isoformat(timespec="seconds")
        merged = gh_json(["pr", "list", "-R", REPO, "--state", "merged", "--limit", str(WATCH_LIMIT), "--search",
                          f"merged:>={since.date().isoformat()}", "--json", "number,mergedBy,mergedAt,title,mergeCommit"]) or []
        at_of = lambda p: datetime.fromisoformat(p["mergedAt"].replace("Z", "+00:00"))   # noqa: E731
        by_n = {p["number"]: p for p in merged if at_of(p) >= since}
        events = read(EVENTS).splitlines() if os.path.exists(EVENTS) else []
        for n, act, by in merge_audit(list(by_n.values()), job_merges(events), known):
            p, ident = by_n[n], f"{SHORT}#{n}"
            at = at_of(p).astimezone(SGT).isoformat(timespec="seconds")
            sha = (p.get("mergeCommit") or {}).get("oid") or "-"
            why = f"merged by {by} at {at} as {sha[:9]}, not by the tick: {(p.get('title') or '')[:100]}"
            if act == "alert":
                self.buffer("unexpected-merge", ident, sha, why, urgent=True)
            elif act == "log":
                self.event("merge-noted", ident, why)
            known[str(n)] = {"by": by, "at": at, "outcome": act}
        if len(merged) >= WATCH_LIMIT:   # merges past the page are unseen: keep the window and wake a person, once per window
            self.errors += 1
            why = f"{len(merged)} merged PRs since {mw['since']}, the fetch limit: check merges as {ME} by hand, then move merges.since"
            self.event("error", "merge-watch", why)
            return self.buffer("merge-watch-overflow", "merge-watch", mw["since"], why, urgent=True)
        mw["since"] = max(mw["since"], start)
        mw["seen"] = {k: v for k, v in known.items() if v.get("at", "") >= mw["since"]}   # older ones are outside the window

    def run(self):
        if self.paused:
            self.decide("paused", "tick", "-", self.paused)
        elif self.s["last_reason"].pop("tick", None):
            self.event("resumed", "tick", "PAUSED removed")
        holds = self.holds()
        for name, fn in (("merge", lambda: self.merges(holds)), ("merge-watch", self.merge_watch)):
            try:
                fn()
            except Exception as e:
                self.errors += 1
                self.event("error", name, f"{type(e).__name__}: {str(e)[:200]}")
        if self.dry:
            return
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.s, f, indent=1, sort_keys=True)
        os.replace(tmp, STATE)
        with open(LAST, "w") as f:
            f.write(f"{now()} {'ok' if not self.errors else f'{self.errors} error(s)'}{' paused' if self.paused else ''}\n")

    def explain(self, n):
        """--explain N: every reason for one PR and the action a run would take. Records nothing."""
        p = gh_json(["pr", "view", str(n), "-R", REPO, "--json", "number,headRefOid,isDraft,labels,body,title,state,author"])
        try:
            holds = parse_holds(read(HOLDS_FILE))
        except Exception as e:
            holds = None
            print(f"holds file unreadable: {e}")
        reasons, notes = self.evaluate(p, holds)
        act, why = next_action(reasons, notes, p, self.s["rerun"].get(f"{SHORT}#{n}"))
        gate_codes = {"ci", "holds-unreadable", "hold", "hands-off", "risk"}
        lines = [f"{SHORT}#{n} {p.get('state', '')} by {(p.get('author') or {}).get('login', '?')} at {p['headRefOid']}: "
                 f"{(p.get('title') or '')[:90]}", f"gate: {'pass' if not gate_codes & {r.code for r in reasons} else 'fail'}"]
        lines += [f"  {'gate' if r.code in gate_codes else 'blocker'} [{r.code}] {r.text}" for r in reasons]
        lines += [f"  note {x}" for x in notes]
        lines.append(f"next action: {act}: {why}"[:400])
        print("\n".join(lines))
        return act == "merge"


def main(argv=None):
    p = argparse.ArgumentParser(prog="merge_job.py", add_help=False, allow_abbrev=False,
                                description="trycua/cua merge job. A bare run is one real run (cron, under flock).")
    m = p.add_mutually_exclusive_group()
    m.add_argument("--dry-run", action="store_true", help="decide everything, change nothing, write nothing")
    m.add_argument("--explain", type=int, metavar="N", help="every reason for trycua/cua#N; read-only")
    m.add_argument("--webhook-test", action="store_true", help="one wiring-test POST")
    # No automatic --help: a usage probe must never run a merge, so any unknown argument prints usage and exits 2.
    p.error = lambda msg: (p.print_help(sys.stderr), p.exit(2, f"merge_job.py: error: {msg}\n"))
    a = p.parse_args(sys.argv[1:] if argv is None else argv)
    if a.explain is not None:
        sys.exit(0 if Job(dry=True).explain(a.explain) else 1)
    elif a.webhook_test:
        print(post_webhook("test", None, "wiring test from the merge job, ignore"))
    else:
        Job(dry=a.dry_run).run()


if __name__ == "__main__":
    main()
