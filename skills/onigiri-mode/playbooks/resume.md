### Resume

**You own the resume point. Read the prior trail, don't redo it.**

1. Find the prior session under `~/.pi/agent/sessions/--<mangled-cwd>--/`. Read the tail first, then scan back for decision points. Reduce a long transcript with a script.
2. Rebuild the state from the repo: the branch, `git log` against the base, `git status`, and what is pushed.
3. Name the resume point and skip work that already landed. Re-verifying everything from scratch means you decided the trail was untrustworthy.
4. Check inherited claims against the goal on the real artifact. A prior self-report is not proof.
5. Hand the remaining work to the matching playbook.

**Pausing.** Stop at a safe boundary and start nothing new. Commit edits as one `wip:` commit. Write a resume note to a file: the intent, what is verified, what isn't, the next action, key paths, and gotchas.

**Reply:** where the work stopped, what you inherited versus redid, the resume point, the tree state, and the first action.
