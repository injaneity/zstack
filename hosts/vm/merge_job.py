#!/usr/bin/env python3
"""merge_job.py: the one scheduled merger for trycua/cua on the VM. Cron runs it every 15 minutes through
hosts/vm/cron-wrap.sh (see docs/HOST.md). It replaced the onigiri tick's merge stage on 6 Nov 2026.

A bare run first moves the package checkout to origin/main (bin/ensure-package-main) and restarts on it, then runs
hosts/check-parity: a host whose package, hook or agents differ from main raises an urgent parity wake.

For every open PR by ME it runs gate() (exactly three checks at the PR head: ci-gate SUCCESS, no hold row, and a
risk tier from TIERS, with a preapprove row pinned to the head for deep/human) and blockers() (what stops any merge: draft,
conflict, mergeability not MERGEABLE, PAUSED). A mergeability GitHub has not computed yet (UNKNOWN) is read again in the
same run when it is the only thing in the way. next_action() turns both into one action: merge, rerun a red
ci-gate's failed runs once per head, or wait, with who must act: zane (a hold, PAUSED, a hands-off path, a deep/human
tier with no preapproval at the head), session (no tier, a conflict, red after its rerun) or none. The job's own entry in GitHub's merge queue
(state.json queued, enqueued as ME at this head) that now fails the gate or meets PAUSED is dequeued. Another actor's
entry is left alone; its merge wakes through the merge watch. A draft is a wait: draft means not ready, and
only a person or the session that opened it readies one, so this job never marks a PR ready. Then the merge watch raises an
urgent unexpected-merge wake for every merge of a PR by ME that this job did not make, whoever merged it (a hand merge, a
teammate, a bot or an app). The job's own merges are the heads in state.json landed and queued.

  merge_job.py             one real run (cron, under flock)
  merge_job.py --dry-run   decide every PR, print would-<action>, change and write nothing
  merge_job.py --explain N every reason for trycua/cua#N and the action a run would take; read-only
  merge_job.py --webhook-test   one wiring-test POST to the urgent webhook

PAUSED (first line = reason) stops merge and rerun, and holds back webhook POSTs: events.log still has the wake, and
the first pass after the pause sends it. An urgent wake (URGENT) POSTs the webhook once per kind, PR and key, retried
every pass until it is sent (a host with no webhook key counts as sent). A wait goes
nowhere but events.log and --dry-run's `who=` lines, which bin/hourly prints under needs-Zane and unstick. Outputs:
$ROOT/events.log (one line per decision, only when it changes), $ROOT/tick/state.json and $ROOT/tick/last-run. ROOT is
$ONIGIRI_ROOT, default /srv/onigiri.
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
EVENTS, HOLDS_FILE = f"{ROOT}/events.log", f"{ROOT}/hold-prs.txt"
STATE, PAUSED, LAST = f"{DIR}/state.json", f"{DIR}/PAUSED", f"{DIR}/last-run"
REPO, SHORT, ME = "trycua/cua", "cua", "injaneity"
GATE_CHECK = "ci-gate"   # the one check the gate reads: it owns red checks and which platform lanes a PR needs
NOT_RERUN = {"cla"}      # not required on trycua/cua main: never rerun, never a reason to wait
WATCH_LIMIT = 500        # merged PRs one merge-watch fetch may return; a full page means the window was cut short
WATCH_OVERLAP_S = 6 * 3600   # the next window starts this long before this run, for GitHub search lag
TIERS = ["light", "standard", "deep", "human"]   # the one risk enum; a PR declaring none of these never merges
MERGEABLE_REREADS, MERGEABLE_WAIT_S = 3, 5   # GitHub computes mergeability on the first ask, so UNKNOWN is read again
PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SGT = ZoneInfo("Asia/Singapore")
# cron's PATH is /usr/bin:/bin and gh lives in /usr/local/bin, so the job sets its own (the first cron run failed without it).
ENV = {**os.environ, "GH_CONFIG_DIR": os.path.expanduser("~/.config/gh"),
       "PATH": os.pathsep.join([os.path.expanduser("~/.local/bin"), "/usr/local/bin", os.environ.get("PATH") or "/usr/bin:/bin"])}

Reason = namedtuple("Reason", "code text")
# gate codes: ci | holds-unreadable | hold | hands-off | no-tier | risk. blocker codes: draft | conflict | mergeable | paused


def now():
    return datetime.now(SGT).isoformat(timespec="seconds")


def read(path):
    with open(path) as f:
        return f.read()


# ---------------- holds file ----------------

def parse_holds(text):
    """The rows the gate reads: hold, preapprove and hands-off-path. A malformed one raises ValueError, and so does a
    row of any other kind that names a PR number, because a hold must never be dropped silently. A row about a login
    is the pickup's and is not read; neither is the third column."""
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
            if mode == "never":   # split-undone is retired (Zane 10 Nov): its row holds nothing, a hold row stops a PR
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


