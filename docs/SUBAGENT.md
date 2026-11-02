# subagent tool

`extensions/subagent/` gives pi a `subagent` tool. Each call starts a separate `pi` process with its own context window, runs one agent from `~/.pi/agent/agents/` (or a project's `.pi/agents/` with `agentScope: "both"`), and returns the agent's final text. A call takes one `agent` and one `task`.

It is pi 1.4.0's example extension, `examples/extensions/subagent/` in `@earendil-works/pi-coding-agent`, MIT, Copyright (c) 2025 Mario Zechner.

## What is removed

zstack only ever calls one agent with one task, so upstream's parallel (`tasks`) and chain (`chain`) modes are gone, with their concurrency limits and output truncation. So is its custom call and result rendering: pi's default renderer shows the call's arguments and the child's final text, collapsed to a few lines until expanded. The per-child token and cost tally went with it, since only that renderer showed it. `agents.ts` loses only the `formatAgentList` export, which nothing imported.

## The changes

All are in `index.ts`.

1. **Policy reaches children.** pi-run appends the onigiri policy to the parent only. The child's prompt temp file, passed as `--append-system-prompt`, holds the agent prompt, the no-polling line, a framing paragraph, then the policy file's text as is (it brings its own `# onigiri policy` heading). The framing tells the child it is a subagent: only the policy's hold, patent-hold, scope and commit/PR-text rules apply to it, its Merging, Checks and Work sections are for the top-level session, and its agent prompt's tool limits win. The path is `$ONIGIRI_POLICY`, default `/srv/onigiri/policy.md`. If the file is missing, unreadable or empty, the child's prompt says so in one line and the parent logs it to stderr. The temp file is written for every child, even an agent with an empty body.
2. **Depth limit on the tool.** A child loads this same package, so it would get the tool again. The extension reads `PI_SUBAGENT_DEPTH` and registers nothing when it is 1 or more. Unset counts as 0. Any other value that is not a non-negative integer counts as 1, so garbage or `-1` keeps the tool off. Each child is spawned with the parent's value plus one. So a child cannot call the `subagent` tool. The bound is on the tool, not the process: a child's `bash` could still run `pi` (or `env -u PI_SUBAGENT_DEPTH pi`) and start another session.
3. **No sleep polling.** The child's prompt carries one line telling it not to use `sleep` or repeated polling to wait. Long waits are the tick's job.
4. **Project agents need a trusted project or a UI.** With a UI, the upstream confirm dialog runs for project-local agents in an untrusted project. With no UI (`pi -p`, the tick) and an untrusted project, asking for `agentScope: "project"` or `"both"` returns a tool error instead of running repo-controlled agents unasked. A folder whose only `.pi` content is `agents/` has no protected resources, and pi calls such a folder trusted. So here a project counts as trusted only through a saved decision for it in `trust.json`, or through pi trusting its protected resources. User agents work in print mode as before.
5. **Children get no forge or git credentials.** The child keeps the parent's environment except:
   - any variable whose name contains `TOKEN`, `SECRET`, `PASSW`, `CREDENTIAL`, `API_KEY`, `ACCESS_KEY`, `PRIVATE_KEY`, `AUTH` or `ASKPASS` is dropped, which covers `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN`, `GIT_ASKPASS` and `SSH_AUTH_SOCK`. Model-provider variables (`ANTHROPIC_*`, `CLAUDE_CODE_*`, `OPENAI_*`, `AWS_*`, `GOOGLE_*` and the other provider prefixes pi reads) are kept, because the child needs them to reach its model.
   - `GH_CONFIG_DIR` points at an empty per-child directory, so `gh` finds no stored login, and git's `gh auth git-credential` helper gets nothing either.
   - `GIT_TERMINAL_PROMPT=0`, so git fails instead of asking for a password.

## What "read-only" means

pi enforces an agent's `tools:` list: `onigiri-reviewer` gets `read, grep, find, ls, bash` and no `edit` or `write`. But `bash` is not sandboxed. A child can still write files through bash, run any local command, and reach the network. The reviewer keeps `bash` because it needs `git diff`, `git log` and `git show`. So "read-only" is an instruction in the reviewer's prompt, not a boundary. The real bound is trust in that prompt plus the missing credentials: without a token, a child cannot push, merge, comment or close through `gh` or git over HTTPS. Credentials kept elsewhere on disk (an SSH key, a `.netrc`) are not hidden.

## Behaviour to know

- **Model.** A child gets `--model` from its agent file's `model:`. Without one it inherits the parent's model and thinking level.
- **Transcripts are not saved.** Children run with `--no-session`, so nothing lands in `~/.pi/agent/sessions/`. Only the final text and the tool details in the parent's session survive. That is a known cost: a child's reasoning can't be read back later, so ask it for evidence in its answer.
- **Agents are installed, not discovered.** The loader can't see a package's own `agents/`. Run `bin/install-agents.sh` to copy them into `~/.pi/agent/agents/`.
