# subagent tool

`extensions/subagent/` gives pi a `subagent` tool. Each call starts a separate `pi` process with its own context window, runs one agent from `~/.pi/agent/agents/` (or a project's `.pi/agents/` with `agentScope: "both"`), and returns the agent's final text. It has single, parallel and chain modes.

It is pi 1.4.0's example extension, `examples/extensions/subagent/` in `@earendil-works/pi-coding-agent`, MIT, Copyright (c) 2025 Mario Zechner. `index.ts` and `agents.ts` are kept close to upstream so a diff against a newer example stays readable.

## The four changes

All are in `index.ts`. `agents.ts` is unchanged apart from its header.

1. **Policy reaches children.** pi-run appends `/srv/onigiri/policy.md` to the parent only. The child's prompt temp file, passed as `--append-system-prompt`, now holds the agent prompt, then the policy under an `# onigiri policy` heading. A missing policy file is skipped, not an error. The temp file is now written for every child, even an agent with an empty body.
2. **Depth limit of one.** A child loads this same package, so it would get the tool again. The extension reads `PI_SUBAGENT_DEPTH` (default 0) and registers nothing when it is 1 or more. Each child is spawned with the parent's value plus one. So a top-level session can spawn children, and children can't spawn.
3. **No sleep polling.** The child's prompt carries one line telling it not to use `sleep` or repeated polling to wait. Long waits are the tick's job.
4. **Print mode.** No code change. `ctx.hasUI` is read in one place, the confirm dialog before running project-local agents in an untrusted project. Without a UI that dialog is skipped and the call goes ahead, so `pi -p` and the tick's runs can call the tool.

## Behaviour to know

- **Model.** A child gets `--model` from its agent file's `model:`. Without one it inherits the parent's model and thinking level.
- **Transcripts are not saved.** Children run with `--no-session`, so nothing lands in `~/.pi/agent/sessions/`. Only the final text and the tool details in the parent's session survive. That is a known cost: a child's reasoning can't be read back later, so ask it for evidence in its answer.
- **Agents are installed, not discovered.** The loader can't see a package's own `agents/`. Run `bin/install-agents.sh` to copy them into `~/.pi/agent/agents/`.
