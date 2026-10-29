#!/usr/bin/env python3
"""Run the merge gate for one repo.

  gate.py --config gate/zstack.json                 print one decision per open PR
  gate.py --config gate/zstack.json --apply         do it: undraft, rerun a red head once, merge
  gate.py --config gate/zstack.json --set-risk 1=light   record a Clef score for PR 1 at its head
  gate.py --config gate/zstack.json --held 1        PR 1 is on a hold list: never move it

Needs `gh` authenticated as the config's `me`. Decisions come from gate/engine.py and are the same decisions the
onigiri tick made for trycua/cua; this file only supplies what the tick used to keep in its own state files.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gate"))
import engine  # noqa: E402


def open_prs(cfg, limit):
    """Open PRs by `me`, with the fields the gate reads. mergeable and statusCheckRollup come per PR: asking
    gh for them on a list call times out on a repo with this many checks."""
    prs = engine.gh_json(["pr", "list", "-R", cfg.repo, "--state", "open", "--author", cfg.me,
                          "--limit", str(limit), "--json",
                          "number,headRefOid,isDraft,labels,body,title"]) or []
    for p in prs:
        p["labels"] = [l["name"] if isinstance(l, dict) else l for l in p.get("labels") or []]
        p["mergeable"], p["statusCheckRollup"] = "UNKNOWN", []
    return prs


def local_pass(roots):
    """Heads with a verify PASS in a run's verdict.md, first line "PASS <sha>"."""
    shas = set()
    for root in roots or []:
        base = os.path.join(root, ".git", "onigiri", "v2")
        for rid in os.listdir(base) if os.path.isdir(base) else []:
            try:
                with open(os.path.join(base, rid, "verdict.md")) as f:
                    m = engine.re.match(r"PASS ([0-9a-f]{40})$", f.readline().strip())
            except OSError:
                continue
            if m:
                shas.add(m.group(1))
    return shas


def git_in(root):
    """A git callable for engine.carry_pass, in root's clone. (rc, stdout)."""
    return lambda *args: engine.sh(["git", "-C", root, *args], timeout=300)[:2]


def carry(root, repo, n, head, passed, main_ref):
    """May head's verify PASS survive a main merge? Fetches the PR head and main into our own refs first, so it
    never races a run's origin/main. Returns True when the PASS carries, and the new head joins `passed`."""
    git = git_in(root)
    rc, _ = git("fetch", "-q", "--no-write-fetch-head", "origin",
                f"+refs/pull/{n}/head:refs/onigiri-tick/pr/{n}", f"+refs/heads/main:{main_ref}")
    if rc:
        return False
    parent, _why = engine.carry_pass(git, head, passed, main_ref)
    return bool(parent)


def load_state(path):
    """{"clef": {"<n>": {"head", "tier"}}, "rerun": {"<n>": "<sha>"}, "carried": {"<n>": {"head", "ok"}}}."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state, path):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--apply", action="store_true", help="act on the decisions instead of printing them")
    ap.add_argument("--state", help="state file for recorded Clef scores and rerun marks (default: gate/<repo>.state.json)")
    ap.add_argument("--held", default="", help="comma-separated PR numbers on a hold list")
    ap.add_argument("--set-risk", action="append", default=[], metavar="N=TIER",
                    help="record a Clef score (low|standard|high) for PR N at its current head")
    ap.add_argument("--runs", action="append", default=[], help="a repo checkout holding .git/onigiri/v2 run verdicts")
    ap.add_argument("--clone", action="append", default=[],
                    help="a clone of this repo, for carrying a verify PASS across a main merge (needs the PR ref)")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    cfg = engine.load(a.config)
    state_path = a.state or os.path.join(os.path.dirname(os.path.abspath(a.config)),
                                         cfg.repo.split("/")[-1] + ".state.json")
    state = load_state(state_path)
    held = {int(n) for n in a.held.split(",") if n.strip()}
    prs = open_prs(cfg, a.limit)
    by_n = {p["number"]: p for p in prs}
    for spec in a.set_risk:
        n, _, tier = spec.partition("=")
        if int(n) in by_n:
            state.setdefault("clef", {})[n] = {"head": by_n[int(n)]["headRefOid"], "tier": engine.norm_tier(tier)}
    local = local_pass(a.runs)
    for n, c in (state.get("carried") or {}).items():
        if c.get("ok"):
            local.add(c["head"])
    out = []
    for p in sorted(prs, key=lambda p: p["number"]):
        n, head = p["number"], p["headRefOid"]
        if not p["isDraft"] and engine.verify_pass(head, p.get("body"), local):
            p.update(engine.gh_json(["pr", "view", str(n), "-R", cfg.repo, "--json",
                                     "mergeable,statusCheckRollup"]) or {})
        if a.clone and not engine.verify_pass(head, p.get("body"), local):
            olds = engine.re.findall(r"^verify: PASS at ([0-9a-f]{40})", p.get("body") or "", engine.re.M)
            done = (state.get("carried") or {}).get(str(n)) or {}
            if olds and done.get("head") != head and carry(a.clone[0], cfg.repo, n, head, set(olds) | local,
                                                           "refs/onigiri-tick/main"):
                local.add(head)
                p["carried"] = True
                state.setdefault("carried", {})[str(n)] = {"head": head, "ok": True}
        c = (state.get("clef") or {}).get(str(n)) or {}
        action, why = engine.merge_decision(
            p, cfg=cfg, local_pass=local, clef=c.get("tier") if c.get("head") == head else None,
            held="hold list" if n in held else None, rerun_head=(state.get("rerun") or {}).get(str(n)))
        out.append({"pr": n, "head": head, "action": action, "reason": why})

    if a.apply:
        for d in out:
            if d["action"] == "ready":
                rc, _, err = engine.sh(["gh", "pr", "ready", str(d["pr"]), "-R", cfg.repo])
                d["done"] = "ready" if rc == 0 else f"ready failed: {err.strip()[:150]}"
            elif d["action"] == "rerun":
                rollup = (engine.gh_json(["pr", "view", str(d["pr"]), "-R", cfg.repo,
                                          "--json", "statusCheckRollup"]) or {}).get("statusCheckRollup")
                _, _, runs = engine.check_state(rollup, cfg)
                for rid in sorted(runs):
                    engine.sh(["gh", "run", "rerun", rid, "--failed", "-R", cfg.repo])
                state.setdefault("rerun", {})[str(d["pr"])] = d["head"]
                d["done"] = f"rerun {len(runs)} run(s)"
            elif d["action"] == "merge":
                rc, _, err = engine.sh(["gh", "pr", "merge", str(d["pr"]), "-R", cfg.repo,
                                        f"--{cfg.merge_method}", "--match-head-commit", d["head"]], timeout=180)
                d["done"] = "merged" if rc == 0 else f"merge failed: {err.strip()[:150]}"
    if a.apply or a.set_risk or a.clone:
        save_state(state, state_path)

    if a.json:
        print(json.dumps(out, indent=1))
    else:
        for d in out:
            print(f"{cfg.repo}#{d['pr']} {d['action']}: {d['reason']}" + (f" [{d['done']}]" if d.get("done") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
