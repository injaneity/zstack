# One example host

This is how Zane's deployment runs zstack. It is one example, not a requirement: nothing outside `hosts/` reads these paths, and a fresh install needs none of them. This file and `hosts/` are the only places in the repo that name them.

## Machines

- **The box** runs the chat orchestrator. **The VM** (onigiri-linux) runs every pi session and the merge job. The relay Mac holds the VM's key and `~/onigiri-vm/vm`.
- Reach the VM from the box with `HOME=/home/box /srv/onigiri/tools/cua-main-88e6d9f do --no-record shell '~/onigiri-vm/vm "..."'`. For anything longer than a line, base64 a script over and run it as `bash /tmp/x.sh`. If the VM is unreachable, stop and ask Zane. Never do the work in the chat instead.
- `pi list` on the VM shows `git:github.com/injaneity/zstack`, checked out at `/home/box/.pi/agent/git/github.com/injaneity/zstack`. `/home/box/.pi/agent/skills/` is empty by design.

## Parity

Every host runs merged main and nothing else. Three things keep it so:

- `bin/pi-run` runs `bin/ensure-package-main` before every launch, and a bare `hosts/vm/merge_job.py` run (cron) runs it before every merge pass and restarts on the new code. It fast-forwards the package checkout to `origin/main` and never stashes, resets or forces, so a checkout it cannot move is fixed by hand.
- The `subagent` tool loads agents only from the package's `agents/`. Nothing is copied to `<pi agent dir>/agents/`, and a file left there is not read.
- cua's pre-push hook is a symlink into the package on both hosts, so the patent hold is main's too:
  `ln -sf <zstack>/hosts/vm/pre-push <root>/cua/.git/hooks/pre-push` (`<root>` is `/srv/onigiri` on the VM and `~/onigiri` on the mini). It runs `hosts/vm/patent_push.py`.

Every entry point (`bin/*`, `hosts/check-parity`, `hosts/vm/merge_job.py`, `wake.py`, the pre-push hook) prints `zstack <sha>` on stderr first, and the merge job's `last-run` carries it. `hosts/check-parity` prints it on stdout, checks all three and changes nothing but the fetched `origin/main` ref. It prints one `ok` or `FAIL` line each for the package (on `main`, clean, at `origin/main`), the hook (that symlink) and the agents dir (nothing in it that is not equal to the package's file of the same name), and exits 1 on any `FAIL`. Both host files run it at a launch, so `bin/pi-run` exits 4 and starts nothing on a host that differs. The merge job runs it on every cron run and raises an urgent `parity` wake, once an hour while it lasts. Run it by hand with `ONIGIRI_ROOT=<root> hosts/check-parity`.

## Holds file

`/srv/onigiri/hold-prs.txt`, one row per line. Only Zane edits it. The merge job reads three kinds: `hold | <PR> | <rules> | <note>`, `preapprove | #<PR> | <rules> | <40-char head sha> | <source>`, and `hands-off-path | <file name> | <rules> | <note>`, which holds every PR that changes a file of that name (the job waits with `who=zane`). To stop one PR, add a `hold` row. A malformed one, or a row of any other kind that names a PR number, makes the file unreadable, and then every PR counts as held. Rows about a login (`skip-author`, `review-only`) steer the hourly pickup and the merge job does not read them: `skip-author` (bots) is never picked up or reviewed, and `review-only` (teammates) gets a first review and nothing else, so a review task for one is filed, never cancelled. The mini reads a copy synced at launch.

## Launching

