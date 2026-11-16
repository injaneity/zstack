# zstack

A stack for coding agents that works the same under any harness. One thread owns a task and writes all its code. Three delegates help it and never edit source: an **`explorer`** reads, two competing **`designer`**s plan deep or new work, and a **`verifier`** owns verification. The role-to-model map is `config/policy.toml` `[models]`.

Derived from [pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan. `docs/PROVENANCE.md` records what changed and why.

## the three parts

- **The stack** is what every coding thread loads: `skills/`, `agents/`, the Hard rules, and the guard that enforces them. It names no repo. It reaches the rest only through `bin/policy`.
- **The orchestrator** is one scheduled thread per machine (`orchestrator/playbook.md`). It plans the work, launches coding threads with `orchestrator/brief.md`, reads their reports and relays them. A messaging service can talk to it; the service decides nothing.
- **The services** run on a machine beside the threads and are not threads: the merge job (`services/merge_job.py`), the patent pre-push hook (`services/pre-push`), the wake (`services/wake.py`), and the two checks that keep a machine's checkout at main (`services/ensure-main`, `services/check-parity`).

## install

As a Claude Code plugin, which T3 Code threads load:

```bash
claude --plugin-dir /path/to/zstack
```

As a pi package, which the delegates run under:

```bash
pi install git:github.com/injaneity/zstack
```

Both need `node`, `git`, `gh` and `python3` 3.9 or newer on `PATH` (`bin/policy` uses `tomllib`, else the vendored `vendor/tomli`).

## configure a machine

`config/` ships an example deployment: a repo `acme/app`, an owner `Robin`, a login `octo`. Copy the directory, write your own values, and point `ZSTACK_CONFIG` at the copy. That directory replaces the shipped one whole, and a missing or invalid one stops every reader.

- `policy.toml` holds the values the code reads: models, the merge gate and its repo, the patent hold's words and private repo, this machine's clone and holds file, the wake's URL.
- `rules.toml` holds the Hard rules. A rule names a value by its key in braces (`{merge.repo}`) and `bin/policy` fills it in. A rule marked `for = "orchestrator"` is left out of a coding thread's copy.
- `state.toml` declares each runtime state file the services keep.

`bin/policy` is the one reader of all three: `check`, `get <key>`, `dump`, `rules`, `rules --thread`.

## what a coding thread gets

- **`onigiri-mode`**: the one skill it reads. The hard rule (one thread, one task), the working choices this stack makes, the delegates and when to use each, autonomy, and the pull-requests playbook.
- **`unslop`**: strips AI tells from prose.
- **`create-verification-skill`** and **`maintain-verification-skill`**: generate a repo's own `verify-<app>` skill that launches the real app, drives it like a user and captures evidence, then keep its feature map honest.
- **`statechart`**, **`create-formal-verification-skill`** and **`maintain-formal-verification-skill`**: pstack's own text, unchanged. statechart model-checks a TLA+ spec with TLC and draws it as a Mermaid statechart. All three are slash-command only.
- **The rule guard** (`extensions/rule-guard/`, with a pi adapter beside it and a Claude Code adapter in `hooks/`): blocks the calls the Hard rules ban outright. `docs/RULE-GUARD.md` lists them.
- **The pi extensions** `subagent` (`docs/SUBAGENT.md`) and `tool-guard`, which bounds a bash call that declares no timeout by `run.idle_min`.

## license

MIT, retained from pstack. Copyright (c) 2026 Lauren Tan for everything derived from it, including the three skills copied verbatim from pstack. Fork changes are under the same license. `extensions/subagent/` is from pi, MIT, Copyright (c) 2025 Mario Zechner.
