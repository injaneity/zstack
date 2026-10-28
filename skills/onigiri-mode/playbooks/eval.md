### Eval

**You own the experiment design. Plan, blind, run, synthesize.**

**Non-negotiables for blinding:**

- No `eval`, `test`, `judge`, `experiment`, `rubric`, `score`, `compare`, `benchmark`, or `candidate` in any directory, file, or prompt the candidate sees.
- The candidate prompt looks like an organic user request. State the goal, not the meta.
- No chain-eliciting cues. Don't ask the candidate to list which skills, principles, or files they applied. Ask for design notes generally and grade chain-following from code shape, not self-report.
- Sanitize directory and slug names. Use project-shaped names a user might pick.
- Don't tell the candidate other candidates exist.
- Whoever scores sees outputs by sanitized label only, never by model name. If you score them yourself, score all of them before unmasking.
- Comparing two variants: both sets are scored in a single pass on one scale, blind to which set each came from.

**Steps:**

1. **Frame.** State what variant is under test and what behavior counts as success. Write the rubric (3-6 concrete criteria) for the scoring step only. Hold it back from candidates.
2. **Set up sanitized environments.** Per-candidate working dir with the variant in place. Plant any context an organic task would have: a project skeleton, the skills the candidate would naturally read. Run each candidate in its own session so one candidate's context never leaks into the next.
3. **Author one organic prompt.** What a user would type. No leakage of what's being measured.
4. **Build N candidates in sequence**, each in its own sanitized dir under its own run id. Same prompt to each. Change one thing per candidate so a score difference has one cause (the **explain-the-number** principle skill). A candidate that loses is kept until its scores are recorded, not deleted mid-comparison.
5. **Score blind, then look.** Fill in the rubric for each candidate by its sanitized label before you check which candidate is which. Writing the scores down first is what keeps the read honest, whether you score them yourself or hand the rubric and the labeled outputs to a different model through a single call. Both are fine; not labeling the outputs before scoring is not.
6. **Verify the chain from transcripts, not self-report.** Read each candidate's own session transcript through your transcript tool. Do not read other sessions from unrelated projects. Look at which files each candidate actually opened. Grade chain-following from the files it really read plus the shape of the code, never from the candidate's own claims.
7. **Read every candidate output yourself** end to end. Compare to the scores. A disagreement means the rubric is ambiguous or the scores were written from the label's expectation rather than the output.

**Reply:** variant under test, rubric, per-candidate notes, the scores, your synthesis, and a recommendation for whether to promote the variant.
