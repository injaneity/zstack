#!/usr/bin/env python3
"""Run the merge gate for one repo.

  gate.py --config gate/zstack.json                 print one decision per open PR
  gate.py --config gate/zstack.json --apply         do it: undraft, rerun a red head once, merge
  gate.py --config gate/zstack.json --score          score every PR that has none at its head (runs Clef)
  gate.py --config gate/zstack.json --set-risk 1=light   record a Clef score for PR 1 at its head
  gate.py --config gate/zstack.json --held 1        PR 1 is on a hold list: never move it
  gate.py --config gate/cua.json --holds /srv/onigiri/hold-prs.txt   load holds and hands-off paths from a file
  gate.py --config gate/cua.json --pause-file /srv/onigiri/tick/PAUSED   refuse to act while that file exists

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
                          "number,headRefOid,isDraft,labels,body,title,author"]) or []
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


def score(cfg, prs, state, limit, log=print):
    """Run Clef on PRs with no score at their head, and record it.

    Failures count against `limit` too. Clef spends up to 320s per PR and can fail every time (no `pi`, no
    credentials), and uncounted failures would retry the whole repo on every run, silently.
    Drafts are skipped (Zane 27 Oct); the exception is a PR with a verify PASS at head, because the gate cannot
    undraft it without a score.
    """
    scored, tried = [], 0
    for p in sorted(prs, key=lambda p: p["number"]):
        n, head = p["number"], p["headRefOid"]
        if ((state.get("clef") or {}).get(str(n)) or {}).get("head") == head:
            continue
        if p["isDraft"] and not p.get("_has_pass"):
            continue
        if tried >= limit:
            break
        tried += 1
        # Files, title and head from ONE view call, and checked against the head the decision will use: a push in
        # between must not record a score for the old head against the new files.
        info = engine.gh_json(["pr", "view", str(n), "-R", cfg.repo,
                               "--json", "headRefOid,title,body,files"]) or {}
        if info.get("headRefOid") and info["headRefOid"] != head:
            log(f"{cfg.repo}#{n} moved to {info['headRefOid'][:9]} while scoring: not recorded")
            continue
        files = [f["path"] for f in info.get("files") or []]
        try:
            rc, out, err = engine.sh(["bash", cfg.clef_script], cwd=cfg.clef_cwd, timeout=320,
                                     input=json.dumps({"kind": "pr", "number": n, "title": info.get("title") or "",
                                                       "body": info.get("body") or "", "files": files}))
        except Exception as e:  # a timeout must not lose the scores this run already made
            log(f"{cfg.repo}#{n} clef failed: {type(e).__name__}: {e}")
            continue
        raw = out.strip().splitlines()[0] if rc == 0 and out.strip() else None
        tier = engine.clef_tier(raw, files, info.get("title") or "", outside=p.get("_outside", False), cfg=cfg)
        if not tier:
            log(f"{cfg.repo}#{n} clef gave no tier: {(err or '').strip()[:120]}")
            continue
        state.setdefault("clef", {})[str(n)] = {"head": head, "tier": tier, "raw": raw}
        scored.append(n)
    return scored


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
    ap.add_argument("--holds", help="a holds file (the tick's format): PR holds, no-move, hands-off paths")
    ap.add_argument("--pause-file", help="while this file exists, act on nothing (the tick's PAUSED convention)")
    ap.add_argument("--score", action="store_true", help="run Clef on PRs with no score at their head")
    ap.add_argument("--score-limit", type=int, default=4, help="how many PRs to score in one run (default 4)")
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
    log = []  # never stdout: --json owns it
    held = {int(n) for n in a.held.split(",") if n.strip()}
    try:
        holds = engine.load_holds(a.holds)
    except engine.HoldsUnreadable as e:
        # Never act on a partial read: a dropped hold is a merge. The tick failed closed the same way.
        sys.exit(f"holds unreadable, refusing to act: {e}")
    held |= set(holds["hold"] if holds else ()) | set(holds["no_move"] if holds else ())
    paused = None
    if a.pause_file and os.path.exists(a.pause_file):
        try:
            with open(a.pause_file) as f:
                paused = (f.read().strip().splitlines() or ["paused"])[0]
        except OSError as e:
            sys.exit(f"pause file unreadable, refusing to act: {e}")
    can_act = a.apply and not paused
    prs = open_prs(cfg, a.limit)
    by_n = {p["number"]: p for p in prs}
    for spec in a.set_risk:
        n, _, tier = spec.partition("=")
        if int(n) in by_n:
            p = by_n[int(n)]
            files = [f["path"] for f in (engine.gh_json(["pr", "view", n, "-R", cfg.repo, "--json", "files"])
                                        or {}).get("files") or []]
            # through clef_tier, not norm_tier: a raw `high` on an auth or CI path is `human` (needs its owner),
            # and recording it as `deep` would only buffer it.
            got = engine.clef_tier(tier, files, p.get("title") or "",
                                   outside=(p.get("author") or {}).get("login") != cfg.me, cfg=cfg)
            if got:
                prior = (state.get("clef") or {}).get(str(n)) or {}
                # a recorded score only ever escalates: never let a hand-set `light` overwrite a real `human`
                keep = prior.get("tier") if prior.get("head") == p["headRefOid"] else None
                state.setdefault("clef", {})[n] = {"head": p["headRefOid"],
                                                   "tier": engine.stricter(keep, got) or got}
    local = local_pass(a.runs)
    for n, c in (state.get("carried") or {}).items():
        if c.get("ok"):
            local.add(c["head"])
    dirty = False
    if a.clone:  # the PASS carry runs before anything is decided or scored, both of which need its answer
        for p in prs:
            n, head = p["number"], p["headRefOid"]
            if engine.verify_pass(head, p.get("body"), local):
                continue
            olds = engine.re.findall(r"^verify: PASS at ([0-9a-f]{40})", p.get("body") or "", engine.re.M)
            done = (state.get("carried") or {}).get(str(n)) or {}
            if not olds or done.get("head") == head:
                continue
            try:
                ok = carry(a.clone[0], cfg.repo, n, head, set(olds) | local, "refs/onigiri-tick/main")
            except Exception:  # a fetch or git failure must not lose the scores this run already recorded
                log.append(f"{cfg.repo}#{n} carry failed: fetch or git error")
                ok = False
            dirty = True
            state.setdefault("carried", {})[str(n)] = {"head": head, "ok": bool(ok)}
            if ok:
                local.add(head)
    for p in prs:  # what `score` needs and the gate proper does not
        p["_has_pass"] = engine.verify_pass(p["headRefOid"], p.get("body"), local)
        p["_outside"] = (p.get("author") or {}).get("login") != cfg.me
    if a.score:
        if not cfg.clef_script:
            sys.exit(f"{cfg.repo} has no clef_script in its config: nothing to score with")
        score(cfg, prs, state, a.score_limit, log=log.append)
    out = []
    for p in sorted(prs, key=lambda p: p["number"]):
        n, head = p["number"], p["headRefOid"]
        # Checks are read AFTER the carry, so a PASS carried on this run counts now rather than costing a run.
        if not p["isDraft"] and engine.verify_pass(head, p.get("body"), local):
            p.update(engine.gh_json(["pr", "view", str(n), "-R", cfg.repo, "--json",
                                     "mergeable,statusCheckRollup"]) or {})
        c = (state.get("clef") or {}).get(str(n)) or {}
        action, why = engine.merge_decision(
            p, cfg=cfg, local_pass=local, clef=c.get("tier") if c.get("head") == head else None,
            held="hold list" if n in held else None, rerun_head=(state.get("rerun") or {}).get(str(n)))
        if action in ("merge", "ready") and holds and holds["paths"]:
            # file-level holds, only for a PR about to move: the same place the tick checked them
            files = [f["path"] for f in (engine.gh_json(["pr", "view", str(n), "-R", cfg.repo, "--json", "files"])
                                        or {}).get("files") or []]
            hit = next((h for h in (engine.path_hit(f, holds) for f in files) if h), None)
            if hit:
                action, why = "buffer", f"touches {hit} (hands-off path; see the holds file)"
        out.append({"pr": n, "head": head, "action": action, "reason": why})

    if a.apply and paused:
        for d in out:
            d["done"] = f"not acting: paused ({paused})"
    elif a.apply:
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
    if can_act or a.set_risk or dirty or a.score:
        save_state(state, state_path)

    if a.json:
        print(json.dumps(out, indent=1))
    else:
        for d in out:
            print(f"{cfg.repo}#{d['pr']} {d['action']}: {d['reason']}" + (f" [{d['done']}]" if d.get("done") else ""))
    for line in log:
        print(line, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
