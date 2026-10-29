---
name: figure-it-out
description: "Design an auditable playbook when no narrower one fits: a large migration, an ambitious multi-part change, or work a human reviews after stepping away. Scales rigor to the task, runs a hypothesis loop, and logs decisions. Use for /skill:figure-it-out or 'figure it out'."
---

# Figure it out

When no playbook fits, design one first. The deliverable before any code is the workflow: phases sized to the risk, run as experiments, with a trail a human can audit later.

## A. Frame

Before the run, state the definition of done as a predicate that can fail, the scope in rough units and effort, and the rigor level. Bias rigor high for one-way doors and wide blast radius. Rigor means gates and artifacts, not trying harder. Show the framing before a multi-hour run.

## B. Design

Split the work into small units that land on their own, riskiest unknown first. Build the verification harness and capture the baseline before changing anything. Run **architect** for one-way-door design decisions only. Write the phase list down, since that is what the human reviews.

## C. Run the loop

Each unit is an experiment: state the hypothesis, make the smallest change, check it against the predicate on the real artifact, and keep or revert. Every unit ends in a check that can fail. When something passes too easily, suspect the check. A verdict is VERIFIED, NOT VERIFIED, or INCONCLUSIVE, and inconclusive is not a pass.

## D. Keep the trail

Log a row per decision with **show-me-your-work** as each unit lands. Work this size usually commits the trail, so the reviewer can read it in the PR.

## E. Hand back

Check the whole against the predicate on the real product. Turn any recurring correction into a lint, check, or script.

**Reply:** the workflow you designed, the rigor level and why, the trail path, what is verified, and what is open.
