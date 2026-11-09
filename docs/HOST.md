# One example host

This is how Zane's deployment runs zstack. It is one example, not a requirement: nothing outside `hosts/` reads these paths, and a fresh install needs none of them. This file and `hosts/` are the only places in the repo that name them.

## Machines

- **The box** runs the chat orchestrator. **The VM** (onigiri-linux) runs every pi session and the merge job. The relay Mac holds the VM's key and `~/onigiri-vm/vm`.
- Reach the VM from the box with `HOME=/home/box /srv/onigiri/tools/cua-main-88e6d9f do --no-record shell '~/onigiri-vm/vm "..."'`. For anything longer than a line, base64 a script over and run it as `bash /tmp/x.sh`. If the VM is unreachable, stop and ask Zane. Never do the work in the chat instead.
- `pi list` on the VM shows `git:github.com/injaneity/zstack`, checked out at `/home/box/.pi/agent/git/github.com/injaneity/zstack`. `/home/box/.pi/agent/skills/` is empty by design.

## Rules

- The rules every session runs under are onigiri-mode's Hard rules section, appended by both launchers through `bin/hard-rules`, so they move with the package pin.
- Holds live in `/srv/onigiri/hold-prs.txt`, one `kind | target | rules | note` row each. The merge job reads it on every run. Only Zane edits it.
- Author rows steer the hourly pickup, not the merge job. `skip-author` (bots) is never picked up or reviewed, and neither is a PR of our own. `review-only` (teammates) gets a first review and nothing else: no push, rebase, salvage, close, comment or request-changes, so a review task for one is filed, never cancelled.

## Launching

- Models come only from `config/models.json`, one provider and model per role. Each launcher takes a role, resolves it with `bin/model-for`, and records it as the session name `role:<role>`. `bin/check-models` reads that name and the provider and model each reply came from in the pi session files, and prints every mismatch with the config. Run it on either host with no arguments.
- VM: `/home/box/bin/pi-run` is a shim that execs `hosts/vm/pi-run <session-id> <role> <brief-file>`. It sources `/home/box/.profile`, which holds the Claude token, and appends onigiri-mode's Hard rules (`bin/hard-rules`); without them it exits 2. A non-login shell without the profile looks logged out. It takes a role only: the old `<provider> <model>` form, or any name that is not a role, exits 2 with `bin/model-for`'s error.
- Mini: `~/onigiri/bin/pi-run-mini` is a shim that execs `hosts/mini/pi-run-mini <slug> <brief-file> [<role>]` (default `coding`; a name that is not a role exits 2). It prepends `hosts/mini/host-mini.md` to the brief and starts the package's `bin/pi-run`, which appends the Hard rules, detached in `~/onigiri/review/<slug>`.
- Every new launch first runs `bin/ensure-package-main`, which fetches `origin main` and fast-forwards the package checkout to it, printing `zstack: <old> -> <new>` or `zstack: already at <sha>` on stderr. A dirty tree, a branch other than `main`, a detached HEAD, a failed fetch or a non-fast-forward aborts the launch with exit 4 and the reason, and pi does not start. It never stashes, resets or forces, so fix the checkout by hand. `PI_RUN_DRY` and `PI_RUN_MINI_DRY` still run it, so a dry run proves the update. Sessions already running keep the tree they started on. To check a host: `git -C ~/.pi/agent/git/github.com/injaneity/zstack fetch origin main` then compare `rev-parse HEAD` with `rev-parse origin/main`.
- After the pin, each launcher runs `bin/ensure-extensions`, which makes sure every third-party pi package zstack sessions load is in the host's `<agent dir>/settings.json` `packages` list, at the version pinned in that script. Today that is `npm:@ff-labs/pi-fff@0.11.0`, FFF's `fffind`, `ffgrep` and `fff-multi-grep` tools. It prints `extensions: <source> present` or `extensions: installed <source>` on stderr. If `pi install` fails, the launcher exits 4 and pi does not start. Every role and every subagent child loads it, because user packages load for every session. A host can be bootstrapped by hand with `pi install npm:@ff-labs/pi-fff@0.11.0`. The package has no credentials, so nothing is copied between hosts.
- The hourly routine does every pickup and launch. Nothing on the VM starts pi on a schedule. To test a provider, do a real `pi -p` run, because `pi auth check --provider claude-code` reports `not_ready` even when the seat works.
- Each host keeps its own provider logins in `<agent dir>/auth.json`. Never copy one host's tokens to the other: an OpenAI refresh token is single use, so two hosts sharing one invalidate each other with `refresh_token_reused`.
- Only failures wake onigiri. Both launchers run `hosts/vm/wake.py --exit <rc> <session-id>` when pi exits, which POSTs `pi-failed` with `rc` to the same urgent-wake webhook and key file the merge job uses, `~/.config/onigiri-urgent-webhook`, when rc is not 0, and logs `wake: pi-done not sent: only failures wake` when it is. A periodic pickup run collects finished runs: review dirs with a `run.rc` and no processed marker. Each launcher writes `run.rc` itself, after the wake, on every exit of the run it started, including a TERM to the pid it recorded: the mini's in `~/onigiri/review/<slug>/` once its detached wrapper starts, the VM's in `/srv/onigiri/review/<session-id>/` once it owns the id, which also covers its own setup failures. A mini setup failure exits 4 to the caller before anything starts. A signal other than TERM (or HUP on the VM), such as SIGKILL or an OOM kill, leaves no `run.rc`, and a dry run leaves no dir. A session that reports itself failed or blocked mid-run wakes onigiri with `"$ONIGIRI_WAKE" blocked|failed "<message>" [pr=N] [field=value ...]`. Every other kind (`pr-opened`, `question`, `milestone`) is logged as not sent, and a question for Zane goes in a `questions:` list in the run's report. `id` is the session id and the body carries `source: pi`. It is best effort: a 5 s timeout, three tries on network errors and 5xx, and the launcher keeps pi's exit code whatever happens. A host without the key file skips and logs `wake: <kind> webhook not configured` (the mini's `run.log`, the VM launcher's stderr). Only Zane places that file, mode 0600, on each host. `ONIGIRI_WAKE_DRY=1` prints the body and sends nothing.
- `gh` runs as injaneity with `GH_CONFIG_DIR=/home/box/.config/gh HOME=/home/box`. Upstream or public posts go only through dumpling.

