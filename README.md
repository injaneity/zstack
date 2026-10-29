# zstack

[pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan, trimmed for pi.

pstack splits work across agents: one model designs, a subagent implements, more subagents review and race. zstack keeps the same engineering standard with one main session doing the work, and subagents at four named steps only:

- **Explorers** take wide read-only surveys in `how` and `why`, and return cited notes instead of filling the main context.
- **Two designers** sketch competing shapes for a one-way-door decision in `architect`, without seeing each other.
- **A reviewer** reads the diff cold before every PR, and the decision trail before a long run hands back.
- **A verifier** checks your own PR before it merges.

## install

```bash
pi install git:github.com/injaneity/zstack
pi install npm:pi-subagent-manager
bin/install-agents.sh
```

`bin/install-agents.sh` copies the four agent types into `~/.pi/agent/subagent-manager/agents/` and never overwrites one you already have. Each type runs on your main model until you set one with `/agents types`. In `/agents` settings, set Max Levels to 2 so subagents can't spawn their own, and Model Picking to Pick First (available). Without pi-subagent-manager, the main session does every step itself.

Then type `/skill:onigiri-mode` before a task, or describe the work and let the model route to it.

## what you get

- **`onigiri-mode`**: the non-negotiables, principles, subagent rules, autonomy, and the routing table. Five playbooks: diagnose, change (bug fix, feature, or refactor), measure, pull requests (open, land, verify), and resume. It replaces pstack's `poteto-mode`.
- **`how`** and **`why`**: how code works, and what forces shaped it, with confidence-tiered citations.
- **`architect`**: competing design sketches before code.
- **`figure-it-out`**: designs a workflow when no playbook fits.
- **`show-me-your-work`**: an append-only decision log for long or unattended runs.
- **`unslop`**: strips AI tells from prose.
- **`agents/`**: the four subagent types. None names a model, none can spawn agents, and only the verifier writes files, inside its own worktree.

`docs/PROVENANCE.md` records what changed from pstack and why.

## license

MIT, retained from pstack. Copyright (c) 2026 Lauren Tan for everything derived from it. Fork changes are under the same license.
