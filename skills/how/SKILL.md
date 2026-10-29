---
name: how
description: "Use for \"how does X work\", code walkthroughs before changing something, and placement / ownership / layering questions (\"where should this live\", \"which package owns this\", \"is this the right layer\"). Explains subsystem architecture, runtime flow, onboarding mental models. Use why for motivation."
---

# How

Explain a subsystem at the level a senior engineer needs to start changing it: a working mental model, not annotated source.

## 1. Assess

- **Simple** (one module, one function, a narrow question): read it and answer in one pass. Go to step 3.
- **Complex** (several files or services, a cross-cutting flow, an overview): go to step 2.

When in doubt, take the simple path. If the scope is ambiguous, state your reading and proceed.

## 2. Explore

Split the question into 2 to 4 angles, each a distinct slice. Keep notes per angle with the file that taught you each fact.

When a slice is large and you won't edit its files next, give it to a `zstack-explorer` (onigiri-mode's Subagents rules). Spawn one per slice, all at once, each asking for file pointers, the call path, and gotchas. Read small slices, and any you are about to change, yourself.

Reconcile the notes. Where two angles disagree, go back to the code and settle it.

## 3. Explain

Use these sections, dropping any that don't apply:

- **Overview.** What it is, what it does, and why it exists, in a paragraph or two.
- **Key Concepts.** The types, services, or abstractions needed to follow the rest.
- **How It Works.** The longest section. What triggers it, what happens step by step, where data goes, and the decision points. Prose with file and function names, not code dumps. Add a mermaid or ASCII diagram only when components talk to each other in ways prose can't carry.
- **Where Things Live.** The files someone needs to start working here.
- **Gotchas.** Surprising behavior and pitfalls.

Be concrete: "`UserService` calls `AuthClient.refresh()`", not "the service delegates to the client". Explain why something complex is complex. Name the questions you couldn't settle.
