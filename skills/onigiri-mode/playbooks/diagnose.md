### Diagnose

**You own the answer. Find it from evidence, not from reading source and guessing.**

Pick the evidence source by what you have. A question about how or why something works, with no failure yet, is answered from the code and the history. A live process misbehaving is captured while it runs. A profile, heap snapshot, spindump, or trace that someone handed you is read as a fixed artifact, not re-run.

1. Route a how or why question through the **how** skill. Add **why** for motivation, regression history, or "was this deliberate".
2. For a live process, capture the matching signal before theorizing. A CPU profile for a spin, a heap snapshot for a leak, a browser trace for a visual glitch. Drive the app until it happens. Never hand the user a repro you could run yourself.
3. For a handed-over artifact, identify the format and load it with the right tool. Dump it into sqlite, one row per sample, frame, or node, and reach a queryable shape before you read. Keep the tooling generic.
4. Reduce the artifact to the smoking gun. The function on the hot path. The retainer chain from the leaked object to a GC root. The loop firing without input. Parse with a script that prints only the reduction and keep the finding in the thread (the **principle-guard-the-context-window** skill).
5. Prove the mechanism before you believe it. Inject instrumentation into the running process, or hotfix it without reloading, and watch the hypothesis hold or fail. For a handed-over artifact, a paired before-and-after capture confirms it. Without one, say plainly that this is the strongest hypothesis the artifact supports, not a confirmed cause.
6. Attribute to source. File, symbol, line. A frame with no source mapping is not yet a diagnosis, so resolve the symbols or say the artifact does not carry them.
7. For a decision between alternatives rather than a defect, write the tradeoffs table and your recommendation with reasons. Push back if the premise is wrong.

The deliverable is a cited diagnosis or explanation. No fix unless asked. Once the cause is known, hand back to Bug fix or Measure.

**Reply:** the evidence source, the reduced finding, how you proved the mechanism, the source location, and artifact paths. For a how or why question, the how-shaped write-up (Overview, Key Concepts, How It Works, Where Things Live, Gotchas). Apply the **unslop** skill to the prose.
