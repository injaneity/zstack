#!/usr/bin/env python3
"""compare.py OLD NEW DIR...: the release bench verdict from Bench v2 result rows.

Reads every results.jsonl under each DIR (run ids rb-<l|m>-[<f|b>g-]<version>-<task>), keeps final, non-excluded rows,
and pairs OLD and NEW by (platform, condition, task, run_index): the same task, seed and slot. Prints, per platform and
condition and overall: pass k/n with Wilson 95%, paired wins/losses, sign-test p, and paired medians of tokens
(input + output + cache_read), turns and wall_s, with a bootstrap 95% CI of the median paired ratio NEW/OLD.

Regression (the bench's one decision rule, written before any row was read):
- pass: NEW loses more paired slots than it wins and the two-sided sign test gives p < 0.05, on a platform-condition
  or overall; or
- cost: the bootstrap 95% CI of a paired median ratio (tokens, turns or wall) lies wholly above 1.10 on a platform.
Anything else is "no regression" (directional moves are reported, not acted on).
"""
import json
import math
import random
import re
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

RUN = re.compile(r"rb-([lm])-(?:([fb])g-)?(\d+(?:\.\d+)+)-([A-Z0-9-]+)$")


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def sign_p(w, l):
    n = w + l
    if not n:
        return 1.0
    k = min(w, l)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def tokens(r):
    t = r.get("tokens") or {}
    return (t.get("input") or 0) + (t.get("output") or 0) + (t.get("cache_read") or 0)


def boot_median_ratio(pairs, n=4000, seed=7):
    ratios = [b / a for a, b in pairs if a and b]
    if len(ratios) < 3:
        return None, None, None
    rng = random.Random(seed)
    meds = sorted(st.median(rng.choices(ratios, k=len(ratios))) for _ in range(n))
    return st.median(ratios), meds[int(0.025 * n)], meds[int(0.975 * n)]


def load(dirs):
    rows = []
    for d in dirs:
        for f in Path(d).rglob("results.jsonl"):
            m = RUN.match(f.parent.name)
            if not m:
                continue
            plat, cond, ver, task = m.groups()
            for line in f.read_text().splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                if not r.get("final", True):
                    continue
                r["_plat"] = "linux" if plat == "l" else "macos"
                r["_cond"] = {"f": "foreground", "b": "background", None: "foreground"}[cond]
                r["_ver"] = ver
                rows.append(r)
    return rows


def main(argv):
    old, new, dirs = argv[0], argv[1], argv[2:]
    rows = load(dirs)
    excluded = [r for r in rows if r.get("excluded")]
    rows = [r for r in rows if not r.get("excluded")]
    by = {}
    for r in rows:
        by[(r["_plat"], r["_cond"], r["task"], r["run_index"], r["_ver"])] = r
    groups = defaultdict(list)
    for (plat, cond, task, ri, ver), r in by.items():
        if ver != old:
            continue
        o, n = r, by.get((plat, cond, task, ri, new))
        if n is None:
            continue
        groups[(plat, cond)].append((task, ri, o, n))
    out = {"old": old, "new": new, "excluded": [r["trial_id"] + " " + r["_ver"] for r in excluded], "groups": {}, "tasks": {}}
    regress = []

    def summarize(name, pairs, cost_rule):
        if not pairs:
            return None
        ko = sum(bool(o["passed"]) for _, _, o, _ in pairs)
        kn = sum(bool(n["passed"]) for _, _, _, n in pairs)
        w = sum(bool(n["passed"]) and not o["passed"] for _, _, o, n in pairs)
        l = sum(bool(o["passed"]) and not n["passed"] for _, _, o, n in pairs)
        p = sign_p(w, l)
        g = {"n": len(pairs), "pass_old": ko, "pass_new": kn, "wilson_old": wilson(ko, len(pairs)),
             "wilson_new": wilson(kn, len(pairs)), "wins": w, "losses": l, "sign_p": round(p, 4)}
        for key, f in (("tokens", tokens), ("turns", lambda r: r.get("turns")), ("wall_s", lambda r: r.get("wall_s"))):
            pr = [(f(o), f(n)) for _, _, o, n in pairs if f(o) is not None and f(n) is not None]
            med, lo, hi = boot_median_ratio(pr)
            g[key] = {"median_old": st.median([a for a, _ in pr]) if pr else None,
                      "median_new": st.median([b for _, b in pr]) if pr else None,
                      "ratio": med, "ci": [lo, hi]}
            if cost_rule and lo is not None and lo > 1.10:
                regress.append(f"{name}: {key} paired median ratio CI [{lo:.2f}, {hi:.2f}] lies above 1.10")
        if l > w and p < 0.05:
            regress.append(f"{name}: pass {ko}->{kn}, {l} paired losses vs {w} wins, sign p={p:.3f}")
        out["groups"][name] = g
        return g

    allp = []
    for plat in ("linux", "macos"):
        pp = []
        for cond in ("foreground", "background"):
            pairs = groups.get((plat, cond), [])
            summarize(f"{plat}/{cond}", pairs, False)
            pp += pairs
        summarize(plat, pp, True)
        allp += pp
    summarize("overall", allp, False)
    for (plat, cond), pairs in groups.items():
        t = defaultdict(lambda: [0, 0, 0])
        for task, _, o, n in pairs:
            t[task][0] += 1
            t[task][1] += bool(o["passed"])
            t[task][2] += bool(n["passed"])
        out["tasks"][f"{plat}/{cond}"] = {k: {"n": v[0], "pass_old": v[1], "pass_new": v[2]} for k, v in sorted(t.items())}
    out["regression"] = regress
    out["verdict"] = "regression" if regress else "no regression"
    unpaired = [k for k in by if k[4] in (old, new) and (k[0], k[1], k[2], k[3], new if k[4] == old else old) not in by]
    out["unpaired"] = sorted(f"{a}/{b} {c} r{d + 1} {e}" for a, b, c, d, e in unpaired)
    print(json.dumps(out, indent=1, default=lambda x: round(x, 4) if isinstance(x, float) else str(x)))


if __name__ == "__main__":
    main(sys.argv[1:])
