#!/usr/bin/env python3
import argparse
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from collections import namedtuple
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wake  # noqa: E402  the urgent webhook's URL, key file and POST

ROOT = os.path.abspath(os.environ.get("ONIGIRI_ROOT") or "/srv/onigiri")
DIR = f"{ROOT}/tick"
EVENTS, HOLDS_FILE = f"{ROOT}/events.log", f"{ROOT}/hold-prs.txt"
STATE, PAUSED, LAST = f"{DIR}/state.json", f"{DIR}/PAUSED", f"{DIR}/last-run"
PKG = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_spec = importlib.util.spec_from_file_location("policy", os.path.join(PKG, "bin", "policy"),
                                               loader=importlib.machinery.SourceFileLoader("policy", os.path.join(PKG, "bin", "policy")))
policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(policy)
try:
    POLICY = policy.load()
except policy.PolicyError as e:
    sys.exit(f"merge_job: {e}")
REPO, ME = POLICY["merge"]["repo"], POLICY["people"]["me"]
OWNER, SHORT = REPO.split("/")
GATE_CHECK = POLICY["merge"]["gate_check"]
NOT_RERUN = set(POLICY["merge"]["never_rerun"])
TIERS = POLICY["merge"]["tiers"]
PREAPPROVE_TIERS = set(POLICY["merge"]["preapprove_tiers"])
WATCH_LIMIT = 500
WATCH_OVERLAP_S = 6 * 3600
MERGEABLE_REREADS, MERGEABLE_WAIT_S = 3, 5
SGT = ZoneInfo("Asia/Singapore")
ENV = {**os.environ, "GH_CONFIG_DIR": os.environ.get("GH_CONFIG_DIR") or os.path.expanduser("~/.config/gh"),
       "PATH": os.pathsep.join([os.path.expanduser("~/.local/bin"), "/usr/local/bin", os.environ.get("PATH") or "/usr/bin:/bin"])}

Reason = namedtuple("Reason", "code text")


def now():
    return datetime.now(SGT).isoformat(timespec="seconds")


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



RISK = re.compile(rf"risk: *({'|'.join(TIERS)})\b")


def declared_risk(labels, body):
    found = [m.group(1) for x in labels if (m := RISK.fullmatch(x.replace("risk:", "risk: ", 1)))]
    found += RISK.findall("\n".join(line for line in (body or "").splitlines() if line.startswith("risk:")))
    return max(found, key=TIERS.index) if found else None


def check_name(c):
    return c.get("name") or c.get("context") or ""


def is_status(c):
    return c.get("__typename") == "StatusContext" or ("state" in c and "status" not in c)


def latest_checks(rollup):
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
    elif tier in PREAPPROVE_TIERS and pre != head:
        R.append(Reason("risk", f"declared {tier}: needs Zane"
                        + (f" (preapproved at {pre[:9]}, head is {head[:9]})" if pre else " (no preapprove row)")))
    else:
        notes.append(f"risk {tier}" + (f", preapproved at {head[:9]}" if tier in PREAPPROVE_TIERS else ""))
    if (author := pr.get("author", ME)) != ME and pre != head:
        R.append(Reason("risk", f"PR by {author or '?'}: needs Zane"
                        + (f" (preapproved at {pre[:9]}, head is {head[:9]})" if pre else " (no preapprove row)")))
    return R


def blockers(pr, paused):
    R = [Reason("draft", "draft")] if pr.get("isDraft") else []
    if pr.get("mergeable") == "CONFLICTING":
        R.append(Reason("conflict", "merge conflict"))
    elif pr.get("mergeable") != "MERGEABLE":
        R.append(Reason("mergeable", f"mergeable {pr.get('mergeable')}"))
    if paused:
        R.append(Reason("paused", f"tick paused: {paused}"))
    return R


URGENT = ("unexpected-merge", "parity", "holds-unreadable", "merge-watch-overflow", "merge-failed")
ZANE_CODES = {"paused", "holds-unreadable", "hold", "hands-off", "risk"}
SESSION_CODES = {"no-tier", "conflict"}


def next_action(reasons, notes, pr, rerun_head=None):
    codes = {r.code for r in reasons}
    why = "; ".join(r.text for r in reasons) or ", ".join(notes)
    if not reasons:
        return "merge", "none", "green, " + ", ".join(notes)
    if "draft" in codes:
        return "wait", "none", why
    if codes & ZANE_CODES and not (codes & ZANE_CODES == {"risk"} and "ci" in codes and check_state(pr.get("statusCheckRollup"))[0] == "red"):
        return "wait", "zane", why
    if codes & SESSION_CODES:
        return "wait", "session", why
    if "ci" not in codes:
        return "wait", "none", why
    state, names, runs = check_state(pr.get("statusCheckRollup"))
    if state == "red":
        if rerun_head != pr["headRefOid"] and runs:
            return "rerun", "none", "red once: " + ", ".join(sorted(names))[:150]
        return "wait", "session", "red after rerun: " + ", ".join(sorted(names))[:150] + "".join(f"; {r.text}" for r in reasons if r.code == "risk")
    return "wait", "none", why


CHECKS = ("... on CheckRun { name status conclusion startedAt detailsUrl checkSuite { workflowRun { workflow { name } } } } "
          "... on StatusContext { context state createdAt }")
PAGE = "totalCount pageInfo { hasNextPage endCursor }"
PR_FIELDS = ("number state author { login } headRefOid isDraft title body mergeable labels(first: 100) { totalCount nodes { name } } "
             f"files(first: 100) {{ {PAGE} nodes {{ path }} }} "
             f"commits(last: 1) {{ nodes {{ commit {{ statusCheckRollup {{ contexts(first: 100) {{ {PAGE} "
             f"nodes {{ __typename {CHECKS} }} }} }} }} }} }}")
SEARCH_PAGE = 10
OPEN_PRS = ("query($q: String!, $after: String) { search(query: $q, type: ISSUE, first: %d, after: $after) { " % SEARCH_PAGE +
            f"pageInfo {{ hasNextPage endCursor }} nodes {{ ... on PullRequest {{ {PR_FIELDS} }} }} }} }}")
ONE_PR = f"query($n: Int!) {{ repository(owner: \"{OWNER}\", name: \"{SHORT}\") {{ pullRequest(number: $n) {{ {PR_FIELDS} }} }} }}"
MORE = (f"query($n: Int!, $files: String, $checks: String, $f: Boolean!, $c: Boolean!) {{ repository(owner: \"{OWNER}\", name: \"{SHORT}\") {{ "
        f"pullRequest(number: $n) {{ files(first: 100, after: $files) @include(if: $f) {{ {PAGE} nodes {{ path }} }} "
        f"commits(last: 1) @include(if: $c) {{ nodes {{ commit {{ statusCheckRollup {{ contexts(first: 100, after: $checks) {{ {PAGE} "
        f"nodes {{ __typename {CHECKS} }} }} }} }} }} }} }} }} }}")


def contexts_of(node):
    return (((node.get("commits") or {}).get("nodes") or [{}])[0].get("commit") or {}).get("statusCheckRollup") or {}


def read_rest(node):
    files = node["files"]
    checks = contexts_of(node).get("contexts") or {"totalCount": 0, "nodes": [], "pageInfo": {"hasNextPage": False}}
    while files["pageInfo"]["hasNextPage"] or checks["pageInfo"]["hasNextPage"]:
        f, c = files["pageInfo"]["hasNextPage"], checks["pageInfo"]["hasNextPage"]
        page = gh_json(["api", "graphql", "-F", f"n={node['number']}", "-F", f"f={str(f).lower()}", "-F", f"c={str(c).lower()}",
                        *(["-f", f"files={files['pageInfo']['endCursor']}"] if f else []),
                        *(["-f", f"checks={checks['pageInfo']['endCursor']}"] if c else []),
                        "-f", f"query={MORE}"])["data"]["repository"]["pullRequest"]
        if f:
            files["nodes"] += page["files"]["nodes"]
            files["pageInfo"] = page["files"]["pageInfo"]
        if c:
            more = contexts_of(page)["contexts"]
            checks["nodes"] += more["nodes"]
            checks["pageInfo"] = more["pageInfo"]
    return node


def read_pr(node):
    node = read_rest(node)
    contexts = contexts_of(node).get("contexts") or {"totalCount": 0, "nodes": []}
    if len(contexts["nodes"]) > contexts["totalCount"]:
        node = read_rest(gh_json(["api", "graphql", "-F", f"n={node['number']}", "-f", f"query={ONE_PR}"])["data"]["repository"]["pullRequest"])
    return from_graphql(node)


