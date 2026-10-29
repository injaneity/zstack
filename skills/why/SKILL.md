---
name: why
description: "Use for 'why does X work this way', 'why we picked Y', design rationale, regressions, postmortems, or data-backed thresholds. Searches every evidence category that has a tool, then returns a cited, confidence-tiered answer. Use how for runtime behavior."
---

# Why

`how` answers what code does. `why` answers what forces shaped it. Work as a careful investigator: code shows what it does, not why it exists, and the why lives in commits, PRs, tickets, docs, and chat, all incomplete.

## 1. Pin the target and the question

The target is code, a pattern, or a named decision. The question is a rationale, a tradeoff, an edge case, a constraint, or a history sweep. If the target is vague, state your best reading from context and proceed.

## 2. Build the code anchor

Collect file paths and line ranges, key symbols, the commits that touched them, PR numbers, and linked ticket IDs:

```bash
git blame -L <start>,<end> <file>       # last touch per line
git log --follow --oneline -- <file>     # history through renames
git log -S '<exact text>' -- <file>      # commits that added or removed it
git log -1 --format=%B <commit>          # PR number from the message
gh pr view <n> --json title,body,comments,reviews,closingIssuesReferences
```

Also check nearby comments and TODOs, ADRs, tests that encode the edge case, and files changed in the same commits.

## 3. Work the evidence categories

Map each tool you have to one category. Source control is always available.

| Category | Best at |
|---|---|
| Source control (git, `gh`, comments, tests) | Rationale captured during review |
| Issue tracker | The product or business forcing function |
| Long-form docs | Design rationale written before the code |
| Team chat | Deliberation that never reached a doc |
| Observability | Runtime reality: timeouts, retries, rate limits |
| Error tracking | The exceptions behind defensive code |
| Analytics warehouse | Flag-gated code and "where did this number come from" |

Give each category with a real search to a `zstack-explorer` (onigiri-mode's Subagents rules), all at once. Brief it with the question, the anchor, its category, and the note shape below. Do a one-query category yourself. When the code looks defensive (guards, retries, timeouts, flags), also search for incidents: ticket labels like `incident` or `sev-*`, incident channels around the commit dates, and "revert" then "re-apply" commits.

**Note shape per category:** what was searched (queries verbatim), direct evidence (verbatim quote, location, author, date), indirect evidence (with the inference and any other reading), contradictions, gaps, and leads into other categories. Quote, don't summarize. An empty search is a finding.

Skip a category only with a written reason in Sources Consulted: no tool for it (a gap, not a choice), or provably irrelevant.

## 4. Synthesize

Weigh every category's notes yourself. Merge items several categories cite. Show contradictions instead of picking one. Spot-check any citation you're unsure of.

Every claim sits in one tier, and the tier sets its phrasing:

- **Direct.** An author wrote why: a PR body, ticket, comment, doc, or message. State it plainly with the citation.
- **Supported.** Several indirect items converge. Cite each one.
- **Inferred.** A reasonable reading nothing states. Hedge it ("likely", "suggests") and show the chain: given A and B, C.
- **Speculative.** One plausible story among others. Say there is no direct evidence.
- **Unknown.** Say exactly what you searched and that nothing surfaced.

Rules that hold across tiers:
- Never cite code as evidence of its own intent. A change from 50 to 100 shows the change, not the reason.
- Treat a hypothesis in the question as one candidate. Check it, don't confirm it.
- Don't retrofit a clean rationale onto messy history, or read absence of evidence as evidence of absence.
- "Because", "designed to", and "the team decided" need an adjacent citation. Avoid "obviously", "clearly", and "just".
- Don't assume the latest commit is the whole story. Trace back.

## 5. Write it up

- **The Question.** One or two sentences.
- **The Code in Question.** Paths, lines, and symbols.
- **What We Found.** Direct and Supported claims, each cited.
- **What We Can Reasonably Infer.** Inferred claims with their chains. Skip if empty.
- **Competing Hypotheses.** Each with evidence for and against. Skip if one answer is clear.
- **What We Don't Know.** Specific gaps, unavailable sources, and who would know. An empty section is suspicious.
- **Sources Consulted.** One line per category: what was searched, or why it wasn't.
- **Confidence Summary.** One or two sentences.

If the question precedes a change, end with what to Preserve, Change, Avoid, and the Risks.
