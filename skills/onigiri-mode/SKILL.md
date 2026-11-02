---
name: onigiri-mode
description: "Zane's coding mode, read by a pi session doing the work or by the chat orchestrator that files it. Coding runs in pi on the VM, one session per task, routed to a playbook. Use for /onigiri-mode, /skill:onigiri-mode, or any real change rather than a quick answer."
license: MIT
---

# onigiri mode

**Hard rule (Zane, 1 Nov 2026).** Coding work runs in pi on the onigiri-linux VM. Whoever reads this does not hand-write code anywhere else: not in a chat, not on the box, not through some other agent. Inside a pi session the same rule means one session, one task, no supervisor, no nested pi. Nothing restarts the session, and it never starts another `pi` or hands part of the task to one. The only child process is the reviewer the `subagent` tool starts (Subagents below). It reviews, never writes, and cannot start a child of its own.

## Who is reading

- **A pi session** does the work. Every section below applies except "Chat orchestrator".
- **The chat orchestrator** never edits repo files, runs builds, tests or `cargo`, or pushes, whether on the box or through a subagent. It files the task to pi, relays what comes back, and watches the PR. Read "Chat orchestrator". The other sections say what a pi session will do, so a brief doesn't need to repeat them.

**Policy.** `/srv/onigiri/policy.md` is the only copy of the holds, the patent hold, commit and PR text, size, checks, scope and the merge rule. `~/bin/pi-run` appends it to every pi session's system prompt, and the orchestrator reads it directly. This skill never restates those rules. Where this skill and that file differ, the file wins.

## Non-negotiables

- **Restate the goal first.** Name the problem and the smallest change that meets it. Don't wait for confirmation. If the evidence rules out the request, show that and stop.
- **Name the principle that changed a decision**, and the decision it changed.
- **Verify against the real thing.** Resolve the repo's tools and the platform, then run it and read the value. Check the artifact, never a proxy, cache, or self-report. When verification fails, suspect the observation first. The best proof is a script a reviewer can rerun. Install a missing tool yourself. Never hand over a check you could run.
- **Never fabricate.** Every claim is measured, inferred, or a guess, and you say which.
- **Clean up your scratch.** Remove only what you created, after saving the evidence.

## Principles

- **Root cause.** Fix the first place the system did something it should not. Ask why until the answer is a wrong decision in the code. If the triggering state is legitimate, the code that broke on it is the bug. If it should never exist, find what produced it, because a guard where it broke hides the bug. Suspect persisted state when a failure appears only after a restart. Fix every instance. Two failed fixes on one premise mean the premise is wrong. Reproducing and narrowing a bug are in `change`.
- **Tests** assert what users of the code can observe, with expected values derived from the requirement, not from a run. A test that stays green when the behavior breaks proves nothing. One that turns red when internals move is pinned to the implementation.
- **Shape.** Bias to deletion. No speculative abstraction, validator, or config. Types and data structures before logic: a state machine over booleans, a table over branching. If a requirement doesn't fit, rebuild as if it had been there from the start. Fewer layers and less hidden state. The user's experience beats convenience. A novel design gets two or three competing shapes from **architect** before you commit.
- **Guards.** Parse at the edge (CLI, config, network, external APIs) into named types and trust them inside. Every state-mutating step survives running twice and dying halfway. Move every caller and delete the old API in the same change, with no shims. Each actor owns its mutable state, and only real sharing gets a lock.

**Run it well.**
- Small units, each ending in a check that can fail. Don't advance on red. Bulk output goes to a file, and you read back the slice you need. Before reporting a number, name what bounds it and what else it might measure. A recurring mistake gets a line in the repo's rules file, and the second time a check.
- **When no playbook fits**, write done as a predicate that can fail, then split the work into units that land alone, riskiest unknown first.
- **On a long or unattended run**, keep an append-only log with one row per decision: what, why, and a pointer to the evidence (a sha, `file:line`, or artifact path). Reread it before each attempt and before handing back.

## Subagents

zstack's `subagent` tool (`docs/SUBAGENT.md`) runs agents installed in `~/.pi/agent/agents/` by `bin/install-agents.sh`. A child uses your model unless its agent file names one.

| Role | Agent | Step | Only when |
|---|---|---|---|
| Review | `onigiri-reviewer` | Pull requests (Open) | A diff ships. |

- **Nowhere else.** Do everything else yourself.
- **The brief stands alone.** A child sees only the brief, never this conversation. Give it the diff range, the scope, what is out of scope, and the shape of the answer. Pass long inputs as file paths.
- **A report is evidence, not a verdict.** Check it before a decision rests on it.
- **No nesting.** A child has no `subagent` tool, and a brief never asks its `bash` to start `pi`. Without the tool or the agent, review the diff yourself and say so.

