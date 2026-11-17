# {id}

{task}

repo: a worktree of `{clone}`

Follow onigiri-mode and its Hard rules, which are already in your context.
No invented limits: a restriction must trace to the owner's words or the Hard rules. If you think one is needed, put it under `questions:` instead of applying it.

When your PR is open and you believe it is done, run `{queue} ready {id} <pr number>` and wait: the dispatcher has the verifier check that head and sends you its findings or lets it merge. Do not run the verifier for the merge yourself, and never merge.
If the change needs a person's review whatever the verifier says, run `{queue} raise {id}`.

End with a final report: the PR URLs, then
follow-ups:
questions:
