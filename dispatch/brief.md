# {id}

{task}

repo: a worktree of `{clone}`

Follow onigiri-mode and its Hard rules, which are already in your context.
No invented limits: a restriction must trace to the owner's words or the Hard rules. If you think one is needed, put it under `questions:` instead of applying it.

When your PR is open and you believe it is done, have the verifier check its head, and wait for its answer inside this turn: do not end your turn while it runs. Put this line in its brief with the PR number and the full head sha filled in: `{env} {queue} verified {id} <pr number> <head sha> pass auto`, `pass review` or `fail`. The verifier runs it as its last step, with those variables, since its session may not have yours; you never do. On a fail, fix the findings, push, and have it check the new head.
On `pass auto`, wait for the PR's checks, then merge that head yourself: `gh pr merge <pr number> -R {repo} --squash --match-head-commit <head sha>`. The guard lets that command through only for the head the verifier passed, with the checks green and no hold; when it refuses, read its reason, and do not try another way. On `pass review`, or when you think the change needs a person whatever the verifier says, do not merge: say why in your report.
When your turn ends, the queue frees your slot and tells the orchestrator what happened, so finish everything before you stop. A turn that tries to end while your open PR's head has no verdict is sent back to get one.

End with a final report: the PR URLs, then
follow-ups:
questions:
