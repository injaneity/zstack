## Host: Mac mini (macOS arm64), not the Linux VM

This session runs on the relay Mac mini. onigiri-mode's Hard rules, appended to the system prompt, name VM paths; on this host use:
- cua clone: `~/onigiri/cua` (instead of /home/box/agent-data/onigiri/cua). Never work in the main checkout; make your own worktree in this session's directory (`git -C ~/onigiri/cua fetch origin && git -C ~/onigiri/cua worktree add <session dir>/wt <branch>`), and remove only the `target/` you built when you finish.
- holds: `~/onigiri/sync/hold-prs.txt` (read-only copy of the VM's /srv/onigiri/hold-prs.txt, synced at launch). Hands-off: `~/onigiri/sync/hands-off.txt`.
- verify: the `verifier` subagent runs the repo verify skill (Hard rules, Checks). This host can build platform-macos, so name it in the verifier's brief when touched.
- Before your first tool call on a coding task, read the onigiri-mode skill and its matched playbook in full.
- The patent pre-push hook is installed in `~/onigiri/cua`; same rules as Hard rules' Patent hold.
- macOS crates build and test here. Linux and Windows platform crates, Linux desktop E2E (KDE, GNOME, X11, Wayland, PipeWire) do not: the verifier says in its verdict which checks could not run here.
- There is no `timeout` or `setsid` command; default bash is 3.2 (bash 5 is first on PATH).
- Wakes and `questions:` work as onigiri-mode's Autonomy section says. After a push to an existing PR, rerun the scoped checks on the new head. A `verified` comment counts only for the head it names.

- Pruning is allowed on the mini to stay productive: stale lume worker VMs (verifier runs stop them, never delete them), finished sessions' build output and caches. Never delete live sessions' VMs or worktrees, seeds the verify skills reference, or the cua clone.
