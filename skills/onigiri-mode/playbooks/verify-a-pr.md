### Verify a PR

**You own the verdict, not the patch.** Do not fix, post, or merge unless asked.

Diagnose produces an explanation. Shipping verifies and lands a PR. This playbook ends at the verdict.

1. Read the PR and its full diff against the base. Record the head SHA and the behavior it claims to change.
2. Run the relevant checks and exercise the changed behavior on its real surface at that head. Record commands, results, and coverage gaps. When a check fails, compare the same check at the base before attributing the failure to the PR. Source inspection and green CI do not replace running the behavior.
3. Write the verdict with its evidence and limitations. Read the complete deliverable back, including its tail. Remove stray reasoning and claims the evidence does not support. If the deliverable is a file, read that file, not the session log.

**Reply:** PR URL, checked head SHA, verdict, evidence, and notes. Use `PASS` when the relevant checks and behavior pass. Use `PASS+NOTES` when they pass with non-blocking caveats. Use `FAIL` for a demonstrated PR defect. Use `BLOCKED` when the environment or missing evidence prevents verification. A required check that never ran is not `PASS+NOTES`.

**Write the verdict where the merge gate reads it.** `PASS+NOTES` is a passing verdict, and the gate reads only the SHA, so a pass carries either word: put the line `verify: PASS at <head sha>` in the PR body, or make `PASS <head sha>` the first line of the run's `verdict.md`. A verdict word with no SHA there is read as no evidence at all, and the PR waits.
