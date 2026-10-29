---
name: architect
description: "Sketch types, signatures, and module structure before code, then stay in the loop while implementation fills in. Use for /skill:architect, 'architect this', 'design this', or non-trivial work where jumping to code would lock in the wrong shape."
---

# Architect

Sketch types, signatures, and module boundaries with `not implemented` bodies. Compare competing sketches, pick one, then fill in code against it.

## A. Ground

Run **how** over every system the new code touches. Naming a file isn't grounding. If the design changes ownership or layering, run **why** on the current shape so its rationale becomes a constraint. Skip only for true greenfield work.

## B. Sketch

Sketch at least two structurally distinct candidates, even when the first looks fine: whole shapes, not tweaks inside one. Take each far enough to compare its types, its public surface, and where its state lives.

For a one-way-door decision, get two candidates from `zstack-designer` subagents (onigiri-mode's Subagents rules). Write the grounding to a file, then spawn both at once, each with that file and a different angle, such as "state lives in one owner" against "state lives with each caller". For a reversible decision, sketch them yourself.

Assume the next contributor is an agent that sees only the files it opened, copies the nearest example, and takes the shortest path that compiles. Reject or revise a shape with these red flags:

- **Shallow module.** A large interface hiding little. Callers coordinate several calls for one operation.
- **Leakage.** One internal decision, such as a schema or wire format, appears in several modules.
- **Temporal split.** Modules cut by execution order (load, validate, save) instead of by the knowledge they own.
- **Pass-through.** A method that forwards the same arguments and adds nothing.
- **Split ownership.** Two modules write the same state.
- **Two ways to do one thing.** Agents copy whichever they find first.
- **Importable internals.** If outside code can import them, it will.
- **Hand-synced lists.** Derive the copies, or make the build fail when they disagree.

Prefer the shape that hides the most behind the smallest interface. Pick one, graft anything better from the others, and write down what you rejected and the one fact that sank it.

## C. Agree (opt-in)

Proceed without a checkpoint unless asked to stop and show the design. Before implementing, argue hardest against your choice: name where it fails and check whether the rejected shape answers it. Pushback on the shape is new grounding, so re-sketch before writing more code.

## D. Implement

Fill in the sketch. It is the contract, so surface any deviation instead of absorbing it. A parameter the sketch didn't expect means the sketch was wrong, a requirement was missed, or the code is overreaching.

## E. Scrap

When the same friction keeps recurring, throw the sketch out: repeated workarounds, special cases in unrelated places, type escape hatches, an unexpected need for a lock, or callers who must know the internals. Re-run **how** on what exists, subtract first, and return to B as if the new constraints were there from day one.

## Output

Write the caller's usage first and derive the types from it. Ship a short rationale with the sketch: the problem and constraints, the usage, the shape, the decision and what lost, the tradeoffs accepted, open questions, and the first step.
