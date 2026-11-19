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
2. **Read the queue.** Run `queue.py next`. It reads the repo and prints `claim` (the id of the item to take now, or null with why under `waiting`), `running` (each running item's thread, or null while none is recorded), `cleared` (items whose PR has since merged or closed, now forgotten) and `skipped` (how many items it passed over, by reason).
3. **Free dead threads.** A thread's own turn end frees its slot, so this step is only for a thread that never reached one. Read the status of each thread under `running`; an item whose thread is null is step 4's. For each one that has failed, been interrupted or been cancelled, or that completed its turn and is still listed, run `queue.py release <id>`. A thread that is running or waiting is alive: leave it. Run `queue.py next` again if any was released.
4. **Launch.** An item is launched in three steps, and a pass that stops between two of them loses nothing, because the next pass finishes them here.
   1. Run `queue.py claim <id>`. The item runs from now, and the queue makes its worktree. It prints `worktree`, `claimed` (when) and `brief`.
   2. Start one coding thread on the provider and model `queue.py seat coding` prints (`t3_thread_launch` with `modelSelection.instanceId` and `modelSelection.model`), bound to that worktree (`workspaceStrategy` of type `existing_worktree` with `worktreePath`), titled with the `id`, with the `brief` as its first message.
   3. Run `queue.py attach <id> <thread>`, with the thread's id exactly as the launch returned it, since the queue uses it to tell the thread from its verifier.

   First finish every item under `running` whose thread is null: run `queue.py claim <id>` again, which prints the same answer, and look for a thread titled with that `id` and created after `claimed`. If there is one, attach it and start no other. If there is none, launch one and attach it. Then, while `claim` is not null, take that item through the three steps and run `queue.py next` again. If a claim or an attach is refused, or a thread could not start, stop launching and say why in your reply.
5. **Relay.** Reply with one short list: what was launched, released or cleared, and each `questions:` line from a finished thread's report. The queue itself tells the orchestrator each thread's outcome when its turn ends. Say nothing when nothing changed.

## What is not yours

- **What to work on, in what order, and whether it merges.** `queue.py` decides all three (`model/Queue.tla`). Launch nothing it did not print and skip nothing it did.
- **Holds.** An issue or PR carrying the label `merge.hold_label` names is never started or merged. Only the owner sets or lifts it.
- **The work, its verification and its merge.** A thread's task is its own, and so are having the verifier check its PR and merging it. The verifier records the verdict with `queue.py verified`; you never run that command. Do not redo it, and do not start a second thread on the same item.

## The orchestrator's channel

To give a machine a task that is not an issue or a review request, the orchestrator runs `queue.py add <id> --priority <n>` there, with the task text on stdin. A higher priority goes first, and an item labelled `urgent` gets 100.
