#!/usr/bin/env python3
"""Run the merge gate for one repo.

  gate.py --config gate/zstack.json            print one decision per open PR
  gate.py --config gate/zstack.json --apply    do it: undraft, rerun a red head once, score, merge

Two flags. Everything that differs between repos is in the config file, and everything that has to persist between
runs lives beside it. `--apply` is the whole pass, in the tick's order: score what has no score, carry a verify PASS
across a main merge, decide, then move. There is no separate scoring mode and no way to hand-type a risk tier,
because the tick had neither and every bug in this file's first draft came from adding them.

Locking, retries and timeouts belong to whatever schedules this: cron-wrap.sh already runs each job under `flock -n`
and `timeout`, so a second run cannot start and a hung one is killed. Needs `gh` authenticated as the config's `me`.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gate"))
import engine  # noqa: E402

CLEF_MAX_PER_RUN = 4  # ~10s each; the first pass over ~300 open PRs takes about a day


def open_prs(cfg):
    """Open PRs by `me`. mergeable and statusCheckRollup come per PR, because asking gh for them on a list call
    times out on a repo with this many checks."""
    prs = engine.gh_json(["pr", "list", "-R", cfg.repo, "--state", "open", "--author", cfg.me,
                          "--limit", "100", "--json",
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


def carry(root, n, head, passed, main_ref):
    """May head's verify PASS survive a main merge? Fetches the PR head and main into our own refs first, so it
    never races a run's origin/main."""
    git = git_in(root)
    rc, _, err = engine.sh(["git", "-C", root, "fetch", "-q", "--no-write-fetch-head", "origin",
                            f"+refs/pull/{n}/head:refs/onigiri-gate/pr/{n}", f"+refs/heads/main:{main_ref}"],
                           timeout=300)
    if rc:
        raise RuntimeError(f"fetch failed: {err.strip()[:120]}")
    parent, _why = engine.carry_pass(git, head, passed, main_ref)
    return bool(parent)


def score(cfg, prs, state, passed, log):
    """Run Clef on PRs with no score at their head, and record it. Failures count toward the cap: Clef spends up to
    320s per PR and can fail every time, and uncounted failures would retry the whole repo every run, silently.

    Drafts are skipped, except one with a verify PASS at its head: the gate cannot undraft it without a score.
    """
    tried = 0
    for p in sorted(prs, key=lambda x: x["number"]):
        n, head = p["number"], p["headRefOid"]
        if ((state.get("clef") or {}).get(str(n)) or {}).get("head") == head:
            continue
        if p["isDraft"] and not engine.verify_pass(head, p.get("body"), passed):
            continue
        if tried >= CLEF_MAX_PER_RUN:
            break
        tried += 1
        # Head, title, body and files from one call, checked against the head the decision will use: a push in
        # between must not record a score for the old head against the new files.
        info = engine.gh_json(["pr", "view", str(n), "-R", cfg.repo,
                               "--json", "headRefOid,title,body,files"]) or {}
        if info.get("headRefOid") != head:
            log.append(f"{cfg.repo}#{n} head moved while scoring: not recorded")
            continue
        files = [f["path"] for f in info.get("files") or []]
        try:
            rc, out, err = engine.sh([cfg.clef_script], cwd=cfg.clef_cwd, timeout=320,
                                     input=json.dumps({"kind": "pr", "number": n,
                                                       "title": info.get("title") or "",
                                                       "body": info.get("body") or "", "files": files}))
        except Exception as e:
            log.append(f"{cfg.repo}#{n} clef failed: {type(e).__name__}: {e}")
            continue
        raw = out.strip().splitlines()[0] if rc == 0 and out.strip() else None
        outside = (p.get("author") or {}).get("login") != cfg.me
        tier = engine.clef_tier(raw, files, info.get("title") or "", outside=outside, cfg=cfg)
        if not tier:
            log.append(f"{cfg.repo}#{n} clef gave no tier: {(err or '').strip()[:120]}")
            continue
        state.setdefault("clef", {})[str(n)] = {"head": head, "tier": tier, "raw": raw}


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


def pause_reason(path):
    """The first line of the pause file, or None when absent. Unreadable is fatal: a pause we cannot read may be a
    disk-health pause, and guessing "not paused" is how a full disk becomes a merged repo."""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return (f.read().strip().splitlines() or ["paused"])[0]
    except OSError as e:
        sys.exit(f"pause file unreadable, refusing to act: {e}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--apply", action="store_true", help="act on the decisions instead of printing them")
    a = ap.parse_args(argv)

    cfg = engine.load(a.config)
    who = engine.sh(["gh", "api", "user", "-q", ".login"])[1].strip()  # `me` filters PR authors; act as it
    if who != cfg.me:
        sys.exit(f"gh is authenticated as {who or 'nobody'}, but {a.config} expects {cfg.me}")
    here = os.path.dirname(os.path.abspath(a.config))
    state_path = os.path.join(here, cfg.repo.split("/")[-1] + ".state.json")

    if a.apply and not cfg.holds:
        # Absent and empty are different answers. The tick opened its holds file directly and refused to merge when
        # it could not read it; a gate that merges with no holds file is strictly less safe than that.
        sys.exit(f"--apply needs a 'holds' path in {a.config}")
    try:
        holds = engine.load_holds(cfg.holds)
    except engine.HoldsUnreadable as e:
        sys.exit(f"holds unreadable, refusing to act: {e}")

    paused = pause_reason(cfg.pause_file)
    log = []
    prs = open_prs(cfg)

    state = load_state(state_path)
    try:
        run(cfg, prs, state, holds, paused, log, a.apply)
    finally:
        # a gh error halfway through still saves: the Clef scores and reruns already spent are not paid for twice
        save_state(state, state_path)

    for line in log:
        print(line, file=sys.stderr)
    return 0