## Disk

`df -h /` must show well over 30G free. A run that hits ENOSPC dies mid-task. Nothing guards the disk on a schedule. The hourly routine records free space with every pickup decision. Task dirs under `/home/box/sand-data/onigiri/cua/.git/onigiri/v2/` can hold unpushed work even when their `pid` is dead, so deleting one is data deletion. List the candidates and ask Zane first.

## The merge job

`hosts/vm/merge_job.py` is the only thing that merges in `trycua/cua`, by the merge rule in onigiri-mode's Hard rules. Cron runs it every minute through `hosts/vm/cron-wrap.sh tick 900 2400`, which starts it once 15 minutes have passed since the last start (the VM is paused between sessions and cron skips the slots it missed), kills it after 40 minutes, and writes start and end lines to `/srv/onigiri/tick/cron.log`:

```
* * * * * HOME=/home/box GH_CONFIG_DIR=/home/box/.config/gh /home/box/.pi/agent/git/github.com/injaneity/zstack/hosts/vm/cron-wrap.sh tick 900 2400 /usr/bin/flock -n /srv/onigiri/tick/tick.lock /usr/bin/nice -n 5 /usr/bin/python3 /home/box/.pi/agent/git/github.com/injaneity/zstack/hosts/vm/merge_job.py > /dev/null 2>> /srv/onigiri/tick/tick.log
```

- The gate is three checks at the PR head: `ci-gate` SUCCESS, no hold row (`hold`, `no-move`, or a `hands-off-path` the diff hits by its `mode=`), and a `preapprove` row at that head for a declared deep or human tier. Skipped or missing platform jobs don't block when ci-gate is green. Beside the gate, the PR must not be a draft, GitHub must be able to merge it (no conflict, mergeable), and `/srv/onigiri/tick/PAUSED` must not exist.
- Per open PR by injaneity: merge (`gh pr merge --squash --match-head-commit`), rerun a red ci-gate's failed runs once per head, buffer for onigiri, or skip. A draft is always a skip: the job never marks a PR ready, so a draft merges only after a person or its own session readies it. rule-guard lets a session ready one only when its brief has a `merge: allowed` line. It never arms auto-merge. A merge as injaneity it did not make raises an urgent `unexpected-merge` wake.
- Outputs keep the tick's paths: `/srv/onigiri/events.log`, `/srv/onigiri/buffer.jsonl`, and `state.json`, `last-run`, `cron.log` and `tick.log` in `/srv/onigiri/tick/`, which holds data only.
- `merge_job.py --dry-run` prints one `dry-run cua#N <head> <action>: <reasons>` line per PR and changes nothing. `--explain N` prints every reason for one PR.
- The patent hold is cua's pre-push hook: `ln -sf <zstack>/hosts/vm/pre-push /srv/onigiri/cua/.git/hooks/pre-push`. It runs `hosts/vm/patent_push.py`.
- Don't score or flag drafts. Outside PRs go to the 3h sweep, never an urgent wake.