def from_graphql(node):
    files = node["files"]
    contexts = contexts_of(node).get("contexts") or {"totalCount": 0, "nodes": []}
    labels = node["labels"]
    if files["totalCount"] != len(files["nodes"]) or contexts["totalCount"] != len(contexts["nodes"]) or \
            labels["totalCount"] != len(labels["nodes"]):
        raise RuntimeError(f"cua#{node['number']}: read {len(files['nodes'])} of {files['totalCount']} files, "
                           f"{len(contexts['nodes'])} of {contexts['totalCount']} checks and "
                           f"{len(labels['nodes'])} of {labels['totalCount']} labels")
    checks = []
    for c in contexts["nodes"]:
        if c["__typename"] == "CheckRun":
            wf = ((c.get("checkSuite") or {}).get("workflowRun") or {}).get("workflow") or {}
            checks.append({"__typename": "CheckRun", "name": c["name"], "status": c["status"], "conclusion": c["conclusion"],
                           "startedAt": c["startedAt"], "detailsUrl": c["detailsUrl"], "workflowName": wf.get("name") or ""})
        else:
            checks.append({"__typename": "StatusContext", "context": c["context"], "state": c["state"], "createdAt": c["createdAt"]})
    return {"number": node["number"], "author": (node.get("author") or {}).get("login") or "",
            "headRefOid": node["headRefOid"], "isDraft": node["isDraft"], "title": node["title"],
            "body": node["body"], "mergeable": node["mergeable"], "labels": [x["name"] for x in labels["nodes"]],
            "files": [x["path"] for x in files["nodes"]], "statusCheckRollup": checks}


DEQUEUE_CODES = {"ci", "holds-unreadable", "hold", "hands-off", "no-tier", "risk", "paused", "unreadable"}
QUEUE_ENTRY = (f"query($n: Int!) {{ repository(owner: \"{OWNER}\", name: \"{SHORT}\") {{ pullRequest(number: $n) {{ id "
               "mergeQueueEntry { enqueuer { login } headCommit { oid } } } } }")
DEQUEUE = "mutation($id: ID!) { dequeuePullRequest(input: {id: $id}) { clientMutationId } }"
QUEUE_EVENTS = (f"query($n: Int!) {{ repository(owner: \"{OWNER}\", name: \"{SHORT}\") {{ pullRequest(number: $n) {{ "
                "timelineItems(itemTypes: [ADDED_TO_MERGE_QUEUE_EVENT, REMOVED_FROM_MERGE_QUEUE_EVENT], last: 20) { nodes { "
                "__typename ... on AddedToMergeQueueEvent { enqueuer { login } } ... on RemovedFromMergeQueueEvent { reason } } } } } }")


def queue_merged_by(events):
    by = None
    for e in events:
        if e.get("__typename") == "AddedToMergeQueueEvent":
            by = (e.get("enqueuer") or {}).get("login")
        elif e.get("__typename") == "RemovedFromMergeQueueEvent":
            if e.get("reason") == "merged":
                return by
            by = None
    return None


def should_dequeue(reasons, queued, entry, head, me=ME):
    if not entry or not queued or queued.get("head") != head:
        return False
    if (entry.get("enqueuer") or {}).get("login") != me or (entry.get("headCommit") or {}).get("oid") != head:
        return False
    return bool({r.code for r in reasons} & DEQUEUE_CODES)


def merge_audit(merged, job_heads, known):
    out = []
    for p in sorted(merged, key=lambda p: p.get("mergedAt") or ""):
        n, head = p["number"], p.get("headRefOid") or ""
        if str(n) in known or (job_heads.get(n) and head.startswith(job_heads[n])):
            continue
        out.append((n, (p.get("mergedBy") or {}).get("login") or "?"))
    return out


def seed_landed(events):
    out, queued = {}, set()
    for x in events:
        if m := re.match(r"(\S+) (merged|merge-queued) cua#(\d+) squash at ([0-9a-f]+)", x):
            at, kind, n, head = m.groups()
            if kind == "merge-queued" or n not in queued:
                out[n] = {"head": head, "at": at}
            queued |= {n} if kind == "merge-queued" else set()
    return out



