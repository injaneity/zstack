---
name: hillclimb
description: "Iteratively improve one measurable target (latency, throughput, accuracy, score, or pass rate) using a calibrated local evaluator, locked best commits, and an append-only decision log. Use for /hillclimb, /skill:hillclimb, leaderboard optimization, or sustained performance tuning. Not for a one-off bug fix or a single performance patch."
license: MIT
---

# Hillclimb

**Core rule.** One hypothesis, one change, one measurement, then keep or revert. Never stack untested edits, never claim a gain from reading code, and never optimize against an uncalibrated local proxy.

## 0. Match the scoring environment

- Match the target machine's CPU architecture, core count, OS, and memory limits from hour zero. Emulated containers and overloaded shared hosts distort timings and waste hours.
- Run a one-minute smoke test of the build, local evaluator, and submission or scoring path before touching code. A missing dependency, wrong toolchain flag, or quota limit looks like a code regression later.
- Pin the baseline commit on a named branch or tag (such as `best/baseline`), and pin every new verified best the same way so experiments cannot overwrite it.

## 1. Decompose the metric and fix the stop predicate

- Pick one primary scalar metric and its direction (higher or lower is better). Break it into its exact additive or multiplicative components (such as fixed floor against variable runtime, startup CPU against steady-state work, or recall against false positives). When the total stalls, the breakdown shows which component is frozen and where headroom remains.
- Check whether evaluation spans distinct regimes (such as practice against finals, cold against warm cache, or small against large inputs). Measure each regime separately, and detect or specialize per regime so tuning one does not quietly degrade the other.
- Agree on a checkable stop predicate before the first edit. Pair the target number with a minimum iteration floor or a hard budget cap (time, compute, or scarce external submissions). Never relax the predicate to declare success.

## 2. Build, prove, and calibrate the local evaluator

- Build one repeatable command that prints the primary metric, each sub-component, the error count, and the work count. Sample enough paired trials or fixed seeds to clear run-to-run noise (median or mean across multiple runs, never a single run).
- Prove sensitivity before editing target code. Run contrasting inputs (a weak baseline, a stronger variant, and an easier case) and confirm the evaluator separates them cleanly. Run all scripted checks on the unmodified baseline first. If a check fails on the baseline, fix the check rather than the code.
- Calibrate whenever an opaque, stochastic, or remote scorer decides the real score:
  - Build a diverse anchor set of real scored commits early. Include two or three stylistically extreme baselines instead of only tiny tweaks around one design, because extremes expose which mechanics the real scorer rewards or punishes.
  - Parameterize the local evaluator's hidden unknowns (such as opponent strength, cost weights, or hardware scaling factors) rather than hardcoding guesses. Fit those parameters to maximize rank correlation (Spearman rho) against real scores on held-out commits.
  - **Calibration gate.** Do not burn scarce external submissions or trust local hillclimbing until the baseline reproduces its real score within the noise floor and held-out rank correlation clears a strict bar (at least 0.75).
  - Guard against overfitting to a single local proxy by keeping a small ensemble of perturbed evaluator variants and a fresh held-out seed set. Never promote a change using the same seeds that tuned it.
- Freeze the evaluator, record the baseline numbers, and verify a green run of the regression gate before making the first code edit. Any later change to the evaluator starts a fresh baseline row in the log.

## 3. Open the decision log

Create a gitignored `decision.tsv` outside the tracked tree, with one row per attempt:

`id`, `hypothesis`, `mechanism`, `before`, `after`, `delta`, `gate`, `verdict` (`kept` or `reverted`), `commit`, `note`.

Read the log before proposing each new attempt so the run does not repeat dead ends.

## 4. Harvest cheap wins, then run the loop

Order hypotheses by expected gain per unit of risk. Collect low-risk system and compiler wins before rewriting algorithms (target-architecture SIMD flags, link-time optimization, profile-guided optimization, pre-forked or warm workers, and fail-fast short-circuits).

Every hypothesis must state a concrete mechanism tied to the score breakdown ("pre-fork after library import to remove per-case startup CPU"), not vague tinkering ("try caching").

For each iteration:

1. Apply one scoped change.
2. Run the frozen evaluator before and after on identical paired seeds, and run the regression gate.
3. Accept the change only when the metric improves past the noise floor across held-out seeds (and across the calibration ensemble when one exists) while the regression gate stays green. Correctness and simplicity outrank the number. Revert any speedup that breaks behavior, and keep any simplification that holds the number.
4. Make one clean commit per kept change, staging only the changed files (`git add <files>`, never `git add -A`). Update the locked best tag whenever a new external best is confirmed.
5. Revert rejected changes in full before starting the next attempt. Append the row to `decision.tsv` either way.

## 5. Diagnose failures and break plateaus

Inspect raw traces, profiles, and failure logs instead of trusting summary numbers or self-reported pass messages. Find the first divergence (the earliest step where timing, state, or output departs from the target behavior) rather than patching downstream symptoms.

| What happened | Next move |
| --- | --- |
| Local gains fail to transfer to real external scores | Stop editing target code. Add the new scored commits to the anchor set and recalibrate the local evaluator until held-out rank correlation passes the gate. |
| One score sub-component stays flat across iterations | Stop tuning other components. Inspect what holds that floor nonzero and target that bottleneck directly. |
| Noise hides small deltas | Increase paired trial count, pin seeds, and eliminate background CPU contention on the host. |
| Three rejects in a row or a flat curve | Stop incremental weight tuning. Re-read the problem constraints, combine near-misses, or branch a clean-slate rewrite in an isolated git worktree while leaving the locked best untouched. |
| Faster variant fails the regression gate | Revert in full. Identify the broken invariant before trying a safe version. |

## 6. Budget parallel work and scarce evaluations

- Separate exploration from promotion. When testing independent architectural ideas in parallel git worktrees, cap concurrent builds and benchmarks to physical CPU cores and available RAM so workers do not contend and corrupt timings.
- Serialize promotion checks against the current locked best on fresh held-out seeds. Never spend a scarce external evaluation slot on an unverified stack, an uncalibrated proxy gain, or a noisy single trial.

## 7. Stop, verify, and clean up

- Stop when the stop predicate is met, when the external budget runs out, or after repeated category pivots (for example, three clean-slate or category shifts in a row) fail to beat the noise floor. If blocked by an environment or access limit, report the blocker and stop instead of spinning.
- Run the full regression gate cold on the final head (and run the **verifier** when working under **onigiri-mode**).
- Clean up temporary worktrees, scratch datasets, build artifacts, and local VMs created during the run, while keeping the locked best commits and `decision.tsv`.

## Report

End with a concise report run through **unslop**:

- Metric, direction, stop predicate, and whether the predicate was met or why the run stopped.
- Baseline to final score with absolute and percent delta, plus the sub-component breakdown.
- Calibration summary (anchor count and held-out rank correlation) when an external scorer was used.
- Iterations run (`kept` vs `reverted`) and one line per kept commit with its measured delta.
- Path to `decision.tsv` and the strongest next hypothesis if pushed further.
