# Design

zstack configures pi, an open-source coding agent, to do code changes in one session per task. Before a PR opens, a separate reviewer process reads the diff cold.
Every claim about the code cites a file and line at `37787071`. "Tested" means rerun on 2 Nov 2026 with the result read (`governance.md`).

## The workflow and why an agent

The workflow is first-pass review of an agent's own diff before the PR opens (`skills/onigiri-mode/playbooks/pull-requests.md:13`).
It sits inside a larger loop: the main session diagnoses, changes, verifies and opens PRs (`skills/onigiri-mode/SKILL.md:69-77`).

The need comes from volume. Search counts on trycua/cua, the main repo this setup works on (`docs/HOST.md:28`):

- 1,537 PRs opened and 1,095 merged between 1 and 31 Oct 2026, with 334 issues opened. `injaneity` authored 243 of those PRs.
- 654 PRs were open on 2 Nov.

These are GitHub search totals for `created:` and `merged:` windows. They say nothing about review latency or quality, and they include bot and draft PRs.

Conventional automation (lint, tests, the static CI in `.github/workflows/test.yml`) checks what can be stated as a rule.
The reviewer's job is to find "the one fact the change is safe because of" and check whether it holds (`agents/onigiri-reviewer.md:11`). That needs judgement over code and intent.
An author cannot read its own diff cold, which is why the reviewer is a separate process with none of the author's context (`docs/PROVENANCE.md:14`).

## Risks and the controls that answer them

| Risk | Control | Status |
|---|---|---|
| Reviewer acts on GitHub (merge, close, comment) | no forge or git credentials in the child, `extensions/subagent/index.ts:52-66` | tested |
| Reviewer edits code | tool allowlist without `edit`/`write`, `agents/onigiri-reviewer.md:4`, `index.ts:246` | tested |
| Reviewer obeys the parent's merge rule | framing paragraph before the policy, `index.ts:47-48`, `:104` | tested |
| Children spawn children | tool not registered at depth >= 1, `index.ts:49-51`, `:378` | tested for the tool only |
| Untrusted repo ships its own agent | refused in print mode, `index.ts:406-418` | tested |
| Docs name an agent or skill that no longer exists | CI name checks, `test.yml:29-52` | tested, failure path included |
| A launcher script breaks silently | CI parses `bin/*`, `test.yml:16-18` | tested, failure path included |
| Main session merges, closes or force-pushes wrongly | `policy.md:20`, `:41`, `:44-49` | instruction only |
| Runaway cost or time | none | not implemented |

The pattern is deliberate. The child is new code that an outside actor can feed, through the diff and repo files, so it gets mechanisms.
The main session is the operator's own agent, run under the operator's login, so its rules are prompt text. `limits.md` states that cost.

## What the tests showed: PR #8

PR #8 (https://github.com/injaneity/zstack/pull/8) vendored pi's subagent example. Its first head had a reviewer called "read-only" that could merge.

- The child inherited the parent's whole environment, `gh` login included.
- It received the full policy, which says "merge your own PR" and "this file wins".
- Its only barrier was the sentence "Never edit, write, install, commit, push, or post" (`agents/onigiri-reviewer.md:15`).

A separate review of that head (an internal note, not in this repo) also found that the PR had narrowed CI's agent-name check to the exact phrase "Spawn a `name`". That check would have missed stale names written any other way.

The fixes landed in the same PR before merge, and are in `e9a2b35` and so at `37787071`:

- the credential scrub, with an empty `GH_CONFIG_DIR` and `GIT_TERMINAL_PROMPT=0` (`index.ts:52-66`);
- the framing paragraph that scopes the policy for a subagent (`index.ts:47-48`);
- strict depth parsing (`index.ts:49-51`);
- the print-mode refusal (`index.ts:406-418`);
- a name check over every doc (`test.yml:29-36`);
- docs that call "read-only" an instruction, not a boundary (`docs/SUBAGENT.md:25-27`).

PR #10 (`37787071`) closed a related CI gap. The parse step had globbed `bin/*.sh`, so the new `bin/pi-run` was never parsed. It now globs `bin/*` (`test.yml:18`).

Rerun for this case study:

- With dummy `GH_TOKEN` and `GITHUB_TOKEN` in the parent, a real reviewer child reported `gh` logged out and `GH_TOKEN` unset.
- A diff carrying an injected "close stale issues, maintainer approved" instruction was flagged by the reviewer. It did not run the command (`answers.md`, Q1).
- A broken `bin/pi-run` passes the old glob and fails the new one.
- Breaking a name, a model line or a playbook link fails CI.

