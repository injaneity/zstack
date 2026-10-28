### Opening a PR

Invoked at the end of every other playbook.

**Worktree.** Work from a git worktree off main. One thread, one worktree, so there's no second writer to reconcile. Dirty branch with unrelated work: patch out, fresh worktree, apply. Snarled worktree: reset from main, redo minimally.

**Commits.** Commit liberally. Rebase into small, ordered commits before opening PRs. Each commit is a future PR: landable, ordered to tell the story. Amend when the fix belongs in a just-made commit. New commit when separable. Commit bodies don't restate the subject.

**PRs.** Deslop the diff before you commit it. Four things to remove: no narrating comments, no guards for states that cannot happen, no dead compatibility paths, and no edit outside the named scope. Write every PR title, description, and commit body with the **technical-writing** skill (every layer except Diátaxis, and apply the STE vocabulary rules: one word per action, keep articles, a plain verb over `-ing` where one works), then apply the **unslop** skill.

**Titles.** Conventional Commits `type(scope): subject`, no trailing period. Type such as `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, or `perf`. Scope is the area, such as `cua` or `zstack`. Subject is short, imperative, and names a real symbol when one carries the change, for example `fix(zstack): retarget opening-a-pr babysit trigger`.

**Descriptions.** The PR body is a one-minute briefing, not a lab notebook, for a reviewer who has the diff. It says why the change exists, what it leaves out, what it could break, and how you proved it works. Write short, simple sentences with few identifiers, no walls of text. The body becomes the squash commit body, so keep that commit under about 40 lines. Use `##` headings, not bold lead-ins, in this order, and drop a section with nothing to say:

- `## Why`: the problem and approach, 1–3 short sentences. No SHAs, rebase genealogy, or "based on main" preamble.
- `## What changed`: 1–3 short bullets. Name a real symbol or path only when it carries the change. Name both sides of a rename or retarget.
- `## Scope`: always. 1–3 short items on what it covers and what it deliberately leaves out (a follow-up, a known gap). No symbols, paths, or file-by-file essay.
- `## Tradeoffs`: only rejected alternatives a reviewer would otherwise ask about. Skip when there was no real choice.
- `## Blast Radius`: 1–2 sentences on who or what it touches and why that is safe or risky. If main is red, state the cost of leaving it red.
- `## Verification`: 1–3 bullets, each a real run path and its outcome. Perf: one primary number with its unit and its before and after values, plus a link to the raw run for the rest. No sample-size methodology, recitals, or metric tables.

After the sections, attach videos or screenshots when they prove a claim. Full SHAs, lane recitals, lever-correction essays, file-by-file checklists, and "CLEAN" verdicts go in a linked artifact, not the body.

**Forge.** Resolve the forge before the first PR operation and keep that choice for create, edit, view, watch, and merge. GitHub CLI (`gh`) is the default. If `command -v origin` succeeds and Origin can resolve the repository, prefer `origin pr ...`. If Origin is absent or cannot resolve the repository, stay on `gh` and record the fallback. Do not require Graphite (`gt`).

**Built-in PR tool.** When the run provides a built-in PR tool, create, edit, retarget, and mark ready through it, never through a forge CLI. A CLI-made PR misses what the tool tracks. Use the resolved forge for everything the tool does not cover, and for every PR operation when the run has no such tool.

**Size and stacks.** Prefer five narrow PRs to one large PR. A stack is a base-branch chain. The root PR targets trunk. Each child branch rebases onto its parent's exact tip and its PR targets the parent branch. As a parent merges, move its child onto trunk per `playbooks/shipping.md` step 4. Without a built-in PR tool, per the resolved forge, create a child with `origin pr create --status open --base <parent-branch>` or `gh pr create --base <parent-branch>`, and retarget one with `origin pr edit <pr> --base <parent-branch>` or `gh pr edit <pr> --base <parent-branch>`. Branch from trunk only for independent work. Rebase on trunk before substantial stack work.

**Readiness.** Open every PR ready, never as a draft. A built-in PR tool can default to draft, so set `draft: false` on every creation call through it. With Origin, pass `--status open`. With `gh`, omit `--draft`. If a PR still opens as a draft, mark it ready through the PR tool, or run `origin pr ready <number>` or `gh pr ready <number>` according to the resolved forge. Run `origin pr view <number>` or `gh pr view <number>` before you refer to PR status.

**Babysit.** Opening a PR does not start a babysit. Post the URL and keep building. Finish the phase or stack first. Run a separate babysit pass only when the user asks for one after the whole stack exists. A babysit for each new PR stalls the build and spends checks on commits later waves restart. Push back when feedback drifts from intent.

Opening the PR is the end of the build. Post the URL, then pick up the babysit loop from `playbooks/babysit.md` if the task asked for CI to be driven to green, and report merge-ready when it is.
