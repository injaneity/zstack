# Category Notes Template

The shape of the notes you keep for **one** evidence category while working the `why` skill. Fill it in per category, then write the final answer from all of them. Add the matching `sources/<source>.md` playbook for the category, and `sources/incident-postmortem.md` too when the target code looks defensive (null checks, retry logic, timeout handling, rate limiting, feature flags, egress guards, OOM handlers).

Keep notes, not prose. The write-up comes later, from these.

---

You are establishing the historical context and motivation behind a piece of code, from one source at a time. Stay inside the source you are working. When a document points at another source, record it as a lead and move on; you will work that category when its turn comes.

Work like a **careful, cautious, and precise investigator**. Surface evidence and describe it accurately, including the parts that don't fit a tidy story. Boring and exact beats plausible. One verbatim quote with a precise citation is worth more than a paragraph of summary.

## The Question

> {QUESTION}

## The Code Anchor

**Target files:** {FILES_WITH_LINE_RANGES}

**Key symbols:** {SYMBOLS}

**Initial commits touching this code (most recent first):**
{COMMIT_LIST}

**PR numbers extracted from commit messages:** {PR_NUMBERS}

**Ticket IDs mentioned in commits or PR bodies (if any):** {TICKET_IDS}

## The Source

{SOURCE_NAME}

{SOURCE_PLAYBOOK_SECTION}

## How to Search It

1. **Cast a wide net first** so you don't miss related context. Only then narrow in on specific items.
2. **Read the whole thing.** Read each PR, ticket, doc, or thread fully, not just the title or summary. The key evidence is often in a comment, a subtask, or a follow-up.
3. **Follow links within this source.** Pull the PR or commit a PR references, the parent or sibling of a ticket, the doc a doc links. A cross-source reference becomes a lead for later, not a detour now.
4. **Quote verbatim**, with the exact location (PR number, ticket ID, URL, commit hash, file:line), so the reader can confirm the claim in seconds.
5. **Record what you searched, not just what you found.** Write queries verbatim. An empty search is a finding.
6. **Resist the story.** If two items disagree, record both. If three items line up and a fourth contradicts them, the contradiction is the most interesting finding.

Before recording a finding as strong, ask whether you would expect to find it if your current reading were wrong, and how the evidence would differ. **Never invent.** If you're tempted to round a partial finding up into a confident statement, stop and label it partial.

## Epistemic Discipline

- **Mechanics are not motivation.** A commit changing `limit = 50` to `limit = 100` shows the change, not necessarily why. Look for the reason in the commit message, PR description, linked ticket, or review comments.
- **No intent from code behavior or style.** You may read code to learn what the target *is*. "The author chose a functional approach" is an observation about code, not evidence of intent. Claim intent only where the author stated it.
- **Preserve uncertainty.** Say when evidence is ambiguous, or when one reading is more plausible but not certain. Don't collapse ambiguity to look decisive.
- **No silent substitutions.** If the question is about feature X and you only find evidence about feature Y, don't present Y's evidence as if it answers X.
- **Gather, don't conclude.** Collect the raw material honestly and completely. Don't answer the question yet, and don't pick sides in contradictions. The synthesis step does the reasoning, after every category has been searched.

## Note Format

### Source
Which category these notes cover.

### What I Searched
The queries you ran, items you opened, and places you looked. Be specific. This shows how thorough the search was and what is still unsearched.

### Direct Evidence Found
For each item that explicitly addresses the question:
- **What it says**: verbatim quote or accurate paraphrase
- **Where it's from**: PR #123, ticket ID, doc URL, chat permalink, commit hash, or file:line
- **Author and date** (if available)
- **Relevance**: one sentence on how it bears on the question

### Indirect / Circumstantial Evidence
For each item that bears on the question without answering it:
- **What it is**: brief description
- **Where it's from**: location
- **What it suggests**: what a careful reader might infer, and the inference chain
- **Alternative readings**: if the same evidence could support a different interpretation, note it

### Contradictions
Items that disagree with each other, with both citations.

### Gaps
What you searched for and didn't find, specifically: "Searched the issue tracker for [query] across [time range]. No matching issues."

### Additional Leads
Anything that suggests searching a different category later. For example, a chat thread that a PR mentions.
