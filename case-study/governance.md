# Governance controls

Every control zstack has, at commit `37787071`, with the line that implements it and how it was tested.

**Status** takes one of three values:

- **implemented-and-tested**: implemented, and rerun with its result read while this directory was written on 2 Nov 2026. CI checks also had their failure path checked by breaking the tree on purpose.
- **implemented-untested**: implemented, but not exercised for this commit. Where a PR body records a run, the row says so. That is the author's record, not a rerun.
- **not implemented**: absent, or present only as an instruction to the model. An instruction is listed here because nothing enforces it.

The runs were done by hand in scratch directories. Their output is quoted below and not committed. To repeat one, follow the "How tested" cell.

## Who is governed

There are two actors, and they are governed very differently.

- **The main session** does the work. `bin/pi-run` starts it with the policy appended (`bin/pi-run:37-42`). It has every default tool and the operator's own `gh` login. Its limits are almost all instructions.
- **The reviewer child** is started by the `subagent` tool (`extensions/subagent/index.ts:214-360`). Its limits are mostly mechanisms.

## Reviewer child: mechanisms

| # | Control | Where | Status | How tested |
|---|---|---|---|---|
| C1 | Tool allowlist: the reviewer gets `read, grep, find, ls, bash` and no `edit`, `write` or `subagent` | `agents/onigiri-reviewer.md:4`, passed as `--tools` at `extensions/subagent/index.ts:246` | implemented-and-tested | A real `subagent` call. The child listed its tools as read, grep, find, ls, bash, plus the provider's parallel-call wrapper. This is the child's own report, so it is weaker than reading pi's registry. |
| C2 | Depth limit: a child does not register the `subagent` tool | parse at `index.ts:49-51`, child gets depth + 1 at `index.ts:65`, early return at `index.ts:378` | implemented-and-tested | Top-level `pi -p` with the extension. `PI_SUBAGENT_DEPTH=0` listed `subagent`. `1` and `garbage` did not. Lines 50-51 run under node gave `""`→0, `0`→0, `1`→1, `-1`→1, `abc`→1. |
| C3 | No forge or git credentials in the child: credential-named variables dropped, empty `GH_CONFIG_DIR`, `GIT_TERMINAL_PROMPT=0` | `index.ts:52-66`, applied at `index.ts:283` | implemented-and-tested | Parent started with a dummy `GH_TOKEN` and `GITHUB_TOKEN`. The child ran `gh auth status` and printed "You are not logged into any GitHub hosts", `GIT_TERMINAL_PROMPT` 0, depth 1, and `GH_TOKEN-unset`. Lines 54-56 run under node dropped `GH_TOKEN`, `GITHUB_TOKEN`, `GH_ENTERPRISE_TOKEN`, `GIT_ASKPASS`, `SSH_AUTH_SOCK`, `MY_SECRET` and `NPM_PASSWORD`. They kept `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` and **`AWS_SECRET_ACCESS_KEY`** (see `limits.md`). |
| C4 | The policy reaches the child after a framing paragraph that removes Merging, Checks and Work from its scope | `index.ts:47-48`, `index.ts:89-107`, temp file at `index.ts:271-274` | implemented-and-tested | `ONIGIRI_POLICY` pointed at a marker policy holding one made-up fact. Asked from its instructions alone, the child gave the fact and the marker and said "I am a subagent." An earlier run that asked the child to quote the framing sentence word for word got "Those sentences ... do not appear in my system prompt". A model's report about its own prompt is not reliable. |
| C5 | Child told not to `sleep` or poll | `index.ts:45-46`, added at `index.ts:90` | implemented-and-tested (prompt line only) | In the C4 run the child answered "No, I may not use sleep to wait." This is a prompt line. Nothing stops `bash` running `sleep`. |
| C6 | With no UI and an untrusted project, project-local agents are refused | `index.ts:68-74`, `index.ts:406-418` | implemented-and-tested | A scratch repo with `.pi/agents/planted.md`, then `pi -p` asking for `agentScope: "both"`. The result was "Refused: project agents need a trusted project or an interactive session...". |
| C7 | With a UI, a confirm runs before project-local agents | `index.ts:420-439` (upstream code) | implemented-untested | No TUI run for this commit. |
| C8 | Child transcripts are not saved; only the final text returns to the parent | `--no-session` at `index.ts:239`, documented at `docs/SUBAGENT.md:32` | implemented-untested | Checked by reading the code only. It costs the audit trail and adds no safety. |

## Setup and launch

