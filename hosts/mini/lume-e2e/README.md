# lume e2e on the mini

The wrapper's code lives here, in zstack. Its state stays on the host in
`~/.local/share/cua-lume-e2e`: `config.json`, `run.lock`, `runs/` and `unresolved/`.

## install

`~/.local/bin/lume-e2e` is a symlink to this directory's `lume_e2e.py`, so the
host runs the package's copy at whatever commit the package checkout is on:

```bash
ln -sf <zstack>/hosts/mini/lume-e2e/lume_e2e.py ~/.local/bin/lume-e2e
```

## run

In pi: `/skill:lume-e2e test pr #3373`.

The skill resolves the SHA and invokes one synchronous command:

```bash
~/.local/bin/lume-e2e --source /absolute/clean/checkout --sha FULL_SHA
```

Add `--standalone-browser` when required. `--doctor` checks host readiness without
starting a VM. The default runner wait timeout is 7200 seconds, plus bounded
startup and collection time: the full matrix takes about an hour on a loaded host. Keep the command attached and stream its output.

## focused native development

Run exactly one registered AppKit cell without running the full matrix:

```bash
~/.local/bin/lume-e2e --source /absolute/clean/checkout --sha FULL_SHA \
  --focus-cell macos-appkit-ax-tree-ax-not-applicable
```

The pinned source must contain `scripts/ci/macos/appkit-cells.tsv` and the
canonical runner's AppKit selection support. The wrapper checks registration
before cloning and passes the selection to the unchanged guest preparation path
using `--retry-only --retry-attempts 1`. This means one attempt, not an automatic
retry. `--standalone-browser` and `--doctor` cannot be combined with focused mode.

`result.json` records `mode: focused`, `focus_cell`, and, on validated success,
`focused_passing_cells: 1`. It never reports `passing_cells` for focused mode.
Validation requires exactly the selected passing AppKit cell, matching SHA and
native selection metadata, one recorded attempt, and standard-daemon restoration.
Missing, extra, failed, or mismatched evidence cannot pass. Focused evidence is
never full-matrix certification. Failures still require explicit authorization
before another run; the wrapper never repairs or deletes workers.

## what happens

Check prerequisites and claim an exclusive lock → clone the stopped source VM →
boot → transfer an exact git archive → GUI launchd opens the guest Terminal command
→ run the unchanged canonical `run-all.sh` → retrieve evidence → stop the worker.

The desktop-session keychain helper retrieves the configured credential and passes
it through a private socket, then SSH stdin and a guest FIFO. It creates no password
files and needs no runtime password flag. Host login and keychain access are still
required; the command never bypasses consent or changes keychain settings.

## configuration

