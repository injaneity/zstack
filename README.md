# zstack

[pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan, forked for one thread.

pstack splits work across agents: one model designs, a subagent implements, more subagents review and race. zstack keeps the same engineering standard and does all of it in a single agent session. No coordinator, no delegates, no fan-out.

The trade is deliberate. You lose a second author's eyes on the diff. You buy it back by reading your own diff cold against the plan and the principles before you call it done, and by verifying against the real artifact rather than a proxy.

## install

```bash
pi install git:github.com/injaneity/zstack
```

Then, in a session, type `/skill:onigiri-mode` before your task, or just describe the work and let the model route to it. `onigiri-mode` reads your request, picks a playbook, and runs the other skills as the steps need them.

To pin a version:

```bash
pi install git:github.com/injaneity/zstack@v0.1.0
```

To try it without adding it to your settings:

```bash
pi -e git:github.com/injaneity/zstack
```

## what you get

**One mode skill, `onigiri-mode`.** The routing table, the non-negotiables, the principles, the autonomy rules, and the cua hard rules all live here. It replaces pstack's `poteto-mode`.

**Fourteen playbooks.** Diagnose, bug fix, measure, experiment, feature, refactoring, visual parity, authoring a skill, babysit, shipping, opening a PR, multi-phase plan, resume, worktree cleanup. Diagnose absorbs what pstack split across investigation and two forensics playbooks, measure merges perf issue and hillclimb, and experiment merges prototype and eval, because each pair was one job that differed only in who captured the evidence or whether the loop ran once or until a target.

**Twenty-four principle skills.** The same ones pstack ships, minus nothing, with the two that assumed subagents rewritten.

**Eleven utility skills.** `architect`, `how`, `why`, `figure-it-out`, `automate-me`, `tdd`, `unslop`, `sharpen`, `correct`, `blast-radius`, `benchmark-checklist`, plus `technical-writing`, `typescript-best-practices`, and the two verification-skill generators.

## what's gone

`poteto-mode`, `poteto-help`, `arena`, `swarm`, `interrogate`, `reflect`, `orchestrate`, `autopilot-full`, `autopilot-stack`, `teach`, `statechart`, `no-comments`, `ketchup`, `ketchup-video`, `recall`, `in-your-own-words`, `bro`, `setup-pstack`, `make-bot-ui`, and both formal-verification skills. Every one of them exists to spawn, supervise, or fan out across agents, or to serve a chat assistant rather than a coding session.

`docs/PROVENANCE.md` records kept, edited, and dropped for every skill, with the reason.

## differences you will notice

**No `disable-model-invocation`.** pstack marks most skills to run only when you type the slash command. Pi reads that flag as hide-from-the-model, which would leave the model unable to reach any of them on its own. Every skill here is model-invocable.

**`multi-phase-plan` is rewritten.** Upstream it is a coordinator that spawns one owner per PR and runs a ten-lane swarm on each. Here it is a plan one thread runs box by box, with lanes scaled to what the change touches.

**`why` works its evidence categories in turn.** Still seven categories, still the same epistemics framework and templates, just no fan-out. Sources Consulted is one line per category.

**`architect` sketches competing candidates itself.** No arena, no runner models. Two or three whole-shape candidates, one picked, the rejected shape's disqualifying fact written into the rationale.

## license

MIT, retained from pstack. Copyright (c) 2026 Lauren Tan for everything derived from it. Fork changes are under the same license.
