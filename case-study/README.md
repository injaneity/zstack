# zstack as a case study

This directory explains zstack to a reader who has only this commit. It adds prose and changes no existing file.

## What zstack is

zstack is a pi package (`package.json:7-11`). It gives a coding agent one skill that routes work to five playbooks (`skills/onigiri-mode/SKILL.md:69-77`), one policy file that holds the rules (`policy.md`), and a `subagent` tool that starts one cold reviewer before a PR opens (`skills/onigiri-mode/playbooks/pull-requests.md:13`, `agents/onigiri-reviewer.md`).

The governed workflow is **first-pass review of the agent's own diff**. The main session writes a change. A separate reviewer process reads the diff with none of the author's reasoning and reports findings. The main session checks those findings and then opens the PR.

## Headline result

- The reviewer child is limited by mechanism, not only by instruction. It gets pi's tool allowlist, no `subagent` tool, no forge or git credentials, and the policy with a framing paragraph. Each of these was rerun at this commit and held (`governance.md`, rows C1 to C5).
- The main session is limited mostly by instruction. Its holds, merge rule and "never force-push" are prompt text in `policy.md`. It runs with whatever `gh` login the operator has. Nothing in this repo stops it from merging or closing.
- Of the 26 controls in `governance.md`, 13 are implemented and tested, 6 are implemented but untested here, and 7 exist only as instructions to the model.
- There is no golden-task evaluation set, no budget or timeout on a child, no kill switch in the repo, and no GitHub Agentic Workflow. `limits.md` says so in full.

## Reading order (about two minutes)

1. `DESIGN.md`: the argument, the bill of materials and the AI-use statement.
2. `governance.md`: every control with its `file:line` and status.
3. `limits.md`: what is not done.
4. `answers.md`: the five governance questions.

For depth, the two source documents are `docs/SUBAGENT.md` (what the reviewer tool changes and what "read-only" means) and `docs/PROVENANCE.md` (where the code came from).