# ---------------- checks ----------------

RISK = re.compile(rf"risk: *({'|'.join(TIERS)})\b")


def declared_risk(labels, body):
    """The strictest tier among risk:<tier> labels and `risk:` body lines, or None. Only TIERS words count."""
    found = [m.group(1) for x in labels if (m := RISK.fullmatch(x.replace("risk:", "risk: ", 1)))]
    found += RISK.findall("\n".join(line for line in (body or "").splitlines() if line.startswith("risk:")))
    return max(found, key=TIERS.index) if found else None


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


def gate(pr, holds, notes=None):
    """The merge gate: exactly three checks at the PR head, every one run, returning [Reason] (empty = pass).
    1. ci-gate SUCCESS (SKIPPED, NEUTRAL, pending or missing is not; other checks are ci-gate's business).
    2. No hold row: hold, or a hands-off-path that names one of the PR's changed files.
       holds None (the file is unreadable) holds every PR.
    3. A tier from TIERS is declared, and a deep/human one has a preapprove row at exactly this head."""
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
    elif hit := [p for p in holds["paths"] if any(re.search(r"(^|/)" + re.escape(p) + "$", f) for f in pr.get("files") or [])]:
        R.append(Reason("hands-off", f"touches {hit[0]} (hands-off path, mode never; see the holds file)"))
    tier, pre = declared_risk(pr.get("labels") or [], pr.get("body")), (holds or {}).get("preapproved", {}).get(n)
    if tier is None:
        R.append(Reason("no-tier", f"no risk tier ({'|'.join(TIERS)})"))
    elif tier in ("deep", "human") and pre != head:
        R.append(Reason("risk", f"declared {tier}: needs Zane"
                        + (f" (preapproved at {pre[:9]}, head is {head[:9]})" if pre else " (no preapprove row)")))
    else:
        notes.append(f"risk {tier}" + (f", preapproved at {head[:9]}" if tier in ("deep", "human") else ""))
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


URGENT = ("unexpected-merge", "parity", "holds-unreadable", "merge-watch-overflow", "merge-failed")   # the kinds that POST
ZANE_CODES = {"paused", "holds-unreadable", "hold", "hands-off", "risk"}   # only Zane moves these
SESSION_CODES = {"no-tier", "conflict"}                                   # the session that opened the PR fixes these


def next_action(reasons, notes, pr, rerun_head=None):
    """(action, who, why): merge | rerun | wait, and who must act for a wait: zane | session | none. A draft waits on
    nobody, because whoever opened it readies it when it is ready. why names every blocking reason."""
    codes = {r.code for r in reasons}
    why = "; ".join(r.text for r in reasons) or ", ".join(notes)
    if not reasons:
        return "merge", "none", "green, " + ", ".join(notes)
    if "draft" in codes:
        return "wait", "none", why
    if codes & ZANE_CODES:
        return "wait", "zane", why
    if codes & SESSION_CODES:
        return "wait", "session", why
    if "ci" not in codes:
        return "wait", "none", why
    state, names, runs = check_state(pr.get("statusCheckRollup"))
    if state == "red":
        if rerun_head != pr["headRefOid"] and runs:
            return "rerun", "none", "red once: " + ", ".join(sorted(names))[:150]
        return "wait", "session", "red after rerun: " + ", ".join(sorted(names))[:150]
    return "wait", "none", why


