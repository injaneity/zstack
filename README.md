# zstack

[pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan, trimmed for pi.

pstack splits work across agents: one model designs, a subagent implements, more subagents review and race. zstack keeps the same engineering standard with one main session doing the work. One subagent, **`onigiri-reviewer`**, reads the diff cold before every PR, and the decision trail before a long run hands back.

## install

```bash
pi install git:github.com/injaneity/zstack
~/.pi/agent/git/github.com/injaneity/zstack/bin/install-agents.sh
```

The package brings the `subagent` tool (`extensions/subagent/`, vendored from pi's example, see `docs/SUBAGENT.md`). The tool only finds agents in `~/.pi/agent/agents/` and a project's `.pi/agents/`, and a package can't add its own `agents/` to that list, so `bin/install-agents.sh` copies them into `~/.pi/agent/agents/`. It never overwrites a file you already have. An agent runs on your main model unless you add `model:` to its installed copy.

Then type `/onigiri-mode <task>` (or `/skill:onigiri-mode <task>`), or describe the work and let the model route to it. The `/onigiri-mode` prompt template expands inside pi, so it works on any provider, including claude-code.

## what you get

- **`onigiri-mode`**: the non-negotiables, principles, subagent rules, autonomy, and the routing table. Five playbooks: diagnose, change (bug fix, feature, or refactor), measure, pull requests (open, land, verify), and resume. It replaces pstack's `poteto-mode`.
- **`how`** and **`why`**: how code works, and what forces shaped it, with confidence-tiered citations.
- **`architect`**: competing design sketches before code.
- **`figure-it-out`**: designs a workflow when no playbook fits.
- **`show-me-your-work`**: an append-only decision log for long or unattended runs.
- **`unslop`**: strips AI tells from prose.
- **`subagent` tool**: pi's example extension with four changes. Children get the onigiri policy, can't spawn their own, and are told not to sleep-poll.
- **`agents/`**: `onigiri-reviewer`, read-only, names no model.

`docs/PROVENANCE.md` records what changed from pstack and why. `docs/SUBAGENT.md` covers the tool.

## license

MIT, retained from pstack. Copyright (c) 2026 Lauren Tan for everything derived from it. Fork changes are under the same license. `extensions/subagent/` is from pi, MIT, Copyright (c) 2025 Mario Zechner.
