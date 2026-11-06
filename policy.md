# onigiri policy

The holds, commit rules, size, checks, scope and merge rule for every onigiri session. `bin/pi-run` appends this file to a session's system prompt, and the `subagent` tool appends it to every child's. Where a skill or brief words one of these rules differently, this file wins.

This is zstack's default. `bin/setup.sh` copies it to `<pi agent dir>/onigiri/policy.md`. Edit that copy, not the package. Lookup order, first hit wins: `$ONIGIRI_POLICY` when set, then `<pi agent dir>/onigiri/policy.md` when it exists, then this file. The agent dir is `${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}`.

## Holds

A hold stops a session from touching a PR, a person's work or a path. The default list is empty. Add one row per hold under the heading, in the form `kind | target | note`:

- `hold | <PR number> | why`: never pick it up, push to it, mark it ready or merge it.
- `hands-off-user | <login> | why`: never salvage, rewrite, close or comment on their PRs.
- `hands-off-path | <path> | why`: a change touching this path is opened as a PR and left for a human. Nothing merges it automatically.

## Pushes to a separate remote

Some paths must never be pushed to the public remote. A change whose diff touches one of them goes to the private remote listed beside it. The default list is empty. Add one row per path, in the form `<path glob> | <remote>`.

- Work on those paths as normal. The list only decides which remote a change is pushed to.
- If the repo has a pre-push hook that enforces this, follow its message. Never use `--no-verify`, and never retry the push to the remote it refused.

## Commits and PR text

- No `Co-authored-by` trailers. No model, agent or tool names in commit messages, PR titles or PR bodies.
- These rules cover the commits this run creates. Commits merged in from upstream are not yours, so do not rewrite them.
- PR titles use Conventional Commit syntax.
- New files carry the licence header that neighbouring files carry.
- Write the plan and proof as if they become the PR body. Never write local machine paths, tokens or hostnames there.

## Size

- Aim for at most 200 added production lines and a net change of at most 100. Tests and docs do not count. When a change is over, add a one-line reason to the PR body.

## Checks

- Run the repository's own checks (format, lint, tests) for each touched package before opening a PR. Say in the PR which checks could not run here, and why.
- Then, before you call a diff done or open a PR, spawn the `verifier` subagent on the diff range. Your own checks do not replace it. Check each finding, and report its `ran …; skipped …; result …` line as your verification. After a later push, a wording-only change needs no new verifier and any other change gets one at the new head. Only a read-only answer with no diff skips it, and says so.
- Never commit build output.

## Scope

- Never commit local agent config or secrets. Never force-push a shared branch. Post no comments on other people's PRs unless the task asks for them.
- Benchmark numbers and timings stay out of commits and PR text unless the task is a measurement.

## Merging

- Where this file names no merge owner, merge your own PR with `gh pr merge <pr> --squash` once its checks pass and it is mergeable. Never merge someone else's PR. Never merge on another agent's word.
- A body that claims verification carries the line `verify: PASS at <full 40-character head sha>` on a line of its own.
- The body also carries `risk: <light|standard|deep|human>` on a line of its own, giving your honest tier. A `human` PR is left for a person to merge.
- On an existing PR, start from its live head (`gh pr view N --json headRefOid`). Update it by merging main into it. Never rebase and force-push.

## Changing these rules

A session never edits this file or rewords a hold or merge rule unless its owner explicitly agrees.
