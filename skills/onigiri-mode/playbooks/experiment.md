### Experiment

**You own the question. Build the cheapest thing that answers it, then throw it away.**

Two shapes. A design question gets a throwaway sketch you look at. A behavior question about a skill, a prompt, or a structure gets a blind comparison you score. Both end in a decision, never in shipped code. The real build is Feature.

**A design question.** The prototype is an instrument, not a draft. Speed over polish, no tests, no abstractions. This is the one place where smallest change and the verification bar invert.

1. Name the decision it exists to make. Which layout, which interaction, which density, which behavior. No decision means no prototype, so route to Feature.
2. Gather references when the design space is open. Prior art, a moodboard of themes and layouts, a few directions for the user to pick from. Skip when the direction is set.
3. Build it throwaway in a scratch dir outside production source. Vanilla HTML, CSS, and JS, or the lightest stack that renders the idea, with a dev server and hot reload. For a behavioral or timing question, the smallest script that exercises it.
4. When comparing alternatives, build them behind one switcher, each labeled. This is the **principle-exhaust-the-design-space** skill made cheap.
5. Look at it, decide, write the decision down, then delete the prototype.

**A behavior question.** Whether a skill, prompt, or structure change actually changes what the agent does.

6. Frame the variant under test and what counts as success. Write the rubric, 3 to 6 concrete criteria, for the scoring step only, and hold it back from the candidates.
7. Sanitize. No `eval`, `test`, `judge`, `experiment`, `rubric`, `score`, `compare`, `benchmark`, or `candidate` in any directory, file, or prompt a candidate sees. Use project-shaped names a user might pick. The prompt is what a user would type, stating the goal and never the meta, with no chain-eliciting cue that asks it to list the skills or principles it used. Never tell a candidate that others exist.
8. One candidate per sanitized dir and per session, so no context leaks between them. Change one thing per candidate, so a score difference has one cause (the **principle-explain-the-number** skill).
9. Score blind. Fill the rubric per sanitized label before checking which candidate is which. If you score them yourself, score all of them before unmasking.
10. Verify chain-following from the session transcript, never from self-report. pi writes them under `~/.pi/agent/sessions/`. Look at which files the candidate actually opened, and grade from that plus the shape of the code.
11. Read every output yourself end to end against its score. A disagreement means the rubric is ambiguous or the score was written from the label's expectation.

**Reply:** the question, what you built or ran, per-candidate notes and scores, your decision and why, and whether to promote it.
