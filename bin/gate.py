#!/usr/bin/env python3
"""Run the merge gate for one repo.

  gate.py --config gate/zstack.json            print one decision per open PR
  gate.py --config gate/zstack.json --apply    do it: score, carry a verify PASS, undraft, rerun, merge

Two flags. Everything that differs between repos is in the config file, and everything that has to persist between
runs lives beside it. `--apply` is the whole pass, in the tick's order. There is no separate scoring mode and no way
to hand-type a risk tier, because the tick had neither and every bug in this file's first draft came from adding them.

Locking, retries and timeouts belong to whatever schedules this: cron-wrap.sh already runs each job under `flock -n`
and `timeout`, so a second run cannot start and a hung one is killed. Needs `gh` authenticated as the config's `me`.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "gate"))
import engine  # noqa: E402

CLEF_MAX_PER_RUN = 4  # ~10s each; the first pass over ~300 open PRs takes about a day (the tick's CLEF_MAX_PER_TICK)


def say(msg):
    print(msg, file=sys.stderr)


def open_prs(cfg):
    """Open PRs by `me`. mergeable and statusCheckRollup are read per PR where they can change the answer, because
    asking gh for them on a list call times out on a repo with this many checks."""
    prs = engine.gh_json(["pr", "list", "-R", cfg.repo, "--state", "open", "--author", cfg.me,
                          "--limit", "100", "--json", "number,headRefOid,isDraft,labels,body,title"]) or []
    for p in prs:
        p["labels"] = [l["name"] if isinstance(l, dict) else l for l in p.get("labels") or []]
        p["mergeable"], p["statusCheckRollup"] = "UNKNOWN", []
    return prs


def local_pass(root):
    """Heads with a verify PASS in a run's verdict.md, first line "PASS <sha>"."""
    shas = set()
    base = os.path.join(root, ".git", "onigiri", "v2") if root else ""
    for rid in os.listdir(base) if base and os.path.isdir(base) else []:
        try:
            with open(os.path.join(base, rid, "verdict.md")) as f:
                m = engine.re.match(r"PASS ([0-9a-f]{40})$", f.readline().strip())
        except OSError:
            continue
        if m:
            shas.add(m.group(1))
    return shas


def carry(cfg, n, head, passed, main_ref="refs/onigiri-gate/main"):
    """May head's verify PASS survive a main merge? Fetches the PR head and main into our own refs first, so it never
    races a run's origin/main. A failed fetch raises: the question is unanswered, and recording it as answered would
    strand the PR on "no verify PASS at head" until someone pushes."""
    git = lambda *args: engine.sh(["git", "-C", cfg.checkout, *args], timeout=300)[:2]  # noqa: E731
    rc, _, err = engine.sh(["git", "-C", cfg.checkout, "fetch", "-q", "--no-write-fetch-head", "origin",
                            f"+refs/pull/{n}/head:refs/onigiri-gate/pr/{n}", f"+refs/heads/main:{main_ref}"],
                           timeout=300)
    if rc:
        raise RuntimeError(f"fetch failed: {err.strip()[:120]}")
    return bool(engine.carry_pass(git, head, passed, main_ref)[0])


