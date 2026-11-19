---
name: onigiri-mode
description: "Read in full before any coding task: how a coding thread works under zstack, whatever harness runs it. One thread owns one task and writes all its code; delegates explore, design and verify. Use for /onigiri-mode, /skill:onigiri-mode, or any real change rather than a quick answer."
license: MIT
---

# onigiri mode

**Hard rule.** One thread owns one task and writes all of its code. Nothing restarts it, and it never hands part of the task to another coding thread. The only helpers are the three delegates under Subagents below. They never edit source and cannot start a helper of their own.

## Who is reading

- **A coding thread** does the work. Every section below applies. Its model is `models.coding` in the config.
- **The dispatcher** is one scheduled thread per machine. It never edits repo files, runs builds or tests, or pushes. It launches the coding threads `queue.py` names, frees the slot of a thread that died, and relays what comes back. Its instructions are the `dispatch` skill in the package, not this skill.

## Invariants

The invariants are `config/invariants.toml` in the zstack package, plus the target repo's own `invariants.toml` in the config, and nowhere else. The plugin or launcher that started you put `queue.py invariants` in your context, so you already have them. A hook or the guard refuses whatever would break one. The values the code enforces are the config; this skill and its playbook name those keys and never restate their values.

## Work

Your model needs no instruction in how to read code, design or debug. These lines are only the choices this stack makes:

- **A question** gets a cited answer, not a fix. A cause you did not prove on the running thing is the strongest hypothesis, and you call it that.
- **A bug** is reproduced on the real surface before any fix. When a cheap local test exists, its failing test is the first commit. Reply with what broke, the root cause, and the failing-then-passing repro verbatim.
- **A refactor** pins the behavior first with a characterization test, snapshot or equivalence harness (type checks are not a pin), lands its deletions first, and is reverted when it does not lower reader load. Reply with the structure that changed, the pin and the equivalence proof.
- **A measurement** uses a harness proven to tell a slow case from a fast one, then frozen. Keep a change only when the number moves past noise, one commit per kept change, and never relax the stop predicate. Reply with baseline, result, delta and the artifact path.
- **Every change** is small ordered commits, moves every caller and deletes the old API with no shims, and ends in a PR (`playbooks/pull-requests.md`). Follow-ups get their own PRs.
- **Pausing** is one `wip:` commit and a resume note in a file: intent, what is verified, what is not, the next action. Resuming rebuilds state from the repo and checks inherited claims on the real artifact.
- **Every claim** is measured, inferred or a guess, and you say which. Prose goes through **unslop**.

## Subagents

The three delegates are the role files in the package's `agents/`. Each runs on the provider and model the machine file's `[models]` gives its name, never on a model you pick. Start one with the delegate tool your harness has: T3 Code's `delegate_task`, or pi's `subagent` tool (`pi/README.md`). The `verifier` never runs on your own model: without its seat it returns an error and nothing runs.

| Role | Agent | Step | Only when |
|---|---|---|---|
| Explore | `explorer`, 2 to 4 at once | A question, research, any step | Required for a complex question: one that spans many files or services, needs a broad repo, code, log or PR search, or follows a cross-cutting flow. A simple one (one module, one function, a narrow question) stays inline with no explorers. When in doubt, take the simple path. |
| Design | `designer`, twice (`variant` 1 and 2) | A change, before building | Deep risk or new work only. Small fixes skip it. Give both the same brief, pick one plan, and graft in the best of the other. |
| Verify | `verifier` | Before you call the work done or open a PR, and after a push that changes more than wording | Every diff and every done claim that rests on one, and a review of someone else's PR, as the invariant `turn-ends-verified` says. |

- **Fan out in one message.** For a complex question, split it into 2 to 4 angles, each a distinct slice, and spawn every explorer in a single message, one delegate call per angle. Then synthesize yourself. Explorers return file pointers and short summaries, never pasted file contents, and you keep only those summaries.
- **Hand off to the verifier.** Its brief carries the diff range, the worktree, the scope, the host's platform limits, and your short test plan: what to run, which lanes, and the repro a fix must now pass. It returns pass or fail with only the failing lines. For a queue item, the brief also carries the `queue.py verified` line your own brief gives you, with the PR number and the head filled in, and the verifier runs it. Wait for the verifier inside your turn: when your turn ends, the queue frees your slot and reports what happened, so a turn that ends while the verifier still runs is reported as unfinished. A fix round's brief carries only its findings and the new range, never your logs or reasoning.
- **Nowhere else.** Do everything else yourself.
- **The brief stands alone.** A child sees only the brief, never this conversation. Give it the diff range, the scope, what is out of scope, and the shape of the answer. Pass long inputs as file paths.
- **A report is evidence, not a verdict.** Check it before a decision rests on it.
- **No nesting.** A delegate starts no delegate of its own, and a brief never asks one to start an agent from its shell. Without the tool or the role, do that step yourself and say so.
- **The gate is the verifier's.** For a queue item, the verifier's own `queue.py verified` call is what lets the PR merge. You never run it, and never ask the verifier for a result. Elsewhere its pass is evidence for your run report.

## Autonomy

Reversible work proceeds without asking. Never block on a question you could answer by looking. If asked to do something you think is wrong, say why, then do the version that holds up.

End with a final report: the PR URLs, then `follow-ups:` and `questions:` lists. A question for the owner goes under `questions:`, and the orchestrator relays it. A thread that cannot go on says so in its report and stops.

Pause for the irreversible: force-pushes to shared branches, deploys, data deletion, and messages to people. A playbook the user asked for covers its own posts on that PR.

"Don't stop" or "going to bed" means run to the exit condition, with an append-only log of one row per decision (what, why, and a pointer to the evidence). A plateau is not a stop.

## Playbooks

- **Pull requests** (`pull-requests`). `playbooks/pull-requests.md`: open a PR at the end of every change, and on request land one or verify someone else's. Read it in full, and list its numbered steps in your first message. A step you skip stays in the list as `skip: <reason>`.

Editing a skill is a change like any other. Match the neighbouring SKILL.md files (frontmatter with `name` and `description`, one job) and check every link resolves.

## Repo rules

The target repo's `AGENTS.md`, `CLAUDE.md`, or `CONTRIBUTING.md` wins over this section. Read it before the first change. Where it says nothing:

- No admin bypass, and no force-push: the invariant `own-controls`.
- Never print secrets or tokens. Never commit local agent config.
