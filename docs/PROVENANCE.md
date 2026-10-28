# zstack provenance

Forked from pstack 0.15.36 at commit `23ab063af02bb9b9c97936b0563879dd6bf21a05`. The upstream plugin cache is the source of every file here that isn't listed as edited.

The one rule that changed everywhere: pstack routes design to one model and implementation to a subagent, then spawns reviewers and arena runners. zstack does all of it in one thread and buys review separation by reading the diff cold against the plan.

## Changed for every skill

`disable-model-invocation: true` is stripped from all frontmatter. In Cursor that flag means "only run when the user types the slash command", which suits a stack of skills a person summons. In pi it means "hide from automatic model selection", which would leave the model unable to pick any of them on its own. Skills here must be model-invocable.

## Kept, edited

| Skill | What changed |
|---|---|
| `onigiri-mode` | Written new. It replaces `poteto-mode` as the mode skill: routing table, non-negotiables, principles, autonomy, reply rules, and the cua hard rules. |
| `architect` | Arena removed. Phase B sketches two or three candidates in this session and picks one; the rejected shape's disqualifying fact goes in the rationale's Synthesis decision. `interrogate` replaced by arguing against your own sketch. `references/runner-prompt.md` dropped, since it didn't exist upstream and arena was its only reader. |
| `how` | The subagent-explorer step is gone. You read the code and write the explanation; `references/explanation-template.md` is the shape of your write-up rather than a prompt for anyone. |
| `why` | Full rewrite. It still has the seven evidence categories and the epistemics framework, but you work each category yourself in turn instead of spawning one investigator each. `references/category-notes.md` is the shape of your notes per category and `references/writeup-template.md` the shape of the answer. Sources Consulted is one line per category. |
| `figure-it-out` | Arena, fan-out, and the delegated-work judge are gone. Ordering replaces parallelism, and every unit ends in a check that can fail. |
| `automate-me` | Retargeted at `onigiri-mode` instead of a generic `-mode` skill. The mining step reads transcripts itself rather than fanning out across slices, and `create-skill` references are replaced by the `authoring-a-skill` playbook. |
| `maintain-verification-skill` | The parallel source wave is a serial read pass with notes. "The coordinator" becomes "you". |
| `show-me-your-work` | The different-model review subagent is now a cold reread of your own audit trail. |

## Kept verbatim

`tdd`, `unslop`, `blast-radius`, `technical-writing`, `typescript-best-practices`, `create-verification-skill`, and `principle-fix-root-causes`, `principle-prove-it-works`, `principle-test-behavior-not-implementation`.

## Dropped for a single thread

A session audit over 203 pi transcripts and three days of work, plus a citation sweep across every skill, decided these. Each one either had no reader, no citation, or was a leaf whose whole value is a sentence.

| Skill | Why it went |
| --- | --- |
| `sharpen`, `correct` | Zero citations, zero sessions. `correct` also duplicated what the `refactoring` playbook already does after a correction. |
| `benchmark-checklist` | Cited once, by a principle that also went. Its questions are now the harness step in `measure`. |
| `principle-attack-the-premise` | Zero citations. Rule is a line in `onigiri-mode`. |
| `principle-experience-first` | Zero citations. Rule is a line in `onigiri-mode`. |
| `principle-make-operations-idempotent` | Zero citations. Rule is a line in `onigiri-mode`. |
| All other `principle-*` except the three kept | pstack's shape fans one principle out to each subagent, so each leaf is sized for a reader with no shared context. One thread reading its own mode skill does not need a file per rule. The rules are in `onigiri-mode` under Shape the work, Guards, and Run it well. |

## Playbooks

All live under `skills/onigiri-mode/playbooks/`.

Fourteen files. pstack's twenty were one job split across several names, so four of them merged and one absorbed the rest.

**Merged.** `diagnose` is `investigation` plus `runtime-forensics` plus `trace-forensics`, because all three produce the same deliverable and differ only in where the evidence comes from: the code, a live process, or an artifact someone handed you. `measure` is `perf-issue` plus `hillclimb`, because hillclimb already told you a one-off fix was the perf playbook and the only real difference was whether the loop ran once or until a target. `experiment` is `prototype` plus `eval`, because a design sketch and a blind prompt comparison are both a question you answer with a throwaway. `resume` is `session-pickup` plus `pause-safely`, the two ends of the same handoff, with `autonomous-run`'s stop rule folded into the mode skill instead of keeping a playbook for it.

