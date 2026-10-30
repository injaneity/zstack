### Change

**You own the change: a bug fix, a feature, or a refactor.** Every shipped line traces to evidence. A change that "might help" is a hypothesis, not a fix, and it does not ship. Large or cross-cutting work goes to **figure-it-out**.

**Start by kind.**
- **Bug.** Reproduce it yourself on the real surface, synthesizing the trigger or instrumenting until it fires. Ask the user only with a specific reason you can't reach it. Then binary-search the cause, seeding hypotheses with **how** and **why**. Each pass, take the split that cuts the most and get runtime evidence. Keep a list of dead hypotheses and reread it before each pass. Confirm the mechanism at runtime before planning the fix. Revert anything a refuted hypothesis motivated.
- **Feature.** Run **how** over the subsystem. Name the build order: work others depend on goes first, and anything sharing mutable state runs after what it shares with.
- **Refactor.** The structure changes and the behavior doesn't. Learn the contract with **how**, then pin it with a characterization test, snapshot, or equivalence harness. Type checks are not a pin. Split out any bug or missing feature you find. Delete dead code, one-caller wrappers, and redundant validators before adding the new shape, and the new shape must remove branches or invalid states, not add indirection.

**Then, for every kind:**
1. If the change crosses a function boundary, run **architect** for its shape.
2. Write it at a scope you can state in one sentence: the files, the named data shape, and what done looks like. Migrate every caller in the same change. Check renames against the files, since they miss strings and prose. Past a handful of files, write a codemod.
3. Build in small units, each verified before the next and, for a refactor, keeping the pin green.
4. Verify on the real surface. A bug's original repro now passes. A refactor's outputs match the old ones. Inconclusive or wrong-surface is not a pass.
5. If the design is contested, argue hardest against your choice: name where it fails and check whether a rejected shape answers it. Revert a refactor that doesn't lower reader load somewhere.
6. Commit in small ordered commits. A bug with a cheap local test lands its failing test first. A refactor lands the deletions first. Follow-ups get their own PRs.
7. Run **Pull requests** to open and merge it.

**Reply:** for a bug, what broke, the root cause, and the failing-then-passing repro verbatim. For a feature, what you built and chose, and open decisions. For a refactor, the structure that changed, the pin, and the equivalence proof.