DEQUEUE_CODES = {"ci", "holds-unreadable", "hold", "hands-off", "no-tier", "risk", "paused"}   # off the gate, or PAUSED
QUEUE_ENTRY = ("query($n: Int!) { repository(owner: \"trycua\", name: \"cua\") { pullRequest(number: $n) { id "
               "mergeQueueEntry { enqueuer { login } headCommit { oid } } } } }")
DEQUEUE = "mutation($id: ID!) { dequeuePullRequest(input: {id: $id}) { clientMutationId } }"


def should_dequeue(reasons, queued, entry, head, me=ME):
    """True when GitHub's queue holds the job's own entry for this head and a gate reason or PAUSED now stands against it.
    queued is state.json's queued record for the PR; entry is the PR's mergeQueueEntry, None when it is not queued."""
    if not entry or not queued or queued.get("head") != head:
        return False
    if (entry.get("enqueuer") or {}).get("login") != me or (entry.get("headCommit") or {}).get("oid") != head:
        return False
    return bool({r.code for r in reasons} & DEQUEUE_CODES)


def merge_audit(merged, job_heads, known):
    """Merges of our PRs this job did not make -> [(n, by)], oldest first. job_heads is {n: head} from state.json landed
    and queued; a head kept short (seeded from events.log) matches as a prefix. known is the ones already handled."""
    out = []
    for p in sorted(merged, key=lambda p: p.get("mergedAt") or ""):
        n, head = p["number"], p.get("headRefOid") or ""
        if str(n) in known or (job_heads.get(n) and head.startswith(job_heads[n])):
            continue
        out.append((n, (p.get("mergedBy") or {}).get("login") or "?"))
    return out


def seed_landed(events):
    """state.json landed before it existed: the latest head events.log says the job queued (`merge-queued cua#N squash at
    <head>`). A `merged` line counts only for a PR with no `merge-queued` line (the tick before #42 logged merged at
    enqueue), because since #42 it names GitHub's head, which may be a push the job never queued."""
    out, queued = {}, set()
    for x in events:
        if m := re.match(r"(\S+) (merged|merge-queued) cua#(\d+) squash at ([0-9a-f]+)", x):
            at, kind, n, head = m.groups()
            if kind == "merge-queued" or n not in queued:
                out[n] = {"head": head, "at": at}
            queued |= {n} if kind == "merge-queued" else set()
    return out


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


