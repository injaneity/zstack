## Host: onigiri-linux VM

- cua clone: `/srv/onigiri/cua` (the same checkout as /home/box/agent-data/onigiri/cua). Never work in the main checkout; make your own worktree in this session's directory (`git -C /srv/onigiri/cua fetch origin && git -C /srv/onigiri/cua worktree add <session dir>/wt <branch>`), and remove only the `target/` you built when you finish.