**Kept with de-subagent edits:** `bug-fix`, `feature`, `refactoring`, `visual-parity`, `authoring-a-skill`, `babysit`, `shipping`, `worktree-cleanup`, `opening-a-pr`.

**Rewritten:** `multi-phase-plan`. It was a 150-line coordinator that spawned one owner per PR and ran a ten-lane swarm on each. It is now a plan one thread runs box by box, with the lane count scaled to what the change touches.

**pi-native.** Nothing in zstack now names a Cursor-only tool. The sweep replaced the `control-ui` and `control-cli` skills with driving the real surface, `/loop` with the plain instruction to keep one session and re-read your dead hypotheses, `/deslop` with the four-item list it stands for, and `create-skill` with matching the neighbouring SKILL.md files. `.cursor/skills/verify-<app>/` became `.pi/skills/verify-<app>/`, which is the directory pi actually scans. `agent-transcripts` reads became pi session reads under `~/.pi/agent/sessions/--<mangled-cwd>--/`, and `automate-me`'s `ReadTranscript` call became the same path. Slash-command hints are `/skill:<name>`, pi's skill command shape.

**Playbooks speak `gh` only.** The VM has no `origin` CLI, so `opening-a-pr`, `babysit`, `shipping` and `worktree-audit.sh` lost their Origin branches and kept the GitHub ones. `shipping` also lost the Cursor cloud-agent verifier, which one thread cannot produce.

**Swarm-shaped reference files rewritten.** `how`'s `explainer-prompt.md` told the reader to synthesize findings from parallel explorer agents; `explorer-prompt.md` existed only for that and is deleted. `why`'s `investigator-prompt.md` and `synthesizer-prompt.md` were prompts for other agents and are now `category-notes.md` and `writeup-template.md`, the shapes of the notes and the answer you write yourself. `feature` no longer says to delegate implementation or write a throughput checkpoint for workers.

**Dead references fixed.** pstack's playbooks point at things that only exist in Cursor: a `control-ui` and `control-cli` skill, `/deslop` and `/no-comments` from `cursor-team-kit`, the `create-skill` built-in, `/loop`, and `agent-transcripts` paths. None of them are in zstack and none exist in pi. In `shipping`, the verifier was a Cursor cloud agent explicitly credited for not having written the code, which cannot happen in one thread; it now asks for evidence a wrong implementation could not produce. `worktree-audit.sh` read `~/.cursor/projects/<slug>/agent-transcripts`, which is empty on the VM, so it now reads pi's `~/.pi/agent/sessions/--<slug>--/`.

## Dropped

Every one of these exists to spawn, supervise, or fan out across agents, or to serve a chat assistant rather than a coding session.

- Skills: `poteto-mode`, `poteto-help`, `arena`, `swarm`, `interrogate`, `reflect`, `orchestrate`, `autopilot-full`, `autopilot-stack`, `teach`, `statechart`, `no-comments`, `ketchup`, `ketchup-video`, `recall`, `in-your-own-words`, `bro`, `setup-pstack`, `make-bot-ui`, `create-formal-verification-skill`, `maintain-formal-verification-skill`.
- `references/source-playbook.md` survives inside `why`; the playbooks `bugbot-triage` moved to `skills/onigiri-mode/references/`.

Tooling: upstream ships 2,436 lines of TypeScript behind `scripts/watch-pr`, plus `check-plan.mjs`, `bootstrap.ts`, and a bun lockfile, all inside `poteto-mode/scripts`. Dropped, because `watch-pr` is a second merge-gate implementation competing with the one that already runs, and `check-plan.mjs` hardcodes the coordinator plan shape removed from `multi-phase-plan` (it demands "Spawn owners", `/loop 1h`, and "status message" markers, so it rejects every plan this fork writes). `babysit` and `shipping` read PR state with `gh` directly.

Kept tooling: `worktree-audit.sh` (134 lines, bash, no dependencies) and `show-me-your-work/scripts/log.sh` (42 lines). Both are things prose can't do reliably.

`deslop` was never pstack's. It is a plain paragraph in the work prompt: remove narrating comments, unsupported guards, dead compatibility paths, and unrelated edits.

## Not yet ruled on

None. Every pstack skill is either kept, kept-edited, or dropped above.
