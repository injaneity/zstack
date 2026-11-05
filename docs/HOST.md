# One example host

This is how Zane's deployment runs zstack. It is one example, not a requirement: nothing outside `hosts/` reads these paths, and a fresh install needs none of them. This file and `hosts/` are the only places in the repo that name them.

## Machines

- **The box** runs the chat orchestrator and the tick. **The VM** (onigiri-linux) runs every pi session. The relay Mac holds the VM's key and `~/onigiri-vm/vm`.
- Reach the VM from the box with `HOME=/home/box /srv/onigiri/tools/cua-main-88e6d9f do --no-record shell '~/onigiri-vm/vm "..."'`. For anything longer than a line, base64 a script over and run it as `bash /tmp/x.sh`. If the VM is unreachable, stop and ask Zane. Never do the work in the chat instead.
- `pi list` on the VM shows `git:github.com/injaneity/zstack`, checked out at `/home/box/.pi/agent/git/github.com/injaneity/zstack`. `/home/box/.pi/agent/skills/` is empty by design.

## Policy

- The live policy is `/srv/onigiri/policy.md`. `/home/box/.pi/agent/onigiri/policy.md` is a symlink to it, so lookup step 2 finds it with no `$ONIGIRI_POLICY` set.
- Holds live in `/srv/onigiri/hold-prs.txt`, one `kind | target | rules | note` row each. The tick reads it on every check. Only Zane edits it.

## Launching

- Models come only from `config/models.json`, one provider and model per role. Each launcher takes a role, resolves it with `bin/model-for`, and records it as the session name `role:<role>`. `bin/check-models` reads that name and the provider and model each reply came from in the pi session files, and prints every mismatch with the config. Run it on either host with no arguments.
- VM: `/home/box/bin/pi-run` is a shim that execs `hosts/vm/pi-run <session-id> <role> <brief-file>`. It sources `/home/box/.profile`, which holds the Claude token, and appends `/srv/onigiri/policy.md` by literal path. A non-login shell without the profile looks logged out. The old `<provider> <model>` form still parses but runs as role `coding` with a warning.
- Mini: `~/onigiri/bin/pi-run-mini` is a shim that execs `hosts/mini/pi-run-mini <slug> <brief-file> [<role>]` (default `coding`). It prepends `hosts/mini/host-mini.md` to the brief and starts the package's `bin/pi-run` detached in `~/onigiri/review/<slug>`.
- The tick's pickup calls the VM launcher from `/srv/onigiri/tick/pi-task.sh`. To test a provider, do a real `pi -p` run, because `pi auth check --provider claude-code` reports `not_ready` even when the seat works.
- Each host keeps its own provider logins in `<agent dir>/auth.json`. Never copy one host's tokens to the other: an OpenAI refresh token is single use, so two hosts sharing one invalidate each other with `refresh_token_reused`.
- `gh` runs as injaneity with `GH_CONFIG_DIR=/home/box/.config/gh HOME=/home/box`. Upstream or public posts go only through dumpling.

## Disk

`df -h /` must show well over 30G free. The tick pauses itself below 30G, and a run that hits ENOSPC dies while its task record still says `running`. The tick deletes `node_modules` and cargo `target/` dirs in finished runs. Task dirs under `/home/box/sand-data/onigiri/cua/.git/onigiri/v2/` can hold unpushed work even when their `pid` is dead, so deleting one is data deletion. List the candidates and ask Zane first.

## The tick

`/srv/onigiri/tick/tick.py` runs from cron every 15 minutes, logs to `/srv/onigiri/events.log`, and pauses while `/srv/onigiri/tick/PAUSED` exists. In `trycua/cua` it is the only thing that merges, by the policy's merge rule, through one gate (`tick/gate.py`, `tick.py --explain N` prints its reasons). It never arms GitHub auto-merge, and a merge it did not make raises an unexpected-merge wake. zstack ships no merge gate. Don't score or flag drafts. Outside PRs go to the 3h sweep, never an urgent wake.
