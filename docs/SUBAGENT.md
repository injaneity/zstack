# subagent tool

`extensions/subagent/` gives pi a `subagent` tool. Each call starts a separate `pi` process with its own context window, runs one agent from the package's `agents/`, and returns the agent's final text. A call takes one `agent` and one `task`, plus `variant` for a role with several seats (the two designers).

It is pi 1.4.0's example extension, `examples/extensions/subagent/` in `@earendil-works/pi-coding-agent`, MIT, Copyright (c) 2025 Mario Zechner.

## What is removed

zstack only ever calls one agent with one task, so upstream's parallel (`tasks`) and chain (`chain`) modes are gone, with their concurrency limits and output truncation. So is its custom call and result rendering: pi's default renderer shows the call's arguments and the child's final text, collapsed to a few lines until expanded. The per-child token and cost tally went with it, since only that renderer showed it. `agents.ts` loads only the package's `agents/`: user and project-local agents, `agentScope`, `confirmProjectAgents` and their trust checks are gone, with the `formatAgentList` export.

## The changes

The header of `index.ts` lists every change from upstream. The ones a caller needs to know:

- **Agents come from the package.** Only the package's `agents/` loads: never `~/.pi/agent/agents/`, never a project's. So every host runs the agents on main, and nothing is installed or copied.
- **Role prompts, not the session's rules.** `bin/pi-run` appends onigiri-mode's Hard rules to the parent only. A child gets its agent prompt, the no-polling line and one framing line (`buildChildPrompt` in `prompt.ts`). Each `agents/<name>.md` quotes only the rules its role could break.
- **No nesting through the tool.** A child does not get the `subagent` tool (`PI_SUBAGENT_DEPTH`). The bound is on the tool, not the process: a child's `bash` could still run `pi`.
- **No forge or git credentials.** Variables whose names look like credentials are dropped except the model providers', `GH_CONFIG_DIR` points at an empty directory, and `GIT_TERMINAL_PROMPT=0`.
- **Model by role.** The child's provider and model come from `config/models.json` by agent name, with `variant` picking a seat when the role has several. A seat whose login fails falls back to the parent's model with a `WARNING` line at the top of the result. The `verifier` never falls back: the tool returns an error and runs nothing.
- **Child sessions are saved** in `~/.pi/agent/sessions/subagents/`, named `role:<agent>[:<variant>]`, apart from top-level sessions.

## What "read-only" means

pi enforces an agent's `tools:` list: `explorer`, `designer` and `verifier` get `read, grep, find, ls, bash` plus FFF's `fffind`, `ffgrep` and `fff-multi-grep` (where that package is installed; pi skips a listed tool that is not registered) and no `edit` or `write`. But `bash` is not sandboxed. A child can still write files through bash, run any local command, and reach the network. They keep `bash` because they need `git diff`, `git log`, `git show`, and for the verifier the repo's checks. So "read-only" is an instruction in each agent's prompt, not a boundary. The real bound is trust in that prompt plus the missing credentials: without a token, a child cannot push, merge, comment or close through `gh` or git over HTTPS. Credentials kept elsewhere on disk (an SSH key, a `.netrc`) are not hidden.
