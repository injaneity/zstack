### Measure

**You own the measurement. Every change traces to a number, and every number survives scrutiny.** One change, one measurement, keep or revert. Never claim a win from reading code.

1. Ground the workload with **how**. Name what can move the result (data size, state, concurrency) and pick a case that reproduces the complaint. No repro, no optimizing.
2. Fix one metric, the better direction, and a stop predicate. A loop pairs the target with a minimum number of attempts, so an early lucky win cannot end it.
3. Build the harness and prove it can tell a slow case from a fast one, then freeze it. One command emits the metric sampled enough to clear noise, with its error count and work done. Record the baseline and a green regression run before any change.
4. Ground each hypothesis in a mechanism. Try the cheapest first: don't do it, do it once, do less, do it later, do it concurrently, do it cheaper.
5. Measure before and after under the same conditions. Keep a change only when the number moves past noise and the regression gate stays green. Otherwise revert it fully.
6. One commit per kept change, staging only its files.
7. In a loop, log every attempt with **show-me-your-work** and reread the log before the next. After several rejects, change category or try something more radical. Correctness and simplicity outrank the number.
8. Stop at the predicate, or when what's left is marginal. Never relax the predicate. Run **Pull requests** to open it, with the measurement in the description.

**Reply:** baseline, result, delta, and the artifact path. For a loop, attempts kept against reverted, the log path, and the next idea.
