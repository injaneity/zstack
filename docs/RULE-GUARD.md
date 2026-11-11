# rule-guard extension

`extensions/rule-guard/` turns the outright bans in onigiri-mode's Hard rules into code: a `tool_call` handler blocks the call and returns the reason, so the model sees a refusal it can act on. The matcher is `rules.ts`, a pure module, and `tests/rule-guard.test.ts` is the list of what is and is not blocked, case by case.

## What it blocks

1. **Force-push and skipping the pre-push hook.** `git push` with a force flag or a `+refspec`, `--no-verify`, and a `core.hooksPath` override on a push. The patent hold lives in the pre-push hook, so a hook skip is a patent-hold bypass.
2. **Merging or readying where a merge job owns merging.** `gh pr merge`, `gh pr ready`, `gh run rerun`, and the `gh api` and GraphQL calls that do the same, in `trycua/cua` (`MERGE_OWNED` in `rules.ts`). The repo comes from `-R`, a URL, the `gh api` path, `GH_REPO`, or the `origin` remote of every directory the command might run in. A repo that can't be told is blocked, so name it with `-R` to merge elsewhere from an owned checkout. When the brief has a line exactly `merge: allowed`, `bin/pi-run` sets `ONIGIRI_READY_ALLOWED=1` and `gh pr ready` runs; merges and reruns stay blocked. The guard reads the flag once at load and removes it from the environment, and refuses a bash command that names it other than as a bare `$ONIGIRI_READY_ALLOWED` read. Edit files that mention it with the `edit` tool.
3. **Writing the holds file.** A `write` or `edit` of any `hold-prs.txt`, and bash that redirects into it, edits it in place, removes or moves it, or copies over it. Reads, diffs and copies out still run.

Commands are split into simple commands and stripped of wrappers (`env`, `sudo`, `timeout`, `xargs`, ...) by `shell.ts`. The quoted command of `ssh`, `vm`, `bash -c`, `sh -c` and `eval` is checked too. A here-document body is data unless a shell reads it.

## Limits

It reads command text. A command built at run time (`eval "$cmd"`, a script file) is not seen, and neither is a write through Python or another interpreter. The pre-push hook and the merge job's unexpected-merge wake stay the backstops. Children spawned by the `subagent` tool load the same package, so the guard binds them too.
