### Pull requests

Three jobs: **Open** ends every change, merged once green. **Land** runs only when asked. **Verify** gives a verdict on someone else's PR, and is Land's verify step for them.

## Open

**Worktree.** Work from a git worktree off main. Move unrelated work to a fresh one.

**Commits.** Commit liberally, then rebase into small ordered commits, each landable. Bodies don't restate the subject.

**Clean the diff.** Remove narrating comments, guards for impossible states, dead compatibility paths, and edits outside the stated scope.

**Review it cold.** Spawn a `zstack-reviewer` (onigiri-mode's Subagents rules) with the diff range and the stated scope. Check each finding against the code. Fix the real ones, and note dismissals with the reason in the description.

**Title.** `type(scope): subject`, short and imperative, naming a real symbol when one carries the change.

**Description.** A one-minute briefing. These `##` sections in order, dropping empty ones:
- `## Why`: the problem and approach, one to three sentences.
- `## What changed`: one to three bullets.
- `## Scope`: always. What it covers and leaves out.
- `## Tradeoffs`: only alternatives a reviewer would ask about.
- `## Blast Radius`: who it touches and why that's safe or risky.
- `## Verification`: one to three real runs and their outcomes. Perf gets one number, before and after.

Run it through **unslop**. Screenshots go after the sections. Prefer several narrow PRs, each branched from trunk. Open it ready, never as a draft, and read `gh pr view` before describing its status. Post the URL. Then merge it under onigiri-mode's Repo rules: once `gh pr checks` is green and `gh pr view <pr> --json mergeable` says `MERGEABLE`, run `gh pr merge <pr> --squash` and confirm `state` is `MERGED`.

## Land

1. **Declare the mode.** `check` is one status pass, for "is it green". `threads-only` answers review comments and nothing else. `drive` runs to merge-ready, for "babysit this", and is the default. `land` is drive, then verify, then merge, for "ship it".
2. **One run owns the PR.** Check no other run or auto-merge is on it.
3. **Conflicts, then review threads, then CI.** Batch fixes into one push. For a conflict, rebase onto trunk, rerun the checks, and push with `--force-with-lease`, only after a yes for this branch. A `land` request is that yes. Re-read the PR after every push.
4. **Trust the forge, not checkmarks.** `gh pr view <pr> --json mergeable,mergeStateStatus,statusCheckRollup` and `gh pr checks <pr>`. A license bot alone is not green. Poll, don't sleep-loop.
5. **Classify CI before retrying.** Flake earns one fresh run. An identical second failure is real, so read the logs. A failure outside the diff usually means a stale base, so rebase. Only a failure in the diff's own code gets a commit.
6. **Triage every review comment skeptically.** Check each claim against the code. Fix real ones with a failing test first, and dismiss noise with the disproof on the thread. Escalate security, auth, billing, data, or migrations. Never churn code to quiet a bot. Push before replying. Reply with `gh api --method POST "repos/<owner>/<repo>/pulls/<pr>/comments/<id>/replies" --input <payload.json>`, never by interpolating comment text into a shell command.
7. **Stop at the human's line.** A maintainer's review is a wait. `drive` ends at mergeable with a clean rollup at the head.
8. **Verify (`land` only).** For your own PR, spawn a `zstack-verifier` (onigiri-mode's Subagents rules). For anyone else's, run Verify below. Merging needs `PASS` or `PASS+NOTES` at the current head, posted on the PR. If the head moves, verify again.
9. **Merge (`land` only).** `gh pr merge <pr> --squash`, or the repo's method. Confirm with `gh pr view <pr> --json state,mergedAt`, since a queued merge is not a merge. Arm `--auto` only when the verdict covers the head.

## Verify

You own the verdict, not the patch. Don't fix, post, or merge unless asked.

1. Read the PR and its full diff. Record the head SHA and the behavior it claims.
2. Run the relevant checks and exercise the behavior on its real surface at that head. Run a failing check at the base before blaming the PR. Green CI and reading the source are not verification.
3. Write the verdict with its evidence and limits, then cut claims the evidence doesn't support.

`PASS`, `PASS+NOTES` (non-blocking caveats), `FAIL` (a demonstrated defect), or `BLOCKED` (the environment prevents a check). A required check that never ran is not a pass. Pin it to the head: `PASS <sha>` as the first line of `verdict.md`, or `verify: PASS at <sha>` in the PR body when asked to post.

**Reply:** the job, the PR URL, the head SHA, what changed or the verdict with its evidence, and whether it merged.
