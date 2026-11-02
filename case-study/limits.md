# Limits

What zstack does not do at `37787071`.

## Boundaries that are trust, not sandbox

- **The reviewer is not sandboxed.** "Read-only" is a sentence in its prompt (`agents/onigiri-reviewer.md:15`). It keeps `bash`, which can write files, run any command and reach the network (`docs/SUBAGENT.md:27`). The real bound is that the child has no forge or git credentials (`extensions/subagent/index.ts:52-66`), plus trust in the prompt. There is no container, seccomp profile, read-only mount or network policy.
- **The depth limit bounds the tool, not the process.** A child cannot call `subagent`. Its `bash` can still run `pi`, and `env -u PI_SUBAGENT_DEPTH pi` brings the tool back (`docs/SUBAGENT.md:16`).
- **The credential scrub is a name match.** It drops variables whose names contain `TOKEN`, `SECRET`, `AUTH` and similar words (`index.ts:54`). It keeps every variable with a model-provider prefix (`index.ts:55-56`). Run under node, the regex kept `AWS_SECRET_ACCESS_KEY`. By the same prefix rule it keeps any `AWS_*`, `GOOGLE_*` or `AZURE_OPENAI_*` variable, because pi may need them to reach Bedrock, Vertex or Azure. A cloud key that does more than call a model reaches the child too. A credential stored under a plain name (`DEPLOY_KEY`) also gets through. So do SSH keys and `.netrc` files on disk.
- **MCP tools may reach the reviewer.** pi's `--tools` allowlist "Keeps MCP tools unless an entry starts with mcp__" (`pi --help`), and the reviewer's list names none (`agents/onigiri-reviewer.md:4`). Not tested.

## The main session is governed by instructions

The main session runs with every default tool and the operator's `gh` login. Holds, the merge rule, "never force-push", commit-text rules and the pause list are text in `policy.md` and `skills/onigiri-mode/SKILL.md`. Nothing in this repo enforces them (`governance.md`, C20 to C26).

- There is no merge gate. It was cut from the package (`docs/PROVENANCE.md:40`). The `verify:` and `risk:` lines (`policy.md:47-48`) are read by nothing in this repo.
- `main` has no branch protection.
- On the reference host, a cron tick is the only thing that merges in one target repo (`docs/HOST.md:28`). It is not part of zstack and is not assessed here.

## Missing controls

- **Identity.** Sessions act as the operator's `gh` account (`docs/HOST.md:20` names it on the reference host). There is no bot or GitHub App identity.
- **Budgets.** There is no token, cost, turn or wall-clock limit for a session or a child. No cap applies across several `subagent` calls in one turn. A child that ignores the no-sleep line (`index.ts:45-46`) runs until it ends or the parent aborts.
- **Audit trail.** Parent sessions are saved under pi's sessions directory. Children run with `--no-session` (`index.ts:239`), so only their final text survives in the parent (`docs/SUBAGENT.md:32`). Costs are not recorded. The per-child tally was removed with upstream's renderer (`docs/SUBAGENT.md:9`).
- **Kill switch.** None is shipped. See `answers.md`, question 5.
- **Drift detection and golden tasks.** There are none. CI is static and calls no model (`.github/workflows/test.yml`). No model is pinned (`test.yml:26-28` forbids naming one in an agent file).
- **No GitHub Agentic Workflow.** zstack runs as a local pi package, not in Actions. Its one workflow is a static check (`test.yml`). It has no trigger on issues or PRs, no safe outputs and no network restriction.

## Evidence limits

- **Tests are hand runs, not a suite.** Every "implemented-and-tested" row in `governance.md` was run while this directory was written. The output is quoted there and not committed. There is no `tsc` or unit test in CI for `extensions/subagent/index.ts`.
- **Some evidence is a model's report.** C1, C4 and C5 rest on what the child said about its own tools and prompt. In one run the child denied that the framing sentence was in its prompt. A later run with a marker policy showed the policy text did arrive. Model self-reports are weak evidence.
- **One adversarial sample.** The prompt-injection run (`answers.md`, question 1) is one run on one model. It shows the reviewer refused once. It does not show that it always will.
- **Platforms.** Everything ran on Linux. No macOS or Windows run was done for this case study.
- **PR #8's history.** The first version of the extension spread `process.env` into the child (credentials included) and appended the policy with no framing. The CI name check at that time matched only "Spawn a `name`". These facts come from the PR's first commit (`4974b481`, fetched from the PR ref) and an internal review note. That commit is no longer reachable from `main`, which carries only the squash `e9a2b35`.
