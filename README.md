# zstack

A stack for coding agents that works the same under any harness. One thread owns a task and writes all its code. Three delegates help it and never edit source: an **`explorer`** reads, two competing **`designer`**s plan deep or new work, and a **`verifier`** owns verification. The role-to-model map is the machine file's `[models]`.

Derived from [pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan. `docs/PROVENANCE.md` records what changed and why.

## the three parts

- **The stack** is what every coding thread loads: `skills/`, `agents/`, the Hard rules, and the guard that enforces them. It names no repo. It reaches the rest only through `bin/policy`.
- **The queue** is one program per machine, `bin/queue`, with its steps model-checked in `model/Queue.tla`. Work enters two ways: the pickup reads the repo for issues assigned to us and reviews requested of us, and the orchestrator above the machines adds a task by hand. Each machine takes the labels its config names, and `bin/policy check` refuses two machines whose labels overlap. The highest priority goes first.
- **The dispatcher** is one scheduled thread per machine (`dispatch/playbook.md`). It picks up new work, launches what the queue prints, and frees the slot of a thread that died. It decides nothing. A coding thread has the verifier check its own PR and waits for it inside its turn, and the verifier records the verdict with `bin/queue verified`.
- **Merging** is the thread's, through a gate. The verifier passes a head as `auto` or `review`. The thread then runs `gh pr merge <pr> --squash --match-head-commit <head>`, and the guard asks `bin/queue gate` before it lets that run: the head must be the one the verifier passed as `auto` (or the item preapproved), `merge.gate_check` green on it, no hold row, and not a draft. A `review` PR waits for the owner, and a new push shuts the gate until the verifier passes the new head.
- **A turn end** frees the thread's slot. A hook runs `bin/queue release` when a thread's turn ends (Claude Code's `Stop`, pi's `agent_end`). Under Claude Code the hook keeps the turn going, up to twice, while the thread's open PR has no verdict for its current head, so a thread does not finish before its verifier has; pi cannot hold a turn open, so there the brief asks for it. Then the queue tells the orchestrator the outcome through the wake: merged, a PR that waits for the owner and why, or nothing to merge. The queue keeps three states for an item: queued, running, released.
- **The services** are not threads: the patent pre-push hook (`services/pre-push`), the wake (`services/wake.py`), and `services/ensure-main`, which the dispatcher runs first in every pass to bring the machine's checkout to main.

## install

As a Claude Code plugin, which T3 Code threads load:

```bash
claude plugin marketplace add /path/to/zstack
claude plugin install zstack@zstack
```

The plugin is read from that checkout, so a pull updates it.

As a pi package, which the delegates run under:

```bash
pi install git:github.com/injaneity/zstack
```

Both need `node`, `git`, `gh` and `python3` 3.9 or newer on `PATH` (`bin/policy` uses `tomllib`, else the vendored `vendor/tomli`).

## configure

Config has three layers, and `bin/policy` is the one reader of all of them (`check`, `get <key>`, `dump`, `rules`, `rules --thread`).

- **The stack** ships in this package: `config/rules.toml` (the Hard rules) and `config/state.toml` (the state files the services keep).
- **A machine** is `machines/<hostname>.toml`: the models its roles run on, its thread cap, where it sends a wake, and each repo it works on with its clone, its state folder and the labels this machine picks up. `ZSTACK_MACHINE` names another file; `machines/default.toml` is the fallback.
- **A repo** is `repos/<owner>/<name>/policy.toml`: its required checks, the labels that force a review or preapprove a merge, who "we" are, the patent hold's words, and whether an outside PR is salvaged or sent back. An optional `rules.toml` beside it adds sections to the Hard rules.

`config/` ships an example of the last two: a machine working on `acme/app`. Keep your own in a private repo with the same `machines/` and `repos/` folders, clone it on each machine, and point `ZSTACK_CONFIG` at it. A missing or invalid one stops every reader. The repo in play is `ZSTACK_REPO`, else the `origin` of the directory a command runs in, else the machine's only repo. `bin/queue` reads and merges PRs only in `merge.repo`; a PR to this package never goes through it, and the owner merges it.

A rule names a value by its key in braces (`{merge.repo}`) and `bin/policy` fills it in from the machine and repo in play. A rule marked `for = "orchestrator"` is left out of a coding thread's copy.

## set up a machine

1. Write your config (above) and set `ZSTACK_CONFIG` where every thread sees it: under `env` in `~/.claude/settings.json`, and in the shell profile. Run `bin/policy check`.
2. Check that `merge.gate_check` names the CI check that must be green before a merge. A thread merges with the machine's own `gh` login, so that login needs push access.
3. Link the pre-push hook into the clone: `ln -s <zstack>/services/pre-push <clone>/.git/hooks/pre-push`.
4. Create `<root>/hold-prs.txt` (it may hold only a comment). The queue starts nothing while it is unreadable.
5. Schedule one thread in the zstack project, every few minutes, on the model `models.orchestrator` names, with a fresh session each run and this prompt: `You are this machine's zstack dispatcher. Read <zstack>/dispatch/playbook.md and do one pass exactly as it says.`

## what a coding thread gets

- **`onigiri-mode`**: the one skill it reads. The hard rule (one thread, one task), the working choices this stack makes, the delegates and when to use each, autonomy, and the pull-requests playbook.
- **`unslop`**: strips AI tells from prose.
- **`create-verification-skill`** and **`maintain-verification-skill`**: generate a repo's own `verify-<app>` skill that launches the real app, drives it like a user and captures evidence, then keep its feature map honest.
- **`statechart`**, **`create-formal-verification-skill`** and **`maintain-formal-verification-skill`**: pstack's own text, changed only to use the bundled checker. statechart model-checks a TLA+ spec and draws it as a Mermaid statechart.
- **`bin/everystate`**: a TLA+ model checker with TLC's command line and output and no Java, built on first use from a fork pinned to one commit. statechart and `tests/queue-model.sh` both check with it, and CI runs TLC beside it so the two must agree. All three are slash-command only.
- **The rule guard** (`extensions/rule-guard/`, with a pi adapter beside it and a Claude Code adapter in `hooks/`): blocks the calls the Hard rules ban outright. `docs/RULE-GUARD.md` lists them.
- **The pi extensions** `subagent` (`docs/SUBAGENT.md`) and `tool-guard`, which bounds a bash call that declares no timeout by `run.idle_min`.

## license

MIT, retained from pstack. Copyright (c) 2026 Lauren Tan for everything derived from it, including the three skills copied verbatim from pstack. Fork changes are under the same license. `extensions/subagent/` is from pi, MIT, Copyright (c) 2025 Mario Zechner.