`config.json` names the clone source (`seed`), guest user, credential service and
account, and optional `keychain_path` (default: the user's login keychain).
`minimum_free_gib` defaults to 80. Default Lume storage is used.

The configured source is the retained PR #3373 worker, kept stopped and cloned only.
It is a useful local baseline, not a certified immutable private seed. `seed_note`
records this provenance in every result. There is no `seed_verified` boolean: a
self-declared flag proves nothing. The canonical runner's session, signing,
permissions, source, and evidence preflights remain unchanged. This does not waive
any repository requirements for official seed provenance or release certification.

Prepare a new baseline when toolchain, OS, signing identity, or consent state needs
to change; never modify it during a run. Do not set up or repair the baseline
implicitly when asked merely to test a PR. Only explicitly authorized diagnostic
runs may use a different baseline; record its provenance in local configuration.

## results and stopping

The command prints `runs/<run-id>/`. Read `result.json`, `evidence/runner.log`, and
the canonical reports under `evidence/source/artifacts/cua-driver/`.

- exit 0: runner passed and evidence/standard-daemon restoration verified;
- exit 1: test failure, infrastructure failure, cancellation, or incomplete evidence;
- exit 2: configuration/preflight blocked before execution.

A failed test is a useful captured outcome, not permission to retry. Ctrl-C/SIGTERM
attempts partial evidence collection and stops the owned worker. No VM is deleted.
Host loss or SIGKILL cannot guarantee cleanup; inspect the recorded worker before
starting another run. A failed stop is recorded as `cleanup_error` and does not change
the test verdict. The worker is also recorded in `unresolved/`; the next run stops it
again and confirms it is stopped before cloning, and refuses to start while that fails
or any `cua-e2e-*` worker is still running, so a leftover never runs beside a new guest.

A blocked run never asks you to `lume stop` a worker by hand: a running `cua-e2e-*` worker may belong
to a live run that is still testing in it. Stop a leftover with `lume-e2e --stop-leftover <worker>`.
It takes `run.lock` without waiting and refuses while any lume-e2e run holds it, since a live run owns
the worker it cloned and stops it itself. With the lock held no run is live, so it stops the worker,
confirms it reads `stopped` with no `lume run` process left, and clears its `unresolved/` mark. A worker
already stopped counts as stopped even though `lume stop` exits 1 on it. A second run started while one
holds `run.lock` waits for it. Before it clones, a run also waits, printing one line, while
the kernel's memory pressure (`kern.memorystatus_vm_pressure_level`) is above normal; it never
stops another VM or process to make room. The boot check gives a fresh worker 30 s of `stopped`
reads before it calls that a boot failure, inside a strict 180 s boot window. A moved PR head remains untested even if the pinned SHA passes.

## trust boundaries

Use this installed host wrapper, never a candidate's host scripts. Candidate code
runs in the disposable guest, with no GitHub credentials or host private SSH keys
and no configured writable host shares. NAT egress remains available; do not expose
privileged host services to that network. Review candidate runner changes: the
canonical runner receives the guest-only unlock secret through its documented
environment variable and must unset it before building. This is not isolation from
a deliberately malicious runner and must never use release-signing credentials.

The helper socket is private to the host account, not a boundary against other
code running as that same account. Artifact extraction rejects links, traversal,
absolute paths, and more than 20 GiB uncompressed data. Archive transport is
time-bounded but has no separate size quota. Submodules/LFS payloads are not
materialized automatically by git archive.

## evidence so far

- GUI-launch experiment: full 158-row run, exit 0, no guest human intervention;
  `runs/20260907T124840Z/`.
- Packaged startup/cancellation smoke: canonical build started; cancellation,
  partial evidence retrieval, and worker stop succeeded;
  `runs/20260907T133919Z-8bc97d87/`. It used the then-supported diagnostic stdin path.
- Desktop keychain helper: real retrieval and missing-item refusal verified from pi.
- Focused AppKit validation: `runs/20260914T174359Z-9a7ef406/`, SHA
  `6aabab10cd6276b27267dd0f960a159f2c75d74d`. One selected accessibility-tree cell
  passed in one attempt; evidence and standard-daemon restoration verified,
  worker stopped. This is not full-matrix or geometry-specific certification.
- The focused-capable command has not completed a new full run. No full-matrix
  certification claim is implied by the focused validation.

The tests start no VM and touch no host state:

```bash
python3 -m unittest discover -s hosts/mini/lume-e2e -p 'test_*.py'
```

## Reviewed targeted packages

`--targeted-package /absolute/package` runs a reviewed targeted contract instead of
the full or registered focused matrix. It cannot be combined with other selections.
The initial supported contract is `pr4384-relative-profile`; additional contracts
require explicit result-validator review rather than accepting arbitrary success flags.

This mode keeps the installed credential bridge, private FIFO, desktop launch,
exclusive lock, cloning, collection, and stop workflow. It prepares only the existing
dedicated signing keychain, pins the existing identity, explicitly probes signing,
and clears the credential before starting the payload. The payload verifies existing
daemon grants before expensive builds. No new keychain, signing identity, ACL, TCC
grant, login/history preparation, capture-reminder setting, or unrestricted daemon is
introduced. Missing authorization is a failure; the mode never automates OS prompts.

The package manifest pins the production SHA and every separate fixture file.
Production source comes from the normal clean checkout archive, without overlays.
`mode: targeted` and `status: targeted_passed` never imply full-matrix certification.
Success requires exact targeted evidence, standard daemon mode and evidence collection.
Worker shutdown is checked and reported in `worker_stopped`/`cleanup_error`; a failed
stop blocks the next run instead of the verdict. Failed attempts retain their actual execution state.

Example after package/tooling review:

```bash
~/.local/bin/lume-e2e \
  --source /absolute/clean/checkout \
  --sha 529af6bed3f3459a0aeef6535f78ca4fc4198594 \
  --targeted-package /absolute/reviewed/package
```

No extra permission setup or manual password argument is required. The existing
configured credential and stable daemon identity are reused. Actual OS or keychain
access denial still stops the run and is not permission to change security settings.