def sh(args, timeout=120):
    r = subprocess.run(args, env=ENV, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def gh_json(args):
    errs = []
    for attempt in range(3):
        rc, out, err = sh(["gh", *args])
        if rc and err.strip() and err.strip() not in errs:
            errs.append(err.strip())
        if not rc or not re.search(r"HTTP 5\d\d|timeout|TLS|connection|unexpected end of JSON input", err, re.I):
            break
        time.sleep(5 * (attempt + 1))
    if rc:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {'; then '.join(e.removeprefix('gh: ') for e in errs)[:200]}")
    return json.loads(out) if out.strip() else None


def post_webhook(kind, ident, reason):
    m = re.search(r"#(\d+)$", ident or "")
    return wake.send({"kind": kind, "pr": int(m.group(1)) if m else None, "id": ident, "reason": reason, "time": now()},
                     attempts=1, timeout=8)


def sha():
    return subprocess.run(["git", "-C", PKG, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or "unknown"


def parity_problem():
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
        self.can_act = not dry and not self.paused
        s = json.loads(read(STATE)) if os.path.exists(STATE) else {}
        self.s = {k: s.get(k, {}) for k in ("woken", "rerun", "last_reason", "merges", "queued", "landed")}
        if "woken" not in s:
            self.s["woken"] = {k: v for k, v in s.get("buffered", {}).items() if k.split("|")[0] in URGENT}
        if "landed" not in s:
            self.s["landed"] = seed_landed(read(EVENTS).splitlines() if os.path.exists(EVENTS) else [])
        self.errors = 0

    def event(self, kind, ident, reason):
        line = f"{now()} {kind} {ident} {reason}".replace("\n", " ")
        print(line)
        if not self.dry:
            with open(EVENTS, "a") as f:
                f.write(line + "\n")

    def decide(self, kind, ident, head, reason):
        sig = f"{kind}|{head}|{reason}"
        if self.s["last_reason"].get(ident) != sig:
            self.s["last_reason"][ident] = sig
            self.event(kind, ident, reason)

    def wake(self, kind, ident, head, reason):
        assert kind in URGENT, kind
        key = f"{kind}|{ident}|{head or '-'}"
        if key in self.s["woken"]:
            return True
        m = re.fullmatch(rf"{SHORT}#(\d+)", ident or "")
        if m and kind != "unexpected-merge":
            try:
                st = (gh_json(["pr", "view", m.group(1), "-R", REPO, "--json", "state"]) or {}).get("state")
            except Exception:
                st = None
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
        except Exception as e:
            self.errors += 1
            self.event("error", "holds", f"{type(e).__name__}: {str(e)[:200]}")
            self.wake("holds-unreadable", "holds", now()[:13], f"{HOLDS_FILE}: {str(e)[:150]}")
            return None

    def evaluate(self, p, holds):
        n = p["number"]
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
        for ident in list(self.s["queued"]):
            try:
                self.landed(ident)
            except Exception as e:
                self.errors += 1
                self.event("error", ident, f"queued merge: {type(e).__name__}: {str(e)[:150]}")
        nodes, after = [], None
        while True:
            page = gh_json(["api", "graphql", "-f", f"q=repo:{REPO} is:pr is:open author:{ME}", "-f", f"query={OPEN_PRS}",
                            *(["-f", f"after={after}"] if after else [])])["data"]["search"]
            nodes += [x for x in page["nodes"] if x]
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
        mine = {x["number"] for x in nodes}
        queued = {int(k.split("#")[1]) for k in self.s["queued"]}
        for n in sorted((set((holds or {}).get("preapproved", {})) | queued) - mine):
            ident = f"{SHORT}#{n}"
            try:
                node = gh_json(["api", "graphql", "-F", f"n={n}", "-f", f"query={ONE_PR}"])["data"]["repository"]["pullRequest"]
            except Exception as e:
                self.errors += 1
                self.event("error", ident, f"outside PR: {type(e).__name__}: {str(e)[:150]}")
                if ident in self.s["queued"]:
                    try:
                        self.dequeue(ident, n, self.s["queued"][ident]["head"], [Reason("unreadable", str(e)[:150])], str(e)[:150])
                    except Exception as e2:
                        self.event("error", ident, f"dequeue: {type(e2).__name__}: {str(e2)[:150]}")
                continue
            if node and node.get("state") == "OPEN":
                nodes.append(node)
        for node in nodes:
            ident = f"{SHORT}#{node['number']}"
            try:
                try:
                    p = read_pr(node)
                except Exception as e:
                    if ident in self.s["queued"]:
                        self.dequeue(ident, node["number"], node["headRefOid"], [Reason("unreadable", str(e)[:150])], str(e)[:150])
                    raise
                reasons, notes = self.evaluate(p, holds)
                act, who, why = next_action(reasons, notes, p, self.s["rerun"].get(ident))
                if ident in self.s["queued"]:
                    self.dequeue(ident, p["number"], p["headRefOid"], reasons, why)
                if self.dry:
                    print(f"dry-run {ident} {p['headRefOid'][:9]} {act}{f' who={who}' if who != 'none' else ''}: {why}"[:400])
                self.apply(ident, p["number"], p["headRefOid"], act, who, why)
            except Exception as e:
                self.errors += 1
                self.event("error", ident, f"merge: {type(e).__name__}: {str(e)[:150]}")

    def dequeue(self, ident, n, head, reasons, why):
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
        if self.s["queued"].get(ident, {}).get("head") != head:
            self.s["queued"][ident] = {"head": head, "why": why}
            self.once("merge-queued", ident, head, f"squash at {head[:9]}: {why}")
        self.landed(ident)

    def once(self, kind, ident, head, reason):
        mark = f" {kind} {ident} squash at {head[:9]}"
        if not os.path.exists(EVENTS) or not any(mark in x for x in read(EVENTS).splitlines()):
            self.event(kind, ident, reason)

    def landed(self, ident):
        q, n = self.s["queued"][ident], ident.split("#")[1]
        st = gh_json(["pr", "view", n, "-R", REPO, "--json", "state,mergedAt,headRefOid"]) or {}
        by = None
        if st.get("state") == "MERGED":
            pull = (((gh_json(["api", "graphql", "-F", f"n={n}", "-f", f"query={QUEUE_EVENTS}"]) or {}).get("data") or {})
                    .get("repository") or {}).get("pullRequest") or {}
            by = queue_merged_by((pull.get("timelineItems") or {}).get("nodes") or [])
        if st.get("state") == "MERGED" and ((st.get("headRefOid") or q["head"]) != q["head"] or by != ME):
            self.event("merge-dropped", ident, f"merged at {(st.get('headRefOid') or q['head'])[:9]} through {by or 'no'} queue entry, "
                                               f"the job queued {q['head'][:9]}: not the job's merge")
        elif st.get("state") == "MERGED":
            head = q["head"]
            self.s["landed"][ident.split("#")[1]] = {"head": head, "at": now()}
            self.once("merged", ident, head, f"squash at {head[:9]}, mergedAt {st.get('mergedAt')}: {q['why']}")
        elif st.get("state") == "CLOSED":
            self.event("merge-dropped", ident, f"closed before the queued merge at {q['head'][:9]} landed")
        else:
            return
        del self.s["queued"][ident]

    def merge_watch(self):
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
        if len(merged) >= WATCH_LIMIT:
            self.errors += 1
            why = f"{len(merged)} merged PRs since {mw['since']}, the fetch limit: check merges of PRs by {ME} by hand, then move merges.since"
            self.event("error", "merge-watch", why)
            return self.wake("merge-watch-overflow", "merge-watch", mw["since"], why)
        mw["since"] = max(mw["since"], min([start, *unsent]))
        mw["seen"] = {k: v for k, v in known.items() if v.get("at", "") >= mw["since"]}
        merged_at = {str(n): at_of(p).astimezone(SGT).isoformat(timespec="seconds") for n, p in by_n.items()}
        self.s["landed"] = {k: v for k, v in self.s["landed"].items() if max(v["at"], merged_at.get(k, "")) >= mw["since"]}

    def run(self):
        if self.paused:
            self.decide("paused", "tick", "-", self.paused)
        elif self.s["last_reason"].pop("tick", None):
            self.event("resumed", "tick", "PAUSED removed")
        if not self.dry and (drift := parity_problem()):
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
        p = gh_json(["pr", "view", str(n), "-R", REPO, "--json", "state,author"])
        p.update(read_pr(gh_json(["api", "graphql", "-F", f"n={n}", "-f", f"query={ONE_PR}"])["data"]["repository"]["pullRequest"]))
        try:
            holds = parse_holds(read(HOLDS_FILE))
        except Exception as e:
            holds = None
            print(f"holds file unreadable: {e}")
        reasons, notes = self.evaluate(p, holds)
        act, who, why = next_action(reasons, notes, p, self.s["rerun"].get(f"{SHORT}#{n}"))
        gate_codes = {"ci", "holds-unreadable", "hold", "hands-off", "no-tier", "risk"}
        lines = [f"{SHORT}#{n} {p.get('state', '')} by {p.get('author') or '?'} at {p['headRefOid']}: "
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
    p.error = lambda msg: (p.print_help(sys.stderr), p.exit(2, f"merge_job.py: error: {msg}\n"))
    a = p.parse_args(sys.argv[1:] if argv is None else argv)
    if a.explain is not None:
        sys.exit(0 if Job(dry=True).explain(a.explain) else 1)
    elif a.webhook_test:
        print(post_webhook("test", None, "wiring test from the merge job, ignore"))
    else:
        Job(dry=a.dry_run).run()


def run_on_main():
    if os.environ.pop("ZSTACK_ENSURED", None):
        return
    if subprocess.run([f"{PKG}/bin/ensure-package-main"], stdout=subprocess.DEVNULL).returncode == 0:
        os.execve(sys.executable, [sys.executable, os.path.abspath(__file__)], {**os.environ, "ZSTACK_ENSURED": "1"})


if __name__ == "__main__":
    if len(sys.argv) == 1:
        run_on_main()
    print(f"zstack {sha()}", file=sys.stderr)
    main()
