# {id}

{task}

repo: a worktree of `{clone}`

Follow onigiri-mode and its Hard rules, which are already in your context.
No invented limits: a restriction must trace to the owner's words or the Hard rules. If you think one is needed, put it under `questions:` instead of applying it.

When your PR is open and you believe it is done, have the verifier check its head. Put this line in its brief with the PR number and the full head sha filled in: `{env} {queue} verified {id} <pr number> <head sha> pass auto`, `pass review` or `fail`. The verifier runs it as its last step, with those variables, since its session may not have yours; you never do. On a fail, fix the findings, push, and have it check the new head. On a pass the queue merges the PR or leaves it to the owner, and the verifier tells you which. Never merge.
If the change needs a person's review whatever the verifier says, run `{queue} raise {id}`.
If you finish with nothing to merge, such as a review with no PR of ours, run `{queue} finished {id}` as your last step.

End with a final report: the PR URLs, then
follow-ups:
questions:
