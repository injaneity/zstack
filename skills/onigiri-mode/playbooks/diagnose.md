### Diagnose

**You own the answer. Find it from evidence, not from reading source and guessing.** No fix unless asked. Once the cause is known, hand off to Change or Measure.

1. A how or why question goes through the **how** skill, plus **why** for motivation, regressions, or "was this deliberate".
2. A live process gets its signal captured before any theory: a CPU profile for a spin, a heap snapshot for a leak, a trace for a visual glitch. Drive the app until it happens.
3. A handed-over artifact gets loaded into a queryable shape first, such as sqlite with one row per sample or node. Then reduce it with a script that prints only the smoking gun: the hot function, the retainer chain to a GC root, the loop firing without input.
4. Prove the mechanism. Instrument or hotfix the running process and watch the hypothesis hold or fail. For an artifact, a before-and-after capture confirms it. Without one, call it the strongest hypothesis, not a confirmed cause.
5. Attribute to file, symbol, and line. A frame with no source mapping is not yet a diagnosis.
6. For a choice between alternatives, write a tradeoffs table and a recommendation. Push back on a wrong premise.

**Reply:** the evidence source, the reduced finding, how you proved the mechanism, the source location, and artifact paths.
