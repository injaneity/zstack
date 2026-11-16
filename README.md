# zstack

A stack for coding agents that works the same under any harness. One thread owns a task and writes all its code. Three delegates help it and never edit source: an **`explorer`** reads, two competing **`designer`**s plan deep or new work, and a **`verifier`** owns verification. The role-to-model map is the machine file's `[models]`.

Derived from [pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan. `docs/PROVENANCE.md` records what changed and why.

## the three parts

- **The stack** is what every coding thread loads: `skills/`, `agents/`, the Hard rules, and the guard that enforces them. It names no repo. It reaches the rest only through `bin/policy`.
- **The orchestrator** is one scheduled thread per machine (`orchestrator/playbook.md`). `orchestrator/plan` decides in code what is ready to work on and keeps the queue, and `model/Plan.tla` model-checks its steps; the thread launches exactly that, reads the reports and relays them. A messaging service can talk to it; the service decides nothing.
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

## configure

Config has three layers, and `bin/policy` is the one reader of all of them (`check`, `get <key>`, `dump`, `rules`, `rules --thread`).

- **The stack** ships in this package: `config/rules.toml` (the Hard rules) and `config/state.toml` (the state files the services keep).
- **A machine** is `machines/<hostname>.toml`: the models its roles run on, its limits, where it sends a wake, and each repo it works on with that repo's clone and state root. `ZSTACK_MACHINE` names another file; `machines/default.toml` is the fallback.
- **A repo** is `repos/<owner>/<name>/policy.toml`: its merge gate and risk tiers, who "we" are, the patent hold's words, and whether an outside PR is salvaged or sent back. An optional `rules.toml` beside it adds sections to the Hard rules.

`config/` ships an example of the last two: a machine working on `acme/app`. Keep your own in a private repo with the same `machines/` and `repos/` folders, clone it on each machine, and point `ZSTACK_CONFIG` at it. A missing or invalid one stops every reader. The repo in play is `ZSTACK_REPO`, else the `origin` of the directory a command runs in, else the machine's only repo.

A rule names a value by its key in braces (`{merge.repo}`) and `bin/policy` fills it in from the machine and repo in play. A rule marked `for = "orchestrator"` is left out of a coding thread's copy.

## what a coding thread gets

- **`onigiri-mode`**: the one skill it reads. The hard rule (one thread, one task), the working choices this stack makes, the delegates and when to use each, autonomy, and the pull-requests playbook.
- **`unslop`**: strips AI tells from prose.
- **`create-verification-skill`** and **`maintain-verification-skill`**: generate a repo's own `verify-<app>` skill that launches the real app, drives it like a user and captures evidence, then keep its feature map honest.
- **`statechart`**, **`create-formal-verification-skill`** and **`maintain-formal-verification-skill`**: pstack's own text, unchanged. statechart model-checks a TLA+ spec with TLC and draws it as a Mermaid statechart. All three are slash-command only.
- **The rule guard** (`extensions/rule-guard/`, with a pi adapter beside it and a Claude Code adapter in `hooks/`): blocks the calls the Hard rules ban outright. `docs/RULE-GUARD.md` lists them.
- **The pi extensions** `subagent` (`docs/SUBAGENT.md`) and `tool-guard`, which bounds a bash call that declares no timeout by `run.idle_min`.

## license

MIT, retained from pstack. Copyright (c) 2026 Lauren Tan for everything derived from it, including the three skills copied verbatim from pstack. Fork changes are under the same license. `extensions/subagent/` is from pi, MIT, Copyright (c) 2025 Mario Zechner.
