### Feature

**You own the design. Plan, review, verify.** Delegate implementation. Stay in the lead.

1. `how` over the affected subsystem.
2. `architect` for competing design candidates.
3. Write the throughput checkpoint as four todo items. A dimension that genuinely does not apply (single file, no fan-out) keeps its item with `n/a: <reason>` rather than being dropped:
   - **Blocking first steps.** Gates run before fan-out.
   - **Independent workstreams.** Disjoint files, services, or layers parallelize. Shared writes serialize.
   - **Shared mutable state.** Default to splitting the target (the **separate-before-serializing-shared-state** principle skill). Serialize only for real invariants.
   - **Smallest safe decomposition.** If one worker is best, name why.
4. Write the code yourself at a scope you can state before you start: file paths, the named data shape and its organizing structure per **principle-model-the-domain** (a state machine over scattered booleans, a table or registry over branching, a typed model over repeated shape assumptions), and success criteria. When the implementation admits multiple valid shapes (error handling, abstraction layer, test structure), sketch two of them with `architect` and pick one before writing logic. Review separation comes from reading the diff against the plan and the principles afterwards, not from a second author. Comments per **Comments**. Surgical edits, re-ground against the source for upstream-derived files. Port shared-primitive improvements to all consumers and verify each. Commit liberally.
5. Verify on the matching surface. "Inconclusive" or wrong-surface is not a pass. Flag it.
6. Rebase into small, ordered commits. Stack follow-ups.
   Use the **sequence-verifiable-units** principle skill, building, verifying, and committing each small unit before the next.
7. If the design is contested, argue hardest against your own choice before shipping: name the case where it fails and check whether a rejected shape answers it.
8. Run **Opening a PR**.

Code-coupled work (one feature, one migration) stays in one thread with the checkpoint inline. Independent artifacts (audits, cross-subsystem reads, competing experiments) run one after another in that same thread. Rewrite the checkpoint at phase boundaries.

**Reply:** what you built, what you chose and why, the throughput checkpoint, open decisions. Tables for design alternatives.