def run(cfg, prs, state, holds, paused, log, apply):
    """One pass, in the order the merge depends on: PASS evidence first, then score, then decide, then move."""
    held = set(holds["hold"]) | set(holds["no_move"]) if holds else set()
    passed = local_pass(cfg.checkouts)
    for c in (state.get("carried") or {}).values():
        if c.get("ok"):
            passed.add(c["head"])

    carry_all(cfg, prs, passed, held, state, log)

    if apply and cfg.clef_script and not paused:
        score(cfg, prs, state, passed, log)
    elif paused:
        log.append(f"not scoring or acting: paused ({paused})")

    if apply:
        apply_pass(cfg, prs, state, holds, held, passed, paused, log)  # prints as it acts
    else:
        for p in sorted(prs, key=lambda x: x["number"]):
            action, why = decide(cfg, p, state, holds, held, passed)
            print(f"{cfg.repo}#{p['number']} {action}: {why}")


def carry_all(cfg, prs, passed, held, state, log):
    """Record whether a verify PASS survives a main merge, once per head. A fetch failure is not recorded, so the
    question is asked again next run rather than stranding the PR until its next push."""
    if not cfg.checkouts:
        return
    for p in prs:
        n, head = p["number"], p["headRefOid"]
        if n in held or engine.verify_pass(head, p.get("body"), passed):
            continue
        olds = engine.re.findall(r"^verify: PASS at ([0-9a-f]{40})", p.get("body") or "", engine.re.M)
        done = (state.get("carried") or {}).get(str(n)) or {}
        if not olds or done.get("head") == head:
            continue
        try:
            ok = carry(cfg.checkouts[0], n, head, set(olds) | passed, "refs/onigiri-gate/main")
        except Exception as e:
            log.append(f"{cfg.repo}#{n} carry unanswered ({type(e).__name__}: {e}): will retry next run")
            continue
        state.setdefault("carried", {})[str(n)] = {"head": head, "ok": ok}
        if ok:
            passed.add(head)


def decide(cfg, p, state, holds, held, passed):
    """One PR's (action, why, files-when-needed)."""
    n, head = p["number"], p["headRefOid"]
    if not p["isDraft"] and engine.verify_pass(head, p.get("body"), passed):
        # checks are read only where they can change the answer, and after the carry, so a PASS carried this run
        # counts now instead of costing another run
        p.update(engine.gh_json(["pr", "view", str(n), "-R", cfg.repo, "--json",
                                 "mergeable,statusCheckRollup"]) or {})
    c = (state.get("clef") or {}).get(str(n)) or {}
    action, why = engine.merge_decision(
        p, cfg=cfg, local_pass=passed, clef=c.get("tier") if c.get("head") == head else None,
        held="hold list" if n in held else None, rerun_head=(state.get("rerun") or {}).get(str(n)))
    if action in ("merge", "ready") and holds and holds["paths"]:
        files = [f["path"] for f in (engine.gh_json(["pr", "view", str(n), "-R", cfg.repo, "--json", "files"])
                                    or {}).get("files") or []]
        hit = next((h for h in (engine.path_hit(f, holds) for f in files) if h), None)
        if hit:
            action, why = "buffer", f"touches {hit} (hands-off path; see the holds file)"
    return action, why


def apply_pass(cfg, prs, state, holds, held, passed, paused, log):
    """Do the ready/rerun/merge work, printing each PR as it goes."""
    if paused:
        for p in sorted(prs, key=lambda x: x["number"]):
            action, why = decide(cfg, p, state, holds, held, passed)
            print(f"{cfg.repo}#{p['number']} {action}: {why} [not acting: paused ({paused})]")
        return
    for p in sorted(prs, key=lambda x: x["number"]):
        n, head = p["number"], p["headRefOid"]
        action, why = decide(cfg, p, state, holds, held, passed)
        done = ""
        if action == "ready":
            rc, _, err = engine.sh(["gh", "pr", "ready", str(n), "-R", cfg.repo])
            done = "ready" if rc == 0 else f"ready failed: {err.strip()[:150]}"
        elif action == "rerun":
            rollup = (engine.gh_json(["pr", "view", str(n), "-R", cfg.repo,
                                      "--json", "statusCheckRollup"]) or {}).get("statusCheckRollup")
            _, _, runs = engine.check_state(rollup, cfg)
            for rid in sorted(runs):
                engine.sh(["gh", "run", "rerun", rid, "--failed", "-R", cfg.repo])
            state.setdefault("rerun", {})[str(n)] = head
            done = f"rerun {len(runs)} run(s)"
        elif action == "merge":
            rc, _, err = engine.sh(["gh", "pr", "merge", str(n), "-R", cfg.repo,
                                    f"--{cfg.merge_method}", "--match-head-commit", head], timeout=180)
            done = "merged" if rc == 0 else f"merge failed: {err.strip()[:150]}"
        print(f"{cfg.repo}#{n} {action}: {why}" + (f" [{done}]" if done else ""))


if __name__ == "__main__":
    sys.exit(main())
