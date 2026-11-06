# tool-guard extension

`extensions/tool-guard/` stops two ways a session burns wall clock without doing work. One research session spent over an hour on two `find` runs looking for `cmake` (`find / -name cmake ...` and `find /tmp /Users/<user> ...`) and minutes more on `sleep N; tail <log>` loops.

## What it does

1. **Refuses unscoped `find`.** A `tool_call` handler blocks a bash command when any of its simple commands runs `find` over `/`, a top-level directory (`/usr`, `/tmp`, `/opt`, ...), `/usr/local`, `/opt/homebrew`, `/home`, `/Users`, or a home directory (`~`, `$HOME`, `/home/<user>`, `/Users/<user>`), unless it passes `-maxdepth 2` or less. Wrappers (`sudo`, `timeout`, `env`, `xargs`, ...) and a full path to `find` are seen through. The refusal tells the model to use `command -v`, `which -a`, `brew --prefix`, a known install directory, or a scoped `find`. A `find` inside the project or any deeper directory runs as before.
2. **Adds three rules to the system prompt** of every session that loads the package, subagent children included, on `before_agent_start`: resolve tools without searching the disk, run long builds in the background and wait on them once instead of `sleep`-and-`tail` polling, and measure with a fixed, time-bounded matrix.

The matcher is `find-guard.ts`, a pure function with no pi imports, tested by `tests/find-guard.test.ts` (`node --test tests/find-guard.test.ts`).

## Limits

It reads the command text. A `find` built at run time (`eval "$cmd"`, a script file, `bash -c "$x"`) is not seen. The `find` tool pi ships (glob search, `.gitignore`-aware) is not touched. `sleep` polling is a prompt rule, not a block, because a short `sleep` has honest uses.
