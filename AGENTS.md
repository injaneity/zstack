# zane's coding setup

zstack is pstack trimmed for pi. For any non-trivial coding task, read the **onigiri-mode** skill first and follow its routing table.

Do the work in the main session, in verifiable units. Use subagents only at the steps onigiri-mode's Subagents section names.

A change to `bin/hourly`'s pass or the hourly playbook's steps 0 to 5 changes `model/Hourly.tla` to match in the same PR, with a mutant in `model/hourly_mutants.py` for the bug it fixes, so the fix is model-checked (`tests/hourly-model.sh`). The same goes for `bin/PiRun.tla` and `model/Zstack.tla` and the code they model.
