### Measure

**You own the measurement story. Every change traces to a number, and every number survives scrutiny.**

One-off fix or sustained loop, the discipline is the same. One change, one measurement, keep or revert. Never stack untested changes and never claim a win from reading code.

1. Ground the workload before choosing a number. Run **how** over the target, name the dimensions that can move the result (data size, history, state, concurrency), and pick a case that reproduces the complaint. If nothing reproduces it, fix the repro instead of optimizing.
2. Fix one metric, the direction that counts as better, and a stop predicate. A sustained loop pairs the target with a floor on attempts so a lucky early win cannot end the run ("at least 50% better and at least 10 iterations"). Use the user's numbers when given, otherwise agree them.
3. Build the harness, prove its sensitivity, then freeze it. Run contrasting realistic workloads and confirm the target case shows the symptom while easier cases separate as expected. If the harness cannot tell them apart, fix the harness. It should print its error count and a count of the work done. Once frozen, one command emits the metric sampled enough to clear noise (median of N, not a single run). Vet the harness and every later number: count the errors, confirm the work happened, and check the run-to-run spread before you trust a delta. Record the baseline and a green run of the regression gate before touching anything.
4. Ground each hypothesis in a mechanism, not "try memoizing something". Order them by the performance mantras, cheapest first. Don't do it. Don't do it twice. Do it less. Do it later. Do it when nobody is looking. Do it concurrently. Do it cheaper. When an earlier one meets the target, stop.
5. Make the change at a tight scope, read the diff against the metric, then measure before and after under the same conditions and run the gate. Accept only when the number moves past noise and the gate stays green. Otherwise revert in full. A tweak that might help is not kept.
6. One commit per accepted change, staging only the files you changed (`git add <files>`, never `-A`). Each unit verified before the next begins.
7. For a loop, open a decision log through the **show-me-your-work** skill. A `decision.tsv` row per attempt: id, hypothesis, change, before, after, delta, tests, kept or reverted, note. Read it before each attempt, and keep it out of the tree.
8. Push past the first plateau. On several rejects in a row, change category, combine near-misses, re-read the source, or try something more radical. Correctness and simplicity outrank the number. Revert a win that breaks behavior, and keep a simplification that holds it.
9. Stop when the predicate is met, or when the remaining ideas are marginal and not worth their cost. Never relax the predicate to meet it.
10. Run **Opening a PR**, citing the measurement in the description.

**Reply:** baseline number, post-fix number, delta, artifact path. For a loop add the iterations run kept versus reverted, each accepted fix on one line, the decision log path, and the best idea you would try next.
