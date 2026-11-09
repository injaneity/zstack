# zstack

[pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan, trimmed for pi.

pstack splits work across agents: one model designs, a subagent implements, more subagents review and race. zstack keeps the same engineering standard with one main session doing the work. The main session builds. A **`verifier`** reviews every diff cold and runs its checks, an optional **`explorer`** answers narrow reading questions, and two competing **`designer`**s plan deep or new work. Each role's model is in `config/models.json`.

## install

```bash
pi install git:github.com/injaneity/zstack
~/.pi/agent/git/github.com/injaneity/zstack/bin/setup.sh
```

`setup.sh` is safe to re-run. It copies the default `policy.md` to `~/.pi/agent/onigiri/policy.md` if absent (edit that copy to add your holds), runs `bin/install-agents.sh`, and checks `gh` only for a GitHub repo. It changes nothing you already have. You still log in to each provider yourself. `config/models.json` maps each role (`builder`, `explorer`, `designer`, `verifier`, `research`) to a provider and model, with the launchers' older names (`coding`, `reads`, `review`) as aliases, and `bin/model-for <role>` prints the pair. Start a print-mode session with the policy appended with `bin/pi-run <session-id> [<provider> <model>] <brief-file>`, for example `bin/pi-run s1 $(bin/model-for coding) brief.md --name role:coding` (the name is how `bin/check-models` knows the role). Without a provider and model pi uses its own default. `bin/check-models` compares what each session under a review directory actually ran on with the config. `docs/HOST.md` and `hosts/` are one example deployment.

The package brings the `subagent` tool (`extensions/subagent/`, vendored from pi's example, see `docs/SUBAGENT.md`). The tool only finds agents in `~/.pi/agent/agents/` and a project's `.pi/agents/`, and a package can't add its own `agents/` to that list, so `bin/install-agents.sh` copies them into `~/.pi/agent/agents/`. It never overwrites a file you already have. An agent runs on the provider and model `config/models.json` gives its name, or on your main model when the config has no entry for it.

Then type `/onigiri-mode <task>` (or `/skill:onigiri-mode <task>`), or describe the work and let the model route to it. The `/onigiri-mode` prompt template expands inside pi, so it works on any provider, including claude-code.

## what you get

- **`onigiri-mode`**: one skill for both readers, the pi session that does the work and the chat orchestrator that files it. It holds the hard rule (coding runs in pi, one session per task), the principles, subagent rules, autonomy, and the routing table, and points at the onigiri policy file instead of restating it. Five playbooks: diagnose, change (bug fix, feature, or refactor), measure, pull requests (open, land, verify), and resume. It replaces pstack's `poteto-mode`.
- **`how`** and **`why`**: how code works, and what forces shaped it, with confidence-tiered citations.
- **`architect`**: competing design sketches before code.
- **`unslop`**: strips AI tells from prose.
- **`create-verification-skill`** and **`maintain-verification-skill`**: generate a repo's own `.pi/skills/verify-<app>/` skill that launches the real app, drives it like a user, and captures evidence, then keep its feature map honest as the app changes.
- **`statechart`**, **`create-formal-verification-skill`** and **`maintain-formal-verification-skill`**: pstack's own text, unchanged. statechart model-checks a TLA+ spec with TLC and draws it as a Mermaid statechart (the first run installs Java and TLC through mise, so it needs mise and the network, unless `TLA2TOOLS` points at a local `tla2tools.jar`, with `JAVA` optionally naming the Java binary). The other two generate a repo's formal verification skill and keep it in step with the code. All three are slash-command only: `/skill:<name>`.
- **`subagent` tool** and **`agents/`** (`explorer`, `designer`, `verifier`, no model named in the files): pi's example extension, cut to one agent per call, with safety changes, a per-role model from `config/models.json`, and a session-start dependency check. `docs/SUBAGENT.md` lists them and says why "read-only" is a prompt instruction, not a boundary.

- **`tool-guard` extension** (`extensions/tool-guard/`): refuses a bash `find` over the whole disk or a whole home directory, and adds rules against that and `sleep`-and-`tail` polling to every session's system prompt. `docs/TOOL-GUARD.md` has the details.
- **`rule-guard` extension** (`extensions/rule-guard/`): blocks the bash and write calls the onigiri policy bans outright: force-push, skipping the pre-push hook, `gh pr merge`, `gh pr ready` (unless the brief says `merge: allowed`) or a CI rerun in a repo listed in `$ONIGIRI_MERGE_OWNED`, and writes to `hold-prs.txt`. `docs/RULE-GUARD.md` has the details.

`docs/PROVENANCE.md` records what changed from pstack and why.

## license

MIT, retained from pstack. Copyright (c) 2026 Lauren Tan for everything derived from it, including the three skills copied verbatim from pstack. Fork changes are under the same license. `extensions/subagent/` is from pi, MIT, Copyright (c) 2025 Mario Zechner.