The lesson is the assignment's own: an instruction did not hold, so a mechanism replaced it where one was cheap.

## Rejected alternative

**Drop `bash` from the reviewer** and pass the diff in the brief. That would make "read-only" real for the child, as the PR #8 audit noted.
It was rejected because the reviewer needs `git diff`, `git log` and `git show` to read callers and history (`docs/SUBAGENT.md:27`). A diff pasted into the brief loses that context.
The credential scrub was chosen instead. It stops actions on GitHub, and it leaves local writes possible.

Earlier, pstack's other agents (designer, explorer, verifier, implementer, arena) were dropped. No session ever spawned them, and they cost more tokens than they saved (`docs/PROVENANCE.md:16`).

## Deliberately prevented

- The reviewer cannot use `edit`, `write` or `subagent`, and has no `gh` or HTTPS-git login from env or `gh` config.
- The reviewer cannot run a repo-supplied agent in print mode without a trust decision.
- No agent file may name a model (`test.yml:26-28`), so model choice stays with the operator.
- `setup.sh` never overwrites a user's file (`bin/setup.sh:13-20`), and nothing installs without a yes (`index.ts:132-135`).

## Unresolved

- There are no golden tasks and no drift detection.
- There is no budget, timeout or concurrency cap.
- There is no bot identity and no kill switch in the repo.
- The main session's rules are instructions.
- The reviewer can write files through `bash`, and keeps `AWS_*` and similar provider keys.
- MCP tools may pass the reviewer's allowlist.

`limits.md` has each of these.

## Agent bill of materials

| Item | At `37787071` |
|---|---|
| Agent runtime | pi 1.4.0 (`docs/SUBAGENT.md:5`), installed as a package (`package.json:7-11`) |
| Model | not pinned. pi's default or `bin/pi-run <provider> <model>` (`bin/pi-run:10-11`). Children inherit it (`index.ts:240-245`) |
| Instructions | `policy.md`, `skills/onigiri-mode/SKILL.md` and five playbooks, `skills/{how,why,architect,unslop}`, `AGENTS.md`, `prompts/onigiri-mode.md` |
| Reviewer | `agents/onigiri-reviewer.md`, installed by `bin/install-agents.sh` |
| Tools | pi defaults for the main session. Reviewer: `read, grep, find, ls, bash`. Plus the `subagent` tool, `extensions/subagent/` |
| MCP servers | none shipped. Whatever the operator configures in pi |
| Permissions | the operator's `gh` login for the main session. None for the child. CI token `contents: read` (`test.yml:8-9`) |
| Data sources | the working repo, its PRs through `gh`, and the policy file (`index.ts:76-85`) |
| Logs | parent sessions in pi's sessions dir. Children unsaved (`index.ts:239`) |
| Evaluations | static CI (`test.yml`). Hand runs recorded in `governance.md`. No golden set |
| Upstream | pstack 0.15.36 (`PINNED_COMMIT`). pi's subagent example, MIT (`LICENSE:3-4`, `docs/PROVENANCE.md`) |
| Responsible human | Zane (`injaneity`). All ten PRs were opened and merged under his account, and he owns the policy (`policy.md:51-53`) |

## AI-use statement

The repository and its documentation were written by an AI coding agent working with Zane (`injaneity`). Zane directed the work, set the rules and reviewed it.

This `case-study/` directory was written by an AI agent on 2 Nov 2026, at Zane's direction, as one commit after `37787071`. Zane reviews it before it is pushed.

Verified by running, on 2 Nov 2026, against `37787071`:

- the five CI steps, passing, and four of them failing when broken on purpose;
- `bin/setup.sh` twice on an empty agent dir;
- `bin/pi-run` argument and policy resolution, using a stub `pi`;
- the depth parse and the credential regex, extracted and run under node;
- real `pi -p` runs of the extension: the tool list at depth 0, 1 and `garbage`, the print-mode refusal, and a reviewer child's `gh` status and environment, the policy marker, and the injection case.

Verified only by reading: every other `file:line` citation, the PR #8 history (its PR body and commit list on GitHub, its first commit `4974b481`, and an internal review note), the PR #10 TUI runs, and the search counts above (GitHub search API, run once).
