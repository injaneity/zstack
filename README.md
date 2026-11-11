# zstack

[pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan, trimmed for pi.

pstack splits work across agents: one model designs, a subagent implements, more subagents review and race. zstack keeps the same engineering standard with one main session doing the work. The main session builds. A **`verifier`** owns verification (`agents/verifier.md`), an optional **`explorer`** answers narrow reading questions, and two competing **`designer`**s plan deep or new work. Each role's model is in `config/models.json`.

## install

```bash
pi install git:github.com/injaneity/zstack
```

That installs the skills, extensions and agents. A fresh host needs these too (a fresh-HOME install on 11 Nov 2026 failed without each one):

- **Tools** on `PATH`: `pi`, `node`, `git`, `gh`, and `python3` 3.9 or newer.
- **The claude-code provider** for the `coding`, `research` and `designer:1` roles: the Claude Code CLI (`npm install -g @anthropic-ai/claude-code`, then `claude auth login`, or `CLAUDE_CODE_OAUTH_TOKEN` exported by the host file) and the pi package, pinned:
  `pi install git:github.com/earendil-works/pi-claude-code@70abb65186592ca788b29b3db22c3160d629c41f`.
  Later commits (from d070212, which sets `CLAUDE_CODE_ENTRYPOINT=pi-agent`) get every request refused as third-party usage on a Claude plan, while pi still lists the models.
- **The openai-codex provider** for `explorer`, `verifier` and `designer:2`: run `pi`, then `/login` and pick ChatGPT. Without it the verifier refuses to run and the explorer falls back to your model.
- **FFF's search tools** for the agents: `pi install npm:@ff-labs/pi-fff@0.11.0`. Without it pi drops `fffind`, `ffgrep` and `fff-multi-grep` from their tool lists without a word.
- **`gh auth login`**, for the hourly pass, the merge job and PRs.

Then run `bin/check-host` (`--offline` skips the live calls). It prints one `ok` or `FAIL` line per dependency, makes one real `pi -p` call per provider, runs `hosts/check-parity`, and exits 1 on any `FAIL`. A deployment's host file, cua clone, holds file and webhook key are `docs/HOST.md`'s. `config/ci.json` `fix_attempts` is how many red heads of one PR reopen the thread that pushed them. `config/models.json` maps each role (`coding`, `research`, and the subagents `explorer`, `designer`, `verifier`) to a provider and model. `bin/pi-run <id> <role> <brief-file>` starts a detached print-mode session with onigiri-mode's Hard rules appended (`bin/pi-run --help`). Its lifecycle is one state machine, `bin/PiRun.tla`, which CI model-checks and holds the launcher to. `model/Zstack.tla` is the whole flow from the chat to merged, with the merge job. CI model-checks it, and its mutants (`model/mutants.py`) each put back a bug the flow had. `docs/HOST.md` and `hosts/` are one example deployment.

Then type `/onigiri-mode <task>` (or `/skill:onigiri-mode <task>`), or describe the work and let the model route to it. The `/onigiri-mode` prompt template expands inside pi, so it works on any provider, including claude-code.

## what you get

- **`onigiri-mode`**: one skill for both readers, the pi session that does the work and the chat orchestrator that files it. It holds the hard rule (coding runs in pi, one session per task), the principles, subagent rules, autonomy, and the routing table, and beside it `hard-rules.md` (holds, patent hold, commit and PR text, checks, scope, merging), the one copy of the Hard rules, which `bin/pi-run` appends to every session's system prompt. Five playbooks: diagnose, change (bug fix, feature, or refactor), measure, pull requests (open, land, verify), and resume. It replaces pstack's `poteto-mode`.
- **`how`** and **`why`**: how code works, and what forces shaped it, with confidence-tiered citations.
- **`architect`**: competing design sketches before code.
- **`unslop`**: strips AI tells from prose.
- **`create-verification-skill`** and **`maintain-verification-skill`**: generate a repo's own `.pi/skills/verify-<app>/` skill that launches the real app, drives it like a user, and captures evidence, then keep its feature map honest as the app changes.
- **`statechart`**, **`create-formal-verification-skill`** and **`maintain-formal-verification-skill`**: pstack's own text, unchanged. statechart model-checks a TLA+ spec with TLC and draws it as a Mermaid statechart (the first run installs Java and TLC through mise, so it needs mise and the network, unless `TLA2TOOLS` points at a local `tla2tools.jar`, with `JAVA` optionally naming the Java binary). The other two generate a repo's formal verification skill and keep it in step with the code. All three are slash-command only: `/skill:<name>`.
- **`verify-cua-driver`** and **`verify-cua-spaces`** (`skills/cua/`): trycua/cua's own verify skills, copied unchanged so every host with zstack installed loads them. pi doesn't read a repo's `.cursor/skills/`. Their commands run from the root of a trycua/cua checkout and use that checkout's `.cursor/skills/*/scripts/`, so the scripts are not copied, and their `../../../libs/` links resolve only from that checkout's copy. `docs/PROVENANCE.md` says how to refresh them.
- **`subagent` tool** and **`agents/`** (`explorer`, `designer`, `verifier`, no model named in the files): pi's example extension, cut to one agent per call, each child getting only its role prompt (its place in the pipeline, the skill it loads, the rules it could break), with safety changes and a per-role model from `config/models.json`. `docs/SUBAGENT.md` lists them and says why "read-only" is a prompt instruction, not a boundary.

- **`tool-guard` extension** (`extensions/tool-guard/`): adds three wall-clock rules to every session's system prompt: no whole-disk `find` for a tool, no `sleep`-and-`tail` polling, and time-bounded measurements.
- **`rule-guard` extension** (`extensions/rule-guard/`): blocks the bash and write calls onigiri-mode's Hard rules ban outright: force-push, skipping the pre-push hook, `gh pr merge`, `gh pr ready` (unless the brief says `merge: allowed`) or a rerun of really failed CI in `trycua/cua` (cancelled and timed-out runs rerun), and writes to the real holds file (a test root's `hold-prs.txt` is not one) other than `bin/hold-row`, which adds, retires or replaces one row relaying Zane's call. `docs/RULE-GUARD.md` has the details.

`docs/PROVENANCE.md` records what changed from pstack and why.

## license

MIT, retained from pstack. Copyright (c) 2026 Lauren Tan for everything derived from it, including the three skills copied verbatim from pstack. Fork changes are under the same license. `extensions/subagent/` is from pi, MIT, Copyright (c) 2025 Mario Zechner.