def sha():
    """The zstack commit this run is on; last-run and stderr (cron's tick.log) carry it."""
    return subprocess.run(["git", "-C", PKG, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or "unknown"


def parity_problem():
    """Why this host is not merged main and nothing else (hosts/check-parity's FAIL lines), or None."""
    try:
        r = subprocess.run([f"{PKG}/hosts/check-parity"], env={**ENV, "ONIGIRI_ROOT": ROOT}, capture_output=True, text=True, timeout=300)
    except Exception as e:
        return f"check-parity did not run: {type(e).__name__}: {str(e)[:150]}"
    return None if r.returncode == 0 else "; ".join(x for x in (r.stdout + r.stderr).splitlines() if x.startswith("FAIL"))[:300] \
        or f"check-parity exit {r.returncode}"


class Job:
    def __init__(self, dry):
        self.dry = dry
        self.paused = (read(PAUSED).strip().splitlines() or ["paused"])[0] if os.path.exists(PAUSED) else None
        self.can_act = not dry and not self.paused   # merge, rerun
        s = json.loads(read(STATE)) if os.path.exists(STATE) else {}   # a corrupt state file stops the run
        self.s = {k: s.get(k, {}) for k in ("woken", "rerun", "last_reason", "merges", "queued", "landed")}
        if "woken" not in s:   # once: the urgent keys the buffer had, so no wake goes out twice; the rest of it is dropped
            self.s["woken"] = {k: v for k, v in s.get("buffered", {}).items() if k.split("|")[0] in URGENT}
        if "landed" not in s:   # once, on the first run with this field
            self.s["landed"] = seed_landed(read(EVENTS).splitlines() if os.path.exists(EVENTS) else [])
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

    def wake(self, kind, ident, head, reason):
        """POST one urgent wake for this kind, id and head (or hour, or window), once it is sent: a failed, paused or dry
        POST is tried again next pass. Returns whether it is handled: sent now or before, or not needed (the PR closed)."""
        assert kind in URGENT, kind
        key = f"{kind}|{ident}|{head or '-'}"
        if key in self.s["woken"]:
            return True
        m = re.fullmatch(rf"{SHORT}#(\d+)", ident or "")
        if m and kind != "unexpected-merge":   # re-read: a PR can merge or close mid-run (#6026)
            try:
                st = (gh_json(["pr", "view", m.group(1), "-R", REPO, "--json", "state"]) or {}).get("state")
            except Exception:
                st = None   # unknown counts as open, so no alert is lost
            if st in ("CLOSED", "MERGED"):
                self.decide("skipped", ident, head, f"{kind}: PR {st.lower()}, no alert")
                return True
        self.event("urgent", ident, f"{kind}: {reason}")
        sent = "not sent: dry run" if self.dry else "not sent: paused" if self.paused else post_webhook(kind, ident, reason)
        self.event("urgent", ident, sent)
        if sent.startswith("webhook sent") or sent == "webhook not configured":
            self.s["woken"][key] = now()
            return True
        return False

    def holds(self):
        try:
            return parse_holds(read(HOLDS_FILE))
        except Exception as e:   # never act without holds: gate() holds every PR until the file reads again
            self.errors += 1
            self.event("error", "holds", f"{type(e).__name__}: {str(e)[:200]}")
            self.wake("holds-unreadable", "holds", now()[:13], f"{HOLDS_FILE}: {str(e)[:150]}")
            return None

    def evaluate(self, p, holds):
        """(reasons, notes) for one PR: gate() then blockers(). The run and --explain both call this."""
        n = p["number"]
        p["labels"] = [x["name"] if isinstance(x, dict) else x for x in p.get("labels") or []]
        p.update(gh_json(["pr", "view", str(n), "-R", REPO, "--json", "mergeable,statusCheckRollup,files"]) or {})
        p["files"] = [f["path"] if isinstance(f, dict) else f for f in p.get("files") or []]
        notes = []
        reasons = gate(p, holds, notes)
        stop = blockers(p, self.paused)
        for _ in range(MERGEABLE_REREADS):
            if reasons or [r.code for r in stop] != ["mergeable"] or p.get("mergeable") != "UNKNOWN":
                break
            time.sleep(MERGEABLE_WAIT_S)
            p.update(gh_json(["pr", "view", str(n), "-R", REPO, "--json", "mergeable"]) or {})
            stop = blockers(p, self.paused)
        return reasons + stop, notes

    def merges(self, holds):
        for ident in list(self.s["queued"]):   # merges requested by an earlier run that have since left the queue
            try:
                self.landed(ident)
            except Exception as e:
                self.errors += 1
                self.event("error", ident, f"queued merge: {type(e).__name__}: {str(e)[:150]}")
        prs = gh_json(["pr", "list", "-R", REPO, "--state", "open", "--author", ME, "--limit", "100",
                       "--json", "number,headRefOid,isDraft,labels,body,title"]) or []
        for p in prs:
            ident = f"{SHORT}#{p['number']}"
            try:
                reasons, notes = self.evaluate(p, holds)
                act, who, why = next_action(reasons, notes, p, self.s["rerun"].get(ident))
                if ident in self.s["queued"]:
                    self.dequeue(ident, p["number"], p["headRefOid"], reasons, why)
                if self.dry:   # one line per PR, even when the decision has not changed since the last run
                    print(f"dry-run {ident} {p['headRefOid'][:9]} {act}{f' who={who}' if who != 'none' else ''}: {why}"[:400])
                self.apply(ident, p["number"], p["headRefOid"], act, who, why)
            except Exception as e:   # one PR's failure must not skip the rest
                self.errors += 1
                self.event("error", ident, f"merge: {type(e).__name__}: {str(e)[:150]}")

    def dequeue(self, ident, n, head, reasons, why):
        """Take the job's own merge-queue entry out when the gate no longer passes (a hold row came, PAUSED, no tier).
        dequeuePullRequest takes only the PR id, so an entry another actor makes between the read and the call would be
        dequeued too; GitHub has no conditional form."""
        q = gh_json(["api", "graphql", "-F", f"n={n}", "-f", f"query={QUEUE_ENTRY}"]) or {}
        pull = ((q.get("data") or {}).get("repository") or {}).get("pullRequest") or {}
        if not should_dequeue(reasons, self.s["queued"][ident], pull.get("mergeQueueEntry"), head):
            return
        if self.dry:
            return self.decide("would-dequeue", ident, head, why)
        rc, _, err = sh(["gh", "api", "graphql", "-f", f"id={pull['id']}", "-f", f"query={DEQUEUE}"])
        if rc:
            self.errors += 1
            return self.event("error", ident, f"dequeue at {head[:9]}: {err.strip()[:150]}")
        del self.s["queued"][ident]
        self.event("merge-dequeued", ident, f"at {head[:9]}: {why}")

    def apply(self, ident, n, head, act, who, why):
        if act == "wait":
            return self.decide("wait", ident, head, f"who={who}: {why}" if who != "none" else why)
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
            return self.wake("merge-failed", ident, head, f"gh pr merge: {err.strip()[:200]}")
        # trycua/cua has a merge queue: gh exits 0 when it only queued, so `merged` waits for GitHub's MERGED
        if self.s["queued"].get(ident, {}).get("head") != head:
            self.s["queued"][ident] = {"head": head, "why": why}
            self.once("merge-queued", ident, head, f"squash at {head[:9]}: {why}")
        self.landed(ident)

    def once(self, kind, ident, head, reason):
        """Log `kind` for this PR and head unless events.log has it: a run that died before saving state logs it once."""
        mark = f" {kind} {ident} squash at {head[:9]}"
        if not os.path.exists(EVENTS) or not any(mark in x for x in read(EVENTS).splitlines()):
            self.event(kind, ident, reason)

    def landed(self, ident):
        """Log `merged` once GitHub says a requested merge is MERGED, with its mergedAt; drop it when GitHub closed it."""
        q = self.s["queued"][ident]
        st = gh_json(["pr", "view", ident.split("#")[1], "-R", REPO, "--json", "state,mergedAt,headRefOid"]) or {}
        if st.get("state") == "MERGED":
            head = st.get("headRefOid") or q["head"]
            self.s["landed"][ident.split("#")[1]] = {"head": q["head"], "at": now()}   # the head it queued: another head is not the job's
            self.once("merged", ident, head, f"squash at {head[:9]}, mergedAt {st.get('mergedAt')}: {q['why']}")
        elif st.get("state") == "CLOSED":
            self.event("merge-dropped", ident, f"closed before the queued merge at {q['head'][:9]} landed")
        else:
            return
        del self.s["queued"][ident]

    def merge_watch(self):
        """Urgent unexpected-merge wake for a trycua/cua PR by ME that this job did not merge, whoever merged it. The heads
        in state.json landed and queued are the whole allow list. Covers merges since state merges.since: set on the first run, and moved
        up to WATCH_OVERLAP_S before this run only after a fetch that was not cut short, so no merge falls between two
        windows; merges.seen keeps the ones already handled inside the overlap. A merge whose wake was not sent (paused, or
        the POST failed) is not seen, and the window does not move past it, so the next pass sends it."""
        mw = self.s["merges"]
        known = mw.setdefault("seen", {})
        if not mw.get("since"):
            mw["since"] = now()
            return
        since = datetime.fromisoformat(mw["since"]).astimezone(ZoneInfo("UTC"))
        start = datetime.fromtimestamp(time.time() - WATCH_OVERLAP_S, SGT).isoformat(timespec="seconds")
        merged = gh_json(["pr", "list", "-R", REPO, "--state", "merged", "--author", ME, "--limit", str(WATCH_LIMIT), "--search",
                          f"merged:>={since.date().isoformat()}", "--json", "number,headRefOid,mergedBy,mergedAt,title,mergeCommit"]) or []
        at_of = lambda p: datetime.fromisoformat(p["mergedAt"].replace("Z", "+00:00"))   # noqa: E731
        by_n = {p["number"]: p for p in merged if at_of(p) >= since}
        heads = {int(k): v["head"] for k, v in self.s["landed"].items()}
        heads.update({int(k.split("#")[1]): v["head"] for k, v in self.s["queued"].items()})
        unsent = []
        for n, by in merge_audit(list(by_n.values()), heads, known):
            p, ident = by_n[n], f"{SHORT}#{n}"
            at = at_of(p).astimezone(SGT).isoformat(timespec="seconds")
            sha = (p.get("mergeCommit") or {}).get("oid") or "-"
            why = f"merged by {by} at {at} as {sha[:9]}, not by the tick: {(p.get('title') or '')[:100]}"
            if self.wake("unexpected-merge", ident, sha, why):
                known[str(n)] = {"by": by, "at": at, "outcome": "alert"}
            else:
                unsent.append(at)
        if len(merged) >= WATCH_LIMIT:   # merges past the page are unseen: keep the window and wake a person, once per window
            self.errors += 1
            why = f"{len(merged)} merged PRs since {mw['since']}, the fetch limit: check merges of PRs by {ME} by hand, then move merges.since"
            self.event("error", "merge-watch", why)
            return self.wake("merge-watch-overflow", "merge-watch", mw["since"], why)
        mw["since"] = max(mw["since"], min([start, *unsent]))
        mw["seen"] = {k: v for k, v in known.items() if v.get("at", "") >= mw["since"]}   # older ones are outside the window
        merged_at = {str(n): at_of(p).astimezone(SGT).isoformat(timespec="seconds") for n, p in by_n.items()}
        # a seeded entry's at is when the job queued it, before GitHub merged it: keep it while its merge is in the window
        self.s["landed"] = {k: v for k, v in self.s["landed"].items() if max(v["at"], merged_at.get(k, "")) >= mw["since"]}

    def run(self):
        if self.paused:
            self.decide("paused", "tick", "-", self.paused)
        elif self.s["last_reason"].pop("tick", None):
            self.event("resumed", "tick", "PAUSED removed")
        if not self.dry and (drift := parity_problem()):   # merging goes on; a person is woken, once an hour while it lasts
            self.errors += 1
            self.event("error", "parity", drift)
            self.wake("parity", "host", now()[:13], drift)
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
            f.write(f"{now()} zstack {sha()} {'ok' if not self.errors else f'{self.errors} error(s)'}{' paused' if self.paused else ''}\n")

    def explain(self, n):
        """--explain N: every reason for one PR and the action a run would take. Records nothing."""
        p = gh_json(["pr", "view", str(n), "-R", REPO, "--json", "number,headRefOid,isDraft,labels,body,title,state,author"])
        try:
            holds = parse_holds(read(HOLDS_FILE))
        except Exception as e:
            holds = None
            print(f"holds file unreadable: {e}")
        reasons, notes = self.evaluate(p, holds)
        act, who, why = next_action(reasons, notes, p, self.s["rerun"].get(f"{SHORT}#{n}"))
        gate_codes = {"ci", "holds-unreadable", "hold", "hands-off", "no-tier", "risk"}
        lines = [f"{SHORT}#{n} {p.get('state', '')} by {(p.get('author') or {}).get('login', '?')} at {p['headRefOid']}: "
                 f"{(p.get('title') or '')[:90]}", f"gate: {'pass' if not gate_codes & {r.code for r in reasons} else 'fail'}"]
        lines += [f"  {'gate' if r.code in gate_codes else 'blocker'} [{r.code}] {r.text}" for r in reasons]
        lines += [f"  note {x}" for x in notes]
        lines.append(f"next action: {act}{f' who={who}' if who != 'none' else ''}: {why}"[:400])
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


def run_on_main():
    """Cron runs whatever is checked out, so a bare run first moves the package to origin/main and restarts on it. A
    checkout that cannot move runs as it is, and hosts/check-parity reports it in run()."""
    if os.environ.pop("ZSTACK_ENSURED", None):
        return
    if subprocess.run([f"{PKG}/bin/ensure-package-main"], stdout=subprocess.DEVNULL).returncode == 0:
        os.execve(sys.executable, [sys.executable, os.path.abspath(__file__)], {**os.environ, "ZSTACK_ENSURED": "1"})


if __name__ == "__main__":
    if len(sys.argv) == 1:
        run_on_main()
    print(f"zstack {sha()}", file=sys.stderr)
    main()
