# zstack provenance

Forked from pstack 0.15.36 at commit `23ab063af02bb9b9c97936b0563879dd6bf21a05`. Every kept skill is rewritten shorter, and none of the upstream text is verbatim any more, except in the two verification skills and the three vendored skills below.

## Changed everywhere

- **Model-invocable.** `disable-model-invocation: true` is stripped. In pi it hides a skill from the model instead of reserving it for a slash command.
- **pi, not Cursor.** No `/loop`, cloud agents, `control-ui`, todo tool, Bugbot rubric, or `.cursor/` paths. Sessions are read from `~/.pi/agent/sessions/`, and PRs through `gh`.
- **Repo-agnostic.** Project rules live in each repo's own `AGENTS.md`, and onigiri-mode's repo rules defer to it.
- **Models per role live in the config.** pstack named a model per role inside its skills. zstack names none in skills or agent files. `config/models.json` maps each role to a provider and model.

## Subagents

The `subagent` tool is vendored from pi 1.4.0's example extension and replaced pi-subagent-manager. `docs/SUBAGENT.md` has its source, what was cut, and its changes.

The explorer, designer and verifier agents were dropped in 0.2.0, when no session ever spawned them and a cold reviewer was the only agent. They came back, cheaper and with simple gates, when Zane chose a split by role: the main session builds, an optional `explorer` reads, two `designer`s compete on deep or new work only, and a `verifier` replaces that reviewer, reviewing the diff and running the matching verify skill. Explorer and verifier run on a cheaper model than the builder. pstack's other subagents (implementer, arena, swarm, per-principle readers) stay out because they cost more tokens than they save.

## Kept

`onigiri-mode` (new, replacing `poteto-mode`), `how`, `why`, `architect`, `unslop`, `create-verification-skill`, `maintain-verification-skill`.

**Verification skills.** `create-verification-skill` and `maintain-verification-skill` were dropped in the trim to a seven-skill pack and restored after 0.3.0 on request. onigiri-mode's "verify against the real thing" states the rule, and these two are how a repo gets a scripted way to follow it, so they don't overlap it. They are zstack's own pre-trim copies of pstack 0.15.36's text, with these changes. Generated skills go to `.pi/skills/`, commands are `/skill:<name>`, and `disable-model-invocation` is stripped. Maintain's per-feature subagent wave is one source pass in the main session that may use **how**, and an ambiguous target is listed rather than asked about. Create now opens a PR for the generated skill, and both open theirs through onigiri-mode's Pull requests playbook. Create opens by citing onigiri-mode's "verify against the real thing". "Agent" became "session", and long dashes, arrows and semicolons became sentences. The feature-map example under `references/` is pstack's, unchanged. Its `control-notes` is a made-up harness for a made-up app, not Cursor's `control-ui`.

In 0.3.0 `onigiri-mode` became the one copy for a pi session and the chat orchestrator, taking the chat copy's hard rule and its launch and VM notes.

**Playbooks.** `diagnose` merges investigation and two forensics playbooks. `measure` merges perf-issue and hillclimb. `resume` merges session-pickup and pause-safely. `change` merges bug-fix, feature, and refactoring, which shared everything after their first step. `pull-requests` merges opening-a-pr, verify-a-pr, babysit, and shipping, for single PRs without stacks. authoring-a-skill is one line in onigiri-mode.

**Folded in.** The three principle leaves (fix root causes, prove it works, test behavior) are lines in onigiri-mode's Principles and Non-negotiables. `why`'s epistemics, writeup, and notes templates are one file. `architect`'s red flags and rationale template are inside its skill. `how`'s explainer template is its output section.

## Vendored verbatim

`statechart`, `create-formal-verification-skill` and `maintain-formal-verification-skill` were added after 0.3.0 on request. They come from pstack 0.15.36 in cursor/plugins at commit `ae5650febf4320793844c12c4adaa937f417f8da`, not the `PINNED_COMMIT` fork base, and are copied byte for byte with `statechart/scripts/`. The statechart's own test suite and corpus (`scripts/test/`, and the `fast-check` dev dependency in its `package.json`) are left upstream at that commit: no zstack CI step or session ran them. Same MIT license and copyright as the rest of pstack.

None of the zstack changes above apply to them. They keep `disable-model-invocation: true`, so pi leaves them out of the model's skill list and they run only as `/skill:statechart`, `/skill:create-formal-verification-skill` and `/skill:maintain-formal-verification-skill`. Their text still says `/statechart` and other bare slash commands, and create still calls statechart "the pstack skill". The test workflow skips the bold-name check in these three directories, because the copied text uses lowercase bold as emphasis, such as maintain's outcome labels. Links such as `../statechart/SKILL.md` are still checked. To update them, copy the three directories again from a newer cursor/plugins commit and change the sha here.

## Dropped

| Dropped | Why |
| --- | --- |
| `poteto-help`, `arena`, `swarm`, `interrogate`, `reflect`, `orchestrate`, `autopilot-*`, `teach`, `no-comments`, `ketchup*`, `recall`, `in-your-own-words`, `bro`, `setup-pstack`, `make-bot-ui` | They spawn or supervise agents, or serve a chat assistant rather than a coding session. |
| `sharpen`, `correct`, `benchmark-checklist`, 21 principle leaves | No reader. Their rules are lines in onigiri-mode. |
| `automate-me` | Nothing called it. onigiri-mode is written by hand. |
| `blast-radius` | The verifier covers it. Its one key idea, finding the fact a change is safe because of, is in the verifier's prompt. |
| `tdd`, `technical-writing`, `typescript-best-practices` | `tdd` duplicated a personal skill. `technical-writing` overlapped `unslop` and opening-a-pr's description rules. The TypeScript rules are onigiri-mode guards. |
| `experiment`, `visual-parity`, `multi-phase-plan`, `worktree-cleanup` playbooks | Niche, or built for a team process. Large work starts from onigiri-mode's no-playbook line. |
| `why`'s vendor source guides, `bugbot-triage` | Each assumed a tool or bot we don't use. |
| `watch-pr`, `check-plan.mjs`, the merge gate | Tooling tied to one deployment or to the coordinator plan shape. |
| `figure-it-out`, `show-me-your-work` (0.3.0) | No pi session ever read either SKILL.md, and no run wrote a `decisions.tsv`. Their two lines that earned a place, a failing done-predicate with riskiest-first units and a per-decision log with evidence pointers, are in onigiri-mode's Run it well. |
