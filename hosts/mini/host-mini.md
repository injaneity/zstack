## Host: Mac mini (macOS arm64), not the Linux VM

This session runs on the relay Mac mini. The onigiri policy below names VM paths; on this host use:
- cua clone: `~/onigiri/cua` (instead of /home/box/agent-data/onigiri/cua). Never work in the main checkout; make your own worktree in this session's directory (`git -C ~/onigiri/cua fetch origin && git -C ~/onigiri/cua worktree add <session dir>/wt <branch>`), and remove only the `target/` you built when you finish.
- holds: `~/onigiri/sync/hold-prs.txt` (read-only copy of the VM's /srv/onigiri/hold-prs.txt, synced at launch). Hands-off: `~/onigiri/sync/hands-off.txt`.
- verify: follow policy.md (repo verify skills under `.cursor/skills/verify-cua-driver` / `verify-cua-spaces`; elsewhere fmt, clippy, test for touched crates/projects). This host can build platform-macos, so check it when touched.
- The patent pre-push hook is installed in `~/onigiri/cua`; same rules as the policy.
- macOS crates build and test here. Linux and Windows platform crates, Linux desktop E2E (KDE, GNOME, X11, Wayland, PipeWire) do not: say in the verdict which checks could not run here.
- There is no `timeout` or `setsid` command; default bash is 3.2 (bash 5 is first on PATH).
- To wake the orchestrator mid-run (blocked, a question, a PR opened), run `"$ONIGIRI_WAKE" <kind> "<message>" [pr=N]`. The launcher already sends pi-done or pi-failed when you exit. It skips silently when this host has no key.
- When you push to an existing PR, rerun the scoped checks on the new head and post a new `verified <new full head sha>: ...` comment, as the policy says. A comment for an older head does not count.

- Pruning is allowed on the mini to stay productive: stale lume worker VMs, finished sessions' build output and caches. Never delete live sessions' VMs or worktrees, seeds the verify skills reference, or the cua clone.
