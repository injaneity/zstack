# zstack

A stack for coding agents that works the same under any harness. One thread owns a task and writes all its code. Three delegates help it and never edit source: an **`explorer`** reads, two competing **`designer`**s plan deep or new work, and a **`verifier`** owns verification. The role-to-model map is the machine file's `[models]`.

Derived from [pstack](https://github.com/cursor/plugins/tree/main/pstack) by Lauren Tan. `PROVENANCE.md` records what changed and why.

## how it fits together

- **The stack** is what every coding thread loads: `skills/`, `agents/`, the invariants, and the guard that enforces them. It names no repo. It reads the config only through `queue.py`.
- **The invariants** are the few things that always hold: nothing merges unverified, nothing held is started or merged, a turn does not end with unverified work, one item has one thread, a thread is started once, a thread cannot change its own controls, and nothing is published that breaks the repo's own invariants. The stack's are `config/invariants.toml`; each repo adds its own beside its `policy.toml`. Every one names where it is enforced and what proves it: the five about the queue's state are properties of `model/Queue.tla`, each with a mutant that breaks it, and the rest are tests. What nothing can check is judgment, and goes in each item's brief, not in every session.
- **The queue** is one program per machine, `queue.py`. It keeps almost nothing: which threads are running, which finished threads wait on an open PR, the tasks the orchestrator added by hand, the verifier's verdict per PR, and the ids already handled. Everything else it reads from the repo when it matters: what is assigned to us, what is held, whether a PR is open, a draft or green. Each machine takes the labels its config names, and `queue.py check` refuses two machines whose labels overlap. The highest priority goes first. Its steps are model-checked in `model/Queue.tla`.
- **The pass** is `queue.py pass`, run on a schedule on each machine. It clears what has finished, frees the slot of a thread that died, and starts a thread for each ready item up to the cap. No model takes part: it starts and reads threads through the T3 Code command `run.t3` names. It records an item as running before it starts the thread, so a start whose answer was lost is never started twice: a later pass finds that thread by its title, and launches the item again only when T3 Code has listed none for `run.lost_min` minutes.
- **Merging** is the thread's, through a gate. The verifier passes a head as `auto` or `review` and records that with `queue.py verified`. The thread then runs `gh pr merge <pr> --squash --match-head-commit <head>`, and the guard asks `queue.py gate` before it lets that run. A `review` head waits for the owner, and a new push shuts the gate until the verifier passes the new head.
- **A turn end** frees the thread's slot. A hook runs `queue.py release` when a thread's turn ends (Claude Code's `Stop`, pi's `agent_end`), when a turn dies on an API error (`StopFailure`), and when its session ends (`SessionEnd`, pi's `session_shutdown`). No hook fires when a person interrupts a turn, so the pass frees that slot. Under Claude Code the hook keeps the turn going while the thread's open PR has no verdict for its current head; pi cannot hold a turn open, so there the brief asks for it. Then the queue tells the orchestrator the outcome through the wake, which carries the thread's id, so the orchestrator can read its report. A finished item is forgotten, and its worktree goes with it. One whose PR is still open waits on it and keeps its worktree, with only build output removed, until the PR merges or closes. The guard also refuses a delegate that is not awaited inside the turn (`mode: "wait"`), so a turn end means the thread is done.
- **Three more jobs** are `queue.py`'s too: the clone's pre-push hook (`hooks/pre-push`), which enforces the repo's publishing invariants; the wake it sends the orchestrator; and `queue.py update`, which the schedule runs before every pass to bring the machine's checkout to main.

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

Both need `node`, `git`, `gh` and `python3` 3.9 or newer on `PATH` (`queue.py` uses `tomllib`, else the vendored `vendor/tomli`).

## configure

Config has three layers, and `queue.py` is the one reader of all of them (`check`, `get <key>`, `dump`, `invariants`). Run it with no argument for every command.

- **The stack** ships in this package: `config/invariants.toml`.
- **A machine** is `machines/<hostname>.toml`: the models its roles run on, its thread cap, where it sends a wake, and each repo it works on with its clone, its state folder and the labels this machine picks up. `ZSTACK_MACHINE` names another file; `machines/default.toml` is the fallback.
- **A repo** is `repos/<owner>/<name>/`. `policy.toml` holds its values: the gate check, the labels that hold, force a review or preapprove, who "we" are, whose PRs are review-only or skipped, the patent hold's words if it has one, and whether an outside PR is salvaged or sent back. `invariants.toml` holds its own invariants and judgment, which name those values by key.

`config/` ships an example of the last two: a machine working on `acme/app`. Keep your own in a private repo with the same `machines/` and `repos/` folders, clone it on each machine, and point `ZSTACK_CONFIG` at it. A missing or invalid one stops every reader. The repo in play is `ZSTACK_REPO`, else the `origin` of the directory a command runs in, else the machine's only repo. `queue.py` reads and gates PRs only in `merge.repo`; a PR to this package never goes through it, and the owner merges it.

An invariant names a value by its key in braces (`{merge.repo}`) and `queue.py` fills it in from the machine and repo in play.

## set up a machine

1. Write your config (above) and set `ZSTACK_CONFIG` where every thread sees it: under `env` in `~/.claude/settings.json`, and in the shell profile. Run `queue.py check`.
2. Check that `merge.gate_check` names the CI check that must be green before a merge, and that the repo has the labels `merge.hold_label`, `merge.review_label` and `merge.preapproved_labels` name. A thread merges with the machine's own `gh` login, so that login needs push access.
3. Link the pre-push hook into the clone: `ln -s <zstack>/hooks/pre-push <clone>/.git/hooks/pre-push`. Git skips a hook whose link dangles, with no warning, so the repo's publishing invariants are off until the link resolves. `services/pre-push` is a link to the same file, kept only because clones set up before 0.5.0 link to that path.
4. `<root>` holds the machine's state for that repo: `queue.json`, and `events.log`, where the patent hold notes each push it kept local. It is made on first use.
5. Add the clone to T3 Code as a project, and set `run.t3` to its command. Then schedule the pass every few minutes with cron or launchd, with `ZSTACK_CONFIG` in its environment: `<zstack>/queue.py update && <zstack>/queue.py pass`. It prints what it released, launched and cleared as JSON, and exits 1 with the reason when it could not run. A machine with several repos runs one pass per repo, each with its `ZSTACK_REPO`.

## what a coding thread gets

- **`onigiri-mode`**: the one skill it reads. The hard rule (one thread, one task), the working choices this stack makes, the delegates and when to use each, autonomy, and the pull-requests playbook.
- **`unslop`**: strips AI tells from prose.
- **`create-verification-skill`** and **`maintain-verification-skill`**: generate a repo's own `verify-<app>` skill that launches the real app, drives it like a user and captures evidence, then keep its feature map honest.
- **`statechart`**, **`create-formal-verification-skill`** and **`maintain-formal-verification-skill`**: pstack's own text, changed only to use the bundled checker. statechart model-checks a TLA+ spec and draws it as a Mermaid statechart.
- **`bin/everystate`**: a TLA+ model checker with TLC's command line and output and no Java, built on first use from a fork pinned to one commit. statechart and `tests/queue-model.sh` both check with it, and nothing in the package runs Java. All three are slash-command only.
- **The guard** (`hooks/`, with its pi adapter in `pi/rule-guard.ts`): refuses the calls that would break an invariant. `hooks/README.md` lists them.
- **The pi extensions** `subagent` (`pi/README.md`) and `tool-guard`, which bounds a bash call that declares no timeout by `run.idle_min`.

## license

MIT, retained from pstack. Copyright (c) 2026 Lauren Tan for everything derived from it, including the three skills copied verbatim from pstack. Fork changes are under the same license. `pi/subagent/` is from pi, MIT, Copyright (c) 2025 Mario Zechner.
