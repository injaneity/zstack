### Resume

**You own the resume point. Read the prior trail, don't redo it.**

1. Find the prior session. pi keeps them under `~/.pi/agent/sessions/--<mangled-cwd>--/<timestamp>_<id>.jsonl`. `pi --continue` reopens the most recent one for the working directory and `pi --resume` opens the picker. Read the tail first, then scan back for the decision points. Parse a long transcript with a script that prints only those, and keep the reduced timeline in the thread.
2. Reconstruct operational state from the repo, not the transcript alone. The branch, `git log` against the base, `git status`, the commits already pushed. The prior trail is authoritative input, so resist the urge to re-derive it.
3. Diff done against pending, name the resume point, and skip work that already landed. A "let me verify all of it from scratch" pass means you decided the trail was untrustworthy when it is not.
4. Check the inherited claims against the original goal on the real artifact. A prior self-report is not proof.
5. Route the remaining work to the matching playbook. This one ends there.

**Pausing.** When the user stops you mid-flight, stop at a safe boundary: finish or back out of the current atomic step, start nothing new. Commit uncommitted edits as one `wip:` commit so nothing is lost, and say so in the body if the tree is broken. Write the resume note to a file, not the transcript, since the transcript is what gets compacted: the intent, what is verified, what is not, the next action, key file paths, and the gotchas. Take no irreversible step just to pause.

**Reply:** where the prior work stopped, what you inherited versus redid, the resume point, the commits on disk and whether the tree is clean, the first action, and the outcome.
