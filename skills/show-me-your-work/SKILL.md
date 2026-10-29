---
name: show-me-your-work
description: "Keep a reviewable decision trail for long-running or unattended work: a TSV log with one row per decision (what, why, evidence, result). Local by default; commit it when a reviewer needs the trail to trust the result. Use for /skill:show-me-your-work, autonomous or multi-phase runs, or work a human reviews after stepping away."
---

# Show me your work

Keep one append-only TSV log, one row per decision.

```
ts	phase	decision	why	evidence	result
2026-05-24T09:40:00Z	harness	screenshotted the old version before changing anything	to compare old against new	scripts/snapshot.sh	120 baselines saved
2026-05-24T12:30:00Z	widget	threw out a change whose screenshots were blank	checked the files instead of trusting the summary	worktree reset	reverted
```

- **ts** is ISO 8601. **evidence** is a pointer, never prose: a SHA, PR, `file:line`, or artifact path. **result** is the outcome: `tests green`, `reverted`, `INCONCLUSIVE`, `open`.
- Keep cells on one line. Prefix a cell starting with `=`, `+`, `-`, or `@` with a single quote, since spreadsheets execute them.
- Write rows the way you'd tell a teammate, in plain words.
- Log decision points, not every action: a fork chosen, a unit verified, a revert and its trigger, a blocker. In a loop, one row per attempt.
- Never edit or delete a row. A wrong call gets a new row that supersedes it.
- When a run adds to an existing log, its first row has phase `start` and names the rows it didn't write.

Keep it at `decisions.tsv` in the work dir, out of git. Commit it only when a reviewer needs the trail to trust the result.

## Audit before handing back

Read this run's own pi session under `~/.pi/agent/sessions/--<mangled-cwd>--/`, and no other project's. Check every row maps to a real decision, every pointer resolves and shows what the row claims, and every pivot that shaped the work is logged. Fix gaps with new rows, never by editing.

Then get the trail read cold. Spawn a `zstack-reviewer` (onigiri-mode's Subagents rules) with the log path and the run's goal. For a short run, read the log yourself instead of your memory of it. Look for decisions with thin evidence, verification claimed without proof, choices that papered over a symptom, and gaps a skim would miss.

End the reply with an **Attention** section listing each flag with its row, or "No flags".
