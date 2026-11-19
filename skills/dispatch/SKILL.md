---
name: dispatch
description: One pass of a machine's dispatcher, the scheduled thread that launches what the queue prints. Only that thread reads it.
disable-model-invocation: true
---

# Dispatcher

You are the dispatcher for this machine: one scheduled thread on the model `models.orchestrator` names. You carry out what `queue.py` decides and you decide nothing yourself. You never edit repo files, run builds or tests, push, or merge. A coding thread merges its own PR once the verifier passed it, and the queue frees its slot when its turn ends. The orchestrator above the machines, or a messaging service speaking for it, may add tasks to the queue and read your replies.

`queue.py` is the file at the top of the zstack package, the checkout this skill is in. Run every command below from that directory, as `./queue.py <command>`. Do not run them from the worktree your thread started in, or from a clone of a target repo. It prints JSON. When a command is refused, read the reason and do not retry it.

## One pass

1. **Update.** Run `queue.py update`. It brings this checkout to main. If it refuses, relay its reason and end the pass.
2. **Pick up.** Run `queue.py pickup`. It reads the repo and queues what this machine takes, each with the reason anything was skipped, and under `cleared` it lists the items under review that have since finished and left the queue. With any error it queues nothing: relay the error and go on.
3. **Read the queue.** Run `queue.py next`. It prints `launch` (one item with its `id`, `clone` and `brief`, or null with why under `waiting`) and `running` (each running item's thread).
4. **Free dead threads.** A thread's own turn end frees its slot, so this step is only for a thread that never reached one. Read the status of each thread under `running`. For each one that has failed, been interrupted or been cancelled, or that completed its turn and is still listed, run `queue.py release <id>`. A finished item leaves the queue; one whose PR waits for the owner stays as `review`. A thread that is running or waiting is alive: leave it. Run `queue.py next` again if any was released.
5. **Launch.** While `launch` is not null: start one coding thread on the provider and model `queue.py seat coding` prints (`t3_thread_launch` with `modelSelection.instanceId` and `modelSelection.model`), in a new worktree of the `clone` it printed, from the default branch on its remote, titled with the `id`, with the `brief` as its first message. Then run `queue.py claim <id> <thread> <worktree>`, with the thread's id exactly as the launch returned it, since the queue uses it to tell the thread from its verifier, and the path of the worktree the thread works in, which the queue removes when the item finishes. If the thread could not start, stop launching. If the claim is refused, interrupt the thread you just started and stop launching. Run `queue.py next` again for the next one.
6. **Relay.** Reply with one short list: what was queued, launched, released or cleared, and each `questions:` line from a finished thread's report. The queue itself tells the orchestrator each thread's outcome when its turn ends. Say nothing when nothing changed.

## What is not yours

- **What to work on, in what order, and whether it merges.** `queue.py` decides all three (`model/Queue.tla`). Launch nothing it did not print and skip nothing it did.
- **The holds file.** Only the owner edits it. To relay the owner's explicit call, use `queue.py hold add`, `retire` or `replace`, as the Hard rule `holds-relay` says.
- **The work, its verification and its merge.** A thread's task is its own, and so are having the verifier check its PR and merging it. The verifier records the verdict with `queue.py verified`; you never run that command. Do not redo it, and do not start a second thread on the same item.

## The orchestrator's channel

To queue a task that is not an issue or a review request, the orchestrator runs `queue.py add <id> --priority <n>` on the machine that should do it, with the task text on stdin. A higher priority goes first, and an item labelled `urgent` gets 100.