| # | Control | Where | Status | How tested |
|---|---|---|---|---|
| C9 | One lookup order for the policy, first hit wins: `$ONIGIRI_POLICY`, agent-dir copy, package default. A named but missing file never falls through | `index.ts:76-85`, `bin/pi-run:37-39` | implemented-and-tested | `bin/pi-run` with a stub `pi` on PATH. With an empty agent dir it appended the package `policy.md`. With an agent-dir copy present it appended that copy. With `ONIGIRI_POLICY` set to a missing file it printed "is not readable; session runs without it" and appended nothing. In the extension, the marker run (C4) showed the env step. Startup printed "(agent dir)" for step 2. |
| C10 | A missing or empty policy fails open, loudly, in the child | `index.ts:87-107` | implemented-untested | PR #10's body records the ENOENT notice in a child. Not rerun here. |
| C11 | `setup.sh` never overwrites, and is safe to rerun | `bin/setup.sh:13-20`, `bin/install-agents.sh:10-18` | implemented-and-tested | Run twice against an empty `PI_CODING_AGENT_DIR`. The first run copied the policy and `onigiri-reviewer.md`. The second reported "changed: nothing". |
| C12 | `setup.sh` checks `gh` only for a GitHub repo, and asks before `gh auth login` only with a terminal | `bin/setup.sh:22-32` | implemented-untested (partly run) | Only the non-GitHub branch ran ("gh not checked"). The prompt branch was not run. |
| C13 | `pi-run` rejects a malformed call | `bin/pi-run:22-36` | implemented-and-tested | `pi-run s1` exited 2. `pi-run s1 prov mod brief.md` passed `--provider prov --model mod`. |
| C14 | Session-start dependency check: a TUI confirm offers `setup.sh`; print mode prints the fix and goes on | `index.ts:109-138`, hooked at `index.ts:379` | implemented-untested (partly run) | Only the info line ran ("zstack: onigiri policy ... (agent dir)"). PR #10's body records the TUI No/Yes run and the print-mode gap lines. Not rerun here. |

## CI (`.github/workflows/test.yml`)

The job's own token is `contents: read` (`test.yml:8-9`). The checks are static and call no model. Each step was run locally at `37787071` and passed. GitHub's run on the `main` push of `37787071` concluded `success`.

| # | Control | Where | Status | How tested |
|---|---|---|---|---|
| C15 | Every `bin/` script parses, including `bin/pi-run`, which has no `.sh` | `test.yml:16-18` | implemented-and-tested | Appending `if then` to a copy of `bin/pi-run` passes the old `bin/*.sh` glob (rc 0) and fails `bin/*` (rc 2). |
| C16 | Skills and agents have frontmatter | `test.yml:19-25` | implemented-untested (failure path never run) | Passes on the tree. Never broken on purpose. |
| C17 | Agent files name no model | `test.yml:26-28` | implemented-and-tested | Adding `model: some/model` to the reviewer failed with "set models in pi's config, not here". |
| C18 | Every backticked `zstack-*` or `onigiri-*` name in the docs exists | `test.yml:29-36` | implemented-and-tested | Adding `` `onigiri-ghost` `` to `docs/SUBAGENT.md` failed and named the file. |
| C19 | Every skill and playbook the docs name exists | `test.yml:37-52` | implemented-and-tested | Adding `**figure-it-out**` and `playbooks/nope.md` to `README.md` failed on both. |

## Main session: instructions only

None of these has a mechanism in this repo. The model is asked to follow them, and the operator's `gh` login lets it break any of them.

| # | Control | Where | Status | Note |
|---|---|---|---|---|
| C20 | Holds on PRs, people and paths | `policy.md:7-13` | not implemented | The default list is empty. On the reference host a cron "tick" reads a holds file (`docs/HOST.md:14`, `docs/HOST.md:28`). That code is not in this repo. |
| C21 | Merge only with a `verify: PASS at <sha>` line and a `risk:` line | `policy.md:46-48`, `skills/onigiri-mode/playbooks/change.md:17`, `skills/onigiri-mode/playbooks/pull-requests.md:37`, `:47` | not implemented | The merge gate that read these lines was removed from this repo (`docs/PROVENANCE.md:40`). `main` has no branch protection (`gh api .../branches/main/protection` returns 404). |
| C22 | Never force-push, never `--no-verify`, never retry a refused push | `policy.md:20`, `:41`, `:49` | not implemented | Instruction only. |
| C23 | No `Co-authored-by`, no model names in commit or PR text | `policy.md:24` | not implemented | Instruction only. |
| C24 | One session per task, no nested `pi` | `skills/onigiri-mode/SKILL.md:9`, `:49` | not implemented | `bash` can start `pi`, as `docs/SUBAGENT.md:16` says. C2 bounds only the tool. |
| C25 | Reviewer's `bash` is read-only | `agents/onigiri-reviewer.md:15` | not implemented | "an instruction in the reviewer's prompt, not a boundary" (`docs/SUBAGENT.md:27`). In one run with an injected instruction the reviewer refused (`answers.md`, Q1). That is one sample of model behaviour, not a control. |
| C26 | Pause before force-pushes, deploys, data deletion and messages to people | `skills/onigiri-mode/SKILL.md:57` | not implemented | Instruction only. |

## What is absent

There is no bot identity, no per-child timeout or budget, no concurrency cap across `subagent` calls, no kill switch in the repo, no golden-task set and no drift detection. Each is covered in `limits.md`.
