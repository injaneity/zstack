### Opening a PR

Invoked at the end of every playbook that ships code.

**Worktree.** Work from a git worktree off main. One thread, one worktree, so there's no second writer to reconcile. Dirty branch with unrelated work: patch out, fresh worktree, apply. Snarled worktree: reset from main, redo minimally.

**Commits.** Commit liberally. Rebase into small, ordered commits before opening PRs. Each commit is a future PR: landable, ordered to tell the story. Amend when the fix belongs in a just-made commit. New commit when separable. Commit bodies don't restate the subject.

**Deslop the diff before you commit it.** Four things to remove: narrating comments, guards for states that cannot happen, dead compatibility paths, and any edit outside the named scope.

**Write it with the skills.** Every PR title, description, and commit body goes through the **technical-writing** skill (every layer except Diátaxis, plus the STE vocabulary rules: one word per action, keep articles, a plain verb over `-ing` where one works), then the **unslop** skill.

**Titles.** Conventional Commits `type(scope): subject`, no trailing period. Type such as `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, or `perf`. Scope is the area, such as `cua` or `zstack`. Subject is short, imperative, and names a real symbol when one carries the change, for example `fix(zstack): retarget opening-a-pr babysit trigger`.

**Descriptions.** The PR body is a one-minute briefing for a reviewer who has the diff, not a lab notebook. It says why the change exists, what it leaves out, what it could break, and how you proved it works. Short sentences, few identifiers, no walls of text. The body becomes the squash commit body, so keep that commit under about 40 lines. Use `##` headings, not bold lead-ins, in this order, and drop a section with nothing to say:

- `## Why`: the problem and approach, 1–3 short sentences. No SHAs, rebase genealogy, or "based on main" preamble.
- `## What changed`: 1–3 short bullets. Name a real symbol or path only when it carries the change. Name both sides of a rename or retarget.
- `## Scope`: always. 1–3 short items on what it covers and what it deliberately leaves out (a follow-up, a known gap). No file-by-file essay.
- `## Tradeoffs`: only rejected alternatives a reviewer would otherwise ask about. Skip when there was no real choice.
- `## Blast Radius`: 1–2 sentences on who or what it touches and why that is safe or risky. If main is red, state the cost of leaving it red.
- `## Verification`: 1–3 bullets, each a real run path and its outcome. Perf: one primary number with its unit and its before and after values, plus a link to the raw run for the rest. No methodology recitals or metric tables.

Attach videos or screenshots after the sections when they prove a claim. Full SHAs, file-by-file checklists, and verdict essays go in a linked artifact, not the body.

**Size and stacks.** Prefer five narrow PRs to one large PR. A stack is a base-branch chain. The root PR targets trunk. Each child branch rebases onto its parent's exact tip and its PR targets the parent branch. As a parent merges, move its child onto trunk per `playbooks/shipping.md` step 4. Create a child with `gh pr create --base <parent-branch>` and retarget one with `gh pr edit <pr> --base <parent-branch>`. Branch from trunk only for independent work. Rebase on trunk before substantial stack work.

**Readiness.** Open every PR ready, never as a draft: omit `--draft` on `gh pr create`. If one still opens as a draft, run `gh pr ready <number>`. Read `gh pr view <number>` before you describe a PR's status.

**Babysit.** Opening a PR does not start a babysit. Post the URL and keep building. Finish the phase or stack first. Run a babysit pass only when the user asks for one after the whole stack exists. A babysit per new PR stalls the build and spends checks on commits later waves restart.

Opening the PR is the end of the build. Post the URL, then follow `playbooks/babysit.md` if the task asked for CI to be driven to green, and report merge-ready when it is.