- One launcher on both hosts: `bin/pi-run <id> <role> <brief-file>`. `~/.config/onigiri/host` holds `vm` or `mini` (`ONIGIRI_HOST` overrides it), naming `hosts/<host>/host.sh`, which sets the root and the env, so a launch from any shell (nohup, ssh, cron) loads `~/onigiri/env.sh` or `~/.profile`. It then refuses to start, before any run state or wake, when `pi --list-models` in that env lists no model of the role's provider (no credential). `bin/pi-run --help` has the run states, the exit codes and the wakes.
- The hourly pass (`skills/onigiri-mode/playbooks/hourly.md`, `bin/hourly`) runs on the mini and reaches the VM as the peer `vm=~/onigiri-vm/vm` (`ONIGIRI_PEERS` in `hosts/mini/host.sh`). Its `pickup.log`, `hourly-fingerprint.json` and `hourly-snapshot.json` are in `~/onigiri`. It collects with `bin/pi-run list exited lost`, then `bin/pi-run collect <id>` for each run it read, on each host. `bin/hourly queue` launches both hosts' queues (queued `review/<id>/run.json` in `~/onigiri` and `/srv/onigiri`), each run on its own host, and resumes a run back from a 429 once its `resume_at` has passed.
- The hourly pass does every pickup and launch. Nothing on the VM starts pi on a schedule. To test a provider, do a real `pi -p` run, because `pi auth check --provider claude-code` reports `not_ready` even when the seat works.
- Each host keeps its own provider logins in `<agent dir>/auth.json`. Never copy one host's tokens to the other: an OpenAI refresh token is single use, so two hosts sharing one invalidate each other with `refresh_token_reused`.
- The agents list FFF's search tools, which come from a separate package: `pi install npm:@ff-labs/pi-fff@0.11.0`, once per host. Without it pi skips those tools.
- Wakes POST to the urgent-wake webhook with the key in `~/.config/onigiri-urgent-webhook`. Only Zane places that file, mode 0600, on each host. A host without it logs `webhook not configured`.
- `gh` runs as injaneity with `GH_CONFIG_DIR=/home/box/.config/gh HOME=/home/box`. Upstream or public posts go only through dumpling.

## Disk

`df -h /` must show well over 30G free. A run that hits ENOSPC dies mid-task. Nothing guards the disk on a schedule. The hourly routine records free space with every pickup decision. Task dirs under `/home/box/sand-data/onigiri/cua/.git/onigiri/v2/` can hold unpushed work even when their `pid` is dead, so deleting one is data deletion. List the candidates and ask Zane first.

## The merge job

`hosts/vm/merge_job.py` is the only thing that merges in `trycua/cua`. Its header has the gate, the actions and the modes (`--dry-run`, `--explain N`). Cron runs it every minute through `hosts/vm/cron-wrap.sh tick 900 2400`, which starts it once 15 minutes have passed since the last start (the VM is paused between sessions and cron skips the slots it missed), kills it after 40 minutes, and writes start and end lines to `/srv/onigiri/tick/cron.log`:

```
* * * * * HOME=/home/box GH_CONFIG_DIR=/home/box/.config/gh /home/box/.pi/agent/git/github.com/injaneity/zstack/hosts/vm/cron-wrap.sh tick 900 2400 /usr/bin/flock -n /srv/onigiri/tick/tick.lock /usr/bin/nice -n 5 /usr/bin/python3 /home/box/.pi/agent/git/github.com/injaneity/zstack/hosts/vm/merge_job.py > /dev/null 2>> /srv/onigiri/tick/tick.log
```

- trycua/cua merges through a merge queue, so `gh pr merge` can succeed before anything merged. The job logs `merge-queued cua#N` once per head, and `merged cua#N … mergedAt <time>` once GitHub says MERGED.
- Outputs: `/srv/onigiri/events.log`, and `state.json`, `last-run`, `cron.log` and `tick.log` in `/srv/onigiri/tick/`, which holds data only. There is no buffer. The five urgent kinds (`unexpected-merge`, `parity`, `holds-unreadable`, `merge-watch-overflow`, `merge-failed`) POST the webhook once per key. Every other reason is a `wait` with `who=zane`, `who=session` or none, which `--dry-run` prints and `bin/hourly` shows under needs-Zane and unstick (`ONIGIRI_MERGE_HOST`). The patent hook sends a `blocked` wake for a held push, once per branch and head. `/srv/onigiri/tick/PAUSED` stops merges and reruns.
- Don't score or flag drafts. Outside PRs go to the 3h sweep, never an urgent wake.