def score(cfg, prs, state, verdict):
    """Run Clef on PRs with no score at their head, and record it. Failures count toward the cap: Clef spends up to
    320s per PR and can fail every time, and uncounted failures would retry the whole repo every run, silently.

    Drafts are skipped unless `verdict` has a PASS at head, the tick's rule: it scores a draft on a PASS in the body or
    a verdict.md, never on a carried one, so a draft whose PASS only carries waits for its owner to mark it ready.
    """
    tried = 0
    for p in sorted(prs, key=lambda x: x["number"]):
        n, head = p["number"], p["headRefOid"]
        if ((state.get("clef") or {}).get(str(n)) or {}).get("head") == head:
            continue
        if p["isDraft"] and not engine.verify_pass(head, p.get("body"), verdict):
            continue
        if tried >= CLEF_MAX_PER_RUN:
            break
        tried += 1
        # Head, title, body and files from one call, checked against the head the decision will use: a push in
        # between must not record a score for the old head against the new files.
        info = engine.gh_json(["pr", "view", str(n), "-R", cfg.repo,
                               "--json", "headRefOid,title,body,files"]) or {}
        if info.get("headRefOid") != head:
            say(f"{cfg.repo}#{n} head moved while scoring: not recorded")
            continue
        files = [f["path"] for f in info.get("files") or []]
        try:
            rc, out, err = engine.sh([cfg.clef_script], cwd=cfg.checkout, timeout=320,
                                     input=json.dumps({"kind": "pr", "number": n,
                                                       "title": info.get("title") or "",
                                                       "body": info.get("body") or "", "files": files}))
        except Exception as e:
            say(f"{cfg.repo}#{n} clef failed: {type(e).__name__}: {e}")
            continue
        raw = out.strip().splitlines()[0] if rc == 0 and out.strip() else None
        tier = engine.clef_tier(raw, files, info.get("title") or "", cfg)
        if not tier:
            say(f"{cfg.repo}#{n} clef gave no tier: {(err or '').strip()[:120]}")
            continue
        state.setdefault("clef", {})[str(n)] = {"head": head, "tier": tier}


def load_state(path):
    """{"clef": {"<n>": {"head", "tier"}}, "rerun": {"<n>": "<sha>"}, "carried": {"<n>": {"head", "ok"}}}.

    Missing is a fresh start. Unreadable or corrupt is not: the tick raised there and ran nothing, and reading it as
    empty would rerun every red PR again and re-buy scores this file already holds.
    """
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


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
    prs = open_prs(cfg)
    state = load_state(state_path)
    try:
        run(cfg, prs, state, holds, paused, a.apply)
    finally:
        # a gh error halfway through still saves: the Clef scores and reruns already spent are not paid for twice.
        # Print mode writes nothing at all, so it cannot leave a cache behind that a later --apply then trusts.
        if a.apply:
            save_state(state, state_path)
    return 0


def run(cfg, prs, state, holds, paused, apply):
    """One pass, in the order the merge depends on: PASS evidence, then score, then decide, then move."""
    held = set(holds["hold"]) | set(holds["no_move"]) if holds else set()
    verdict = local_pass(cfg.checkout)  # a PASS that a run wrote, not one carried across a merge
    passed = set(verdict) | {c["head"] for c in (state.get("carried") or {}).values() if c.get("ok")}

    carry_all(cfg, prs, passed, held, state)

    if apply and cfg.clef_script and not paused:
        score(cfg, prs, state, verdict)
    elif paused:
        say(f"not scoring or acting: paused ({paused})")

    if apply and not paused:
        for p in sorted(prs, key=lambda x: x["number"]):
            act(cfg, p, state, holds, held, passed)
    else:
        note = f" [not acting: paused ({paused})]" if paused else ""
        for p in sorted(prs, key=lambda x: x["number"]):
            action, why = decide(cfg, p, state, holds, held, passed)
            print(f"{cfg.repo}#{p['number']} {action}: {why}{note}")


def carry_all(cfg, prs, passed, held, state):
    """Record whether a verify PASS survives a main merge, once per head. A fetch failure is not recorded, so the
    question is asked again next run rather than stranding the PR until its next push."""
    if not cfg.checkout:
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
            ok = carry(cfg, n, head, set(olds) | passed)
        except Exception as e:
            say(f"{cfg.repo}#{n} carry unanswered ({type(e).__name__}: {e}): will retry next run")
            continue
        state.setdefault("carried", {})[str(n)] = {"head": head, "ok": ok}
        if ok:
            passed.add(head)


def decide(cfg, p, state, holds, held, passed):
    """One PR's (action, why)."""
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


def act(cfg, p, state, holds, held, passed):
    """Do one PR's ready/rerun/merge work, printing it as it goes."""
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
