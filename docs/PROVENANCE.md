# zstack provenance

Forked from pstack 0.15.36 at commit `23ab063af02bb9b9c97936b0563879dd6bf21a05`. Every kept skill is rewritten shorter. The upstream text is the source, and none of it is verbatim any more.

## Changed everywhere

- **Model-invocable.** `disable-model-invocation: true` is stripped. In pi it hides a skill from the model instead of reserving it for a slash command.
- **pi, not Cursor.** No `/loop`, cloud agents, `control-ui`, todo tool, Bugbot rubric, or `.cursor/` paths. Sessions are read from `~/.pi/agent/sessions/`, and PRs through `gh`.
- **Repo-agnostic.** Project rules live in each repo's own `AGENTS.md`. onigiri-mode's repo rules are defaults that defer to it.
- **No models in skills.** pstack named a model per role in a rule file. zstack names none. Agent types inherit the main model until pi's config sets one.

## Subagents at four steps

| Role | Type | Step | Why a subagent |
| --- | --- | --- | --- |
| Explorer | `zstack-explorer` | `how` 2, `why` 3 | Reads far more than it returns, so the reading stays out of the main context. |
| Two designs | `zstack-designer` | `architect` B, one-way doors | Sketches that can't see each other don't anchor on the first. |
| Review | `zstack-reviewer` | `pull-requests` Open, `show-me-your-work` | An author can't read its own diff cold. |
| Verify | `zstack-verifier` | `pull-requests` Land 8, own PRs | A verdict from the code's author isn't independent. |

Spawns are fresh, never forked, and can't nest. pstack's other subagents (implementer, arena, swarm, per-principle readers) stay out because they cost more tokens than they save.

## Kept

`onigiri-mode` (new, replacing `poteto-mode`), `how`, `why`, `architect`, `figure-it-out`, `show-me-your-work`, `unslop`.

**Playbooks.** `diagnose` merges investigation and two forensics playbooks. `measure` merges perf-issue and hillclimb. `resume` merges session-pickup and pause-safely. `change` merges bug-fix, feature, and refactoring, which shared everything after their first step. `pull-requests` merges opening-a-pr, verify-a-pr, babysit, and shipping, for single PRs without stacks. authoring-a-skill is one line in onigiri-mode.

**Folded in.** The three principle leaves (fix root causes, prove it works, test behavior) are short sections in onigiri-mode. `why`'s epistemics, writeup, and notes templates are one file. `architect`'s red flags and rationale template are inside its skill. `how`'s explainer template is its output section.

## Dropped

| Dropped | Why |
| --- | --- |
| `poteto-help`, `arena`, `swarm`, `interrogate`, `reflect`, `orchestrate`, `autopilot-*`, `teach`, `statechart`, `no-comments`, `ketchup*`, `recall`, `in-your-own-words`, `bro`, `setup-pstack`, `make-bot-ui`, formal-verification skills | They spawn or supervise agents, or serve a chat assistant rather than a coding session. |
| `sharpen`, `correct`, `benchmark-checklist`, 21 principle leaves | No reader. Their rules are lines in onigiri-mode. |
| `automate-me` | Nothing called it. onigiri-mode is written by hand. |
| `create-` and `maintain-verification-skill` | Generators for project-local verify skills. onigiri-mode's "verify against the real thing" covers the rule. |
| `blast-radius` | The reviewer covers it. Its one key idea, finding the fact a change is safe because of, is in the reviewer's prompt. |
| `tdd`, `technical-writing`, `typescript-best-practices` | `tdd` duplicated a personal skill. `technical-writing` overlapped `unslop` and opening-a-pr's description rules. The TypeScript rules are onigiri-mode guards. |
| `experiment`, `visual-parity`, `multi-phase-plan`, `worktree-cleanup` playbooks | Niche, or built for a team process. Large work goes to `figure-it-out`. |
| `why`'s vendor source guides, `bugbot-triage` | Each assumed a tool or bot we don't use. |
| `watch-pr`, `check-plan.mjs`, the merge gate | Tooling tied to one deployment or to the coordinator plan shape. |