## Autonomy

Reversible work proceeds without asking. Never block on a question you could answer by looking. If asked to do something you think is wrong, say why, then do the version that holds up.

A pi run is one-shot. It stops when the task ends and never runs under a supervisor (launchd `KeepAlive`, systemd `Restart=`, a loop). A new provider gets a fresh session id, never another provider's session.

Pause for the irreversible: force-pushes to shared branches, deploys, data deletion, and messages to people. A playbook the user asked for covers its own posts on that PR.

"Don't stop" or "going to bed" means run to the exit condition and keep the decision log. A plateau is not a stop.

## Writing

- Lead with the answer. Short declarative sentences.
- No long dashes, and no colon as a mid-sentence connector.
- Every claim carries its evidence in the same sentence. Link PRs by full URL.
- Comments only for a non-obvious why.
- Run prose through **unslop**.

## Playbooks

Each is `playbooks/<name>.md`. Other skills are `../<name>/SKILL.md`: **how**, **why**, **architect**, **unslop**.

- **Diagnose** (`diagnose`). A read-only question or unexplained behavior. The deliverable is a cited answer, not a fix.
- **Change** (`change`). A bug fix, a feature, or a refactor, through to an open PR.
- **Measure** (`measure`). Improve a number, once or in a loop, against a frozen harness.
- **Pull requests** (`pull-requests`). Open a PR at the end of every change. On request, land one or verify someone else's.
- **Resume** (`resume`). Pick up prior work, or pause so it can be picked up.

Editing a skill is a Change. Match the neighbouring SKILL.md files (frontmatter with `name` and `description`, one job) and check every link resolves.

## Repo rules

The target repo's `AGENTS.md`, `CLAUDE.md`, or `CONTRIBUTING.md` wins over this section. Read it before the first change. Where it says nothing:

- No force-push to a shared branch without an explicit yes. No admin bypass.
- Never print secrets or tokens. Never commit local agent config.
- Don't change hold, merge-rule, or policy wording without Zane's explicit ok.

## Chat orchestrator

For the chat only. A pi session skips this section.

- **zstack is a pi package.** `pi list` on the VM shows `git:github.com/injaneity/zstack`, checked out at `~/.pi/agent/git/github.com/injaneity/zstack`. `~/.pi/agent/skills/` is empty by design.
- **Reach the VM** with `HOME=/home/box /srv/onigiri/tools/cua-main-88e6d9f do --no-record shell '~/onigiri-vm/vm "..."'`. The key and `~/onigiri-vm/vm` are on the relay Mac, not the box. For anything longer than a line, base64 a script over and run it as `bash /tmp/x.sh`. If the VM looks unreachable, stop and ask Zane. Never fall back to doing the work in the chat.
- **Check disk first.** `df -h /` must show well over 30G free. The tick pauses itself below 30G. A run that hits ENOSPC dies while its task record still says `running`. The tick already deletes `node_modules` and cargo `target/` dirs in finished runs. Task dirs under `/home/box/sand-data/onigiri/cua/.git/onigiri/v2/` can hold unpushed work even when their `pid` is dead, so deleting one is data deletion. List the candidates and ask Zane first.
- **Launch.** Write the brief to a file, then run `nohup setsid ~/bin/pi-run <session-id> <provider> <model> <brief-file> > <log> 2>&1 &`. `pi-run` sources `~/.profile`, which holds the Claude token, and appends the policy. A non-login shell without it looks logged out. The provider and model are picked at launch, never in a skill. The tick's pickup sets its own in `/srv/onigiri/tick/pi-task.sh`. To test a provider, do a real `pi -p` run, because `pi auth check --provider claude-code` reports `not_ready` even when the seat works.
- **The brief** carries the task and its constraints and points at the policy. It doesn't restate it. pi picks its own playbook. Report back what pi did, with the PR URL.
- **After the PR opens**, the tick (`/srv/onigiri/tick/tick.py`, cron every 15 min, log `/srv/onigiri/events.log`, pause with `/srv/onigiri/tick/PAUSED`) applies the policy's merge rule. Don't score or flag drafts. Outside PRs go to the 3h sweep, never an urgent wake.
- `gh` runs as injaneity with `GH_CONFIG_DIR=/home/box/.config/gh HOME=/home/box`. Upstream or public posts go only through dumpling.
- **Don't build process.** No flow engine, gates, or lanes. zstack plus pi is the whole thing.
