### Pull requests

Three jobs: **Open** ends every change, and the merge rule in onigiri-mode's Hard rules decides who merges it. **Land** runs only when asked. **Verify** gives a verdict on someone else's PR (a review), and is Land's verify step for them. A review launch runs as role `coding`, and the `verifier` does all of its verification.

## Open

**Worktree.** Work from a git worktree off main. Move unrelated work to a fresh one.

**Commits.** Commit liberally, then rebase into small ordered commits, each landable. Bodies don't restate the subject.

**Clean the diff.** Remove narrating comments, guards for impossible states, dead compatibility paths, and edits outside the stated scope.

**Verify it cold.** Spawn a `verifier` (onigiri-mode's Subagents rules) with the diff range, the stated scope and the worktree. Check each finding against the code. A check result you cite comes from the verifier's run at the current head. Fix the real ones, and note dismissals with the reason in the description.

**Title.** `type(scope): subject`, short and imperative, naming a real symbol when one carries the change.

**Description.** A one-minute briefing. These `##` sections in order, dropping empty ones:
- `## Why`: the problem and approach, one to three sentences.
- `## What changed`: one to three bullets.
- `## Scope`: always. What it covers and leaves out.
- `## Tradeoffs`: only alternatives a reviewer would ask about.
- `## Blast Radius`: who it touches and why that's safe or risky.
- `## Verification`: one to three real runs and their outcomes. Perf gets one number, before and after.

Run it through **unslop**. Screenshots go after the sections. Prefer several narrow PRs, each branched from trunk. Open it ready, unless the brief says to propose it first or not to merge yet: then open it as a draft and leave it one. Read `gh pr view` before describing its status. Post the URL. Then follow Hard rules' merge rule. Where it lets you merge, wait for `gh pr checks` to be green and `gh pr view <pr> --json mergeable` to say `MERGEABLE`, then confirm `state` is `MERGED`.

## Land

1. **Declare the mode.** `check` is one status pass, for "is it green". `threads-only` answers review comments and nothing else. `drive` runs to merge-ready, for "babysit this", and is the default. `land` is drive, then verify, then merge, for "ship it".
2. **One run owns the PR.** Check no other run or auto-merge is on it.
3. **Conflicts, then review threads, then CI.** Batch fixes into one push. For a conflict, update the branch the way Hard rules' Merging section says and have the verifier rerun the checks. Re-read the PR after every push.
4. **Trust the forge, not checkmarks.** `gh pr view <pr> --json mergeable,mergeStateStatus,statusCheckRollup` and `gh pr checks <pr>`. A license bot alone is not green. Poll, don't sleep-loop.
5. **Classify CI before retrying.** Flake earns one fresh run. An identical second failure is real, so read the logs. A failure outside the diff usually means a stale base, so update from trunk. Only a failure in the diff's own code gets a commit.
6. **Triage every review comment skeptically.** Check each claim against the code. Fix real ones with a failing test first, and dismiss noise with the disproof on the thread. Escalate security, auth, billing, data, or migrations. Never churn code to quiet a bot. Push before replying. Reply with `gh api --method POST "repos/<owner>/<repo>/pulls/<pr>/comments/<id>/replies" --input <payload.json>`, never by interpolating comment text into a shell command.
7. **Stop at the human's line.** A maintainer's review is a wait. `drive` ends at mergeable with a clean rollup at the head.
8. **Verify (`land` only).** Run Verify below, for your own PR or anyone else's. Merging needs `PASS` or `PASS+NOTES` at the current head, posted on the PR. If the head moves, verify again.
9. **Merge (`land` only).** Verification and the `risk:` line must be recorded for the current head, as Hard rules' Merging section says. Then `gh pr merge <pr> --squash`, or the repo's method, unless Hard rules say someone else merges. Where it does, do not merge and do not arm `--auto` either. Confirm with `gh pr view <pr> --json state,mergedAt`, since a queued merge is not a merge. Elsewhere, arm `--auto` only when the verdict covers the head.

## Verify

You own the verdict, not the patch. Don't fix, post, or merge unless asked.

1. Read the PR and its full diff. Record the head SHA and the behavior it claims.
2. Spawn the `verifier` on the PR's diff at that head, with the behavior it claims as the test plan. It runs the checks and exercises the behavior on its real surface, and runs a failing check at the base before blaming the PR. Green CI and reading the source are not verification.
3. Write the verdict with its evidence and limits, then cut claims the evidence doesn't support.

`PASS`, `PASS+NOTES` (non-blocking caveats), `FAIL` (a demonstrated defect), or `BLOCKED` (the environment prevents a check). A required check that never ran is not a pass. Pin it to the full 40-character head sha and record it, naming what ran and what was skipped, as Hard rules' Merging section says. A local `verdict.md` is not a record.

**Reply:** the job, the PR URL, the head SHA, what changed or the verdict with its evidence, and whether it merged.
