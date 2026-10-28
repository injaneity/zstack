# Explainer Template

The shape of the explanation you write. Fill in the placeholders with what you found.

---

You are writing an architectural explanation for a senior engineer who does not know this subsystem. The reader should finish it with a mental model solid enough to start working in the code.

## Original Question

> {QUESTION}

## Your Findings

{FINDINGS}

## Instructions

Your notes came from reading several angles of the same subsystem in turn. They will overlap in places and may contradict where your reading was thin. Reconcile them before writing. Where two angles disagree, go back to the code and settle it rather than presenting both.

Write an explanation a senior engineer unfamiliar with this area could read and walk away confident enough to change it.

## Output Format

Use this structure, adapted to the question. Not every section is needed for every question.

### Overview
1-2 paragraphs. What is this thing, what does it do, why does it exist. Someone should be able to read just this and decide whether to keep reading.

### Key Concepts
The important types, services, or abstractions needed to follow the rest. Brief definitions, not exhaustive.

### How It Works
The core of the explanation, and the longest section. Walk through the flow: what triggers it, what happens step by step, where data goes, what the decision points are.

Use prose, not pseudocode. Reference specific files and functions so the reader knows where to look, but don't dump large code blocks unless a snippet is essential to a point.

When the flow involves multiple components talking to each other, or data transforming through stages, include a diagram. Use mermaid (```mermaid) for structured flows (sequence diagrams, flowcharts, component graphs) or ASCII art for simpler relationships where mermaid would be overkill. Use your judgment. A diagram should clarify, not decorate. If prose covers the flow, skip the diagram.

### Where Things Live
A brief file/directory map. Just the ones someone would need to start working here.

### Gotchas
Non-obvious things, surprising behavior, historical context, pitfalls. Skip this section if there's nothing worth calling out.

## Communication Style

- Use concrete language, not abstractions-about-abstractions
- Say "the `UserService` calls `AuthClient.refresh()`" not "the service delegates to the client"
- When something is complex, explain why it's complex. Don't just describe the complexity
- When something is simple, don't pad it out
- If there's a helpful analogy, use it. If there isn't, don't force one
- Name the open questions you could not settle rather than hiding them
