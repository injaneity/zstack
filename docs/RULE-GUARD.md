# rule-guard extension

`extensions/rule-guard/` turns the onigiri policy's outright bans into code. Before, the policy text was the only thing standing between a session and a ban, and a session either remembered it or broke it. Now a `tool_call` handler blocks the call and returns the reason, so the model sees a refusal it can act on.

## What it blocks

1. **Force-push and skipping the pre-push hook.** `git push` with `-f`, `--force`, `--force-with-lease`, `--force-if-includes` or a `+refspec`, `git push --no-verify`, and `git -c core.hooksPath=... push`. The patent hold lives in the pre-push hook, so a hook skip is a patent-hold bypass.
2. **Merging where a merge job owns merging.** `gh pr merge` (`--auto` included), `gh run rerun` and `gh api .../pulls/N/merge` in any repo listed in `$ONIGIRI_MERGE_OWNED` (comma-separated `owner/name`). The repo comes from `-R`/`--repo`, a PR URL, the `gh api` path, or the working tree's `origin` remote. A command that names no repo, run in a tree whose remote can't be read, is blocked. Both host launchers set the variable to `trycua/cua` unless it is already set. With it unset, nothing is owned and merges run.
3. **Writing the holds file.** A `write` or `edit` of any `hold-prs.txt`, or bash that redirects into it, runs `tee`, `sed -i`, `perl -i`, `rm`, `chmod`, `truncate` on it, or names it as the destination of `cp`, `mv`, `install`, `rsync` or `ln`. Reads, diffs and copies out (`cp hold-prs.txt /tmp/x`, or a `.bak-*` backup) still run.

Commands are split the way `tool-guard` splits them (`find-guard.ts` `commands()`), so `cd x && git push -f` is seen. Wrappers (`env`, `sudo`, `nohup`, `timeout`, `VAR=value`) are skipped. The quoted command of `ssh`, `vm`, `bash -c`, `sh -c` and `eval` is checked too, which covers `~/onigiri-vm/vm "git push -f"`.

The matcher is `rules.ts`, a pure module with no pi imports, tested by `tests/rule-guard.test.ts` (`node --test tests/rule-guard.test.ts`).

## Limits

It reads command text, like `tool-guard`. A command built at run time (`eval "$cmd"`, a script file) is not seen, and neither is a write through Python or another interpreter. The pre-push hook and the merge job's unexpected-merge wake stay the backstops. Children spawned by the `subagent` tool load the same package, so the guard binds them too.
