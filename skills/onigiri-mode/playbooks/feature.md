### Feature

**You own the design and the code. Plan, write, review your own diff, verify.**

1. `how` over the affected subsystem.
2. `architect` for competing design candidates.
3. Name the order you will build it in before you start. Blocks of work that must finish before the next can begin go first, and anything sharing mutable state runs after what it shares with, or gets split so it doesn't share (**principle-separate-before-serializing-shared-state**). Then list the units in the order they run, each one small enough to verify on its own (the **principle-sequence-verifiable-units** skill).
4. Write the code yourself at a scope you can state before you start: file paths, the named data shape and its organizing structure per **principle-model-the-domain** (a state machine over scattered booleans, a table or registry over branching, a typed model over repeated shape assumptions), and success criteria. When the implementation admits multiple valid shapes (error handling, abstraction layer, test structure), sketch two of them with `architect` and pick one before writing logic. Review separation comes from reading the diff against the plan and the principles afterwards, not from a second author. Surgical edits, re-ground against the source for upstream-derived files. Port shared-primitive improvements to all consumers and verify each. Commit liberally.
5. Verify on the real surface. "Inconclusive" or wrong-surface is not a pass. Flag it.
6. Rebase into small, ordered commits. Stack follow-ups. Build, verify, and commit each unit before starting the next.
7. If the design is contested, argue hardest against your own choice before shipping: name the case where it fails and check whether a rejected shape answers it.
8. Run **Opening a PR**.

**Reply:** what you built, what you chose and why, the order you built it in, open decisions. Tables for design alternatives.
