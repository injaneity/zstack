# zstack

For any non-trivial coding task, read the **onigiri-mode** skill first.

Do the work in the main thread, in verifiable units. Use delegates only at the steps onigiri-mode's Subagents section names.

A change to `orchestrator/plan`'s queue steps or the playbook's pass changes `model/Plan.tla` to match in the same change, with a mutant in `model/plan_mutants.py` for the bug it fixes, so the fix is model-checked (`tests/plan-model.sh`).

The stack (`skills/`, `agents/`, `extensions/`, `hooks/`, `bin/policy`) names no repo, person or machine. Such a value is a key in the config, and prose names the key.
