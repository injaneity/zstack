# zstack

[pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan, trimmed for pi.

pstack splits work across agents: one model designs, a subagent implements, more subagents review and race. zstack keeps the same engineering standard with one main session doing the work. One subagent, **`onigiri-reviewer`**, reads the diff cold before every PR.

## install

```bash
pi install git:github.com/injaneity/zstack
~/.pi/agent/git/github.com/injaneity/zstack/bin/install-agents.sh
```

The package brings the `subagent` tool (`extensions/subagent/`, vendored from pi's example, see `docs/SUBAGENT.md`). The tool only finds agents in `~/.pi/agent/agents/` and a project's `.pi/agents/`, and a package can't add its own `agents/` to that list, so `bin/install-agents.sh` copies them into `~/.pi/agent/agents/`. It never overwrites a file you already have. An agent runs on your main model unless you add `model:` to its installed copy.

Then type `/onigiri-mode <task>` (or `/skill:onigiri-mode <task>`), or describe the work and let the model route to it. The `/onigiri-mode` prompt template expands inside pi, so it works on any provider, including claude-code.

## what you get

- **`onigiri-mode`**: one skill for both readers, the pi session that does the work and the chat orchestrator that files it. It holds the hard rule (coding runs in pi, one session per task), the principles, subagent rules, autonomy, and the routing table, and points at the onigiri policy file instead of restating it. Five playbooks: diagnose, change (bug fix, feature, or refactor), measure, pull requests (open, land, verify), and resume. It replaces pstack's `poteto-mode`.
- **`how`** and **`why`**: how code works, and what forces shaped it, with confidence-tiered citations.
- **`architect`**: competing design sketches before code.
- **`unslop`**: strips AI tells from prose.
- **`subagent` tool** and **`agents/`** (`onigiri-reviewer`, no model named): pi's example extension, cut to one agent per call, with five safety changes. `docs/SUBAGENT.md` lists them and says why "read-only" is a prompt instruction, not a boundary.

`docs/PROVENANCE.md` records what changed from pstack and why.

## license

MIT, retained from pstack. Copyright (c) 2026 Lauren Tan for everything derived from it. Fork changes are under the same license. `extensions/subagent/` is from pi, MIT, Copyright (c) 2025 Mario Zechner.
