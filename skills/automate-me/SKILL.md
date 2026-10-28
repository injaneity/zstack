---
name: automate-me
description: "Use for \"automate me\", \"create/update/refresh my mode skill\", \"turn/capture my preferences or working style into a skill\", or wanting the agent to follow how the user works. Drafts or revises the mode skill from evidence in recent transcripts plus what the user says, and applies unslop."
---

# Automate me

A guided flow for turning the user's working conventions into a skill the agent will follow. In zstack the output is **onigiri-mode**, the one mode skill, kept in this fork at `skills/onigiri-mode/SKILL.md`.

This skill sequences two others: an inline mining pass (step 1) and the **unslop** skill (step 5). It doesn't replace them.

## Flow

### 0. Read the existing skill

Read `skills/onigiri-mode/SKILL.md` before anything else. On a repeat run, that file is the base: you are revising it, not starting over. Confirm intent with the user only when it isn't already clear from what they said.

Revise mode changes the rest of the flow:
- Step 1 mines only history since the skill was last edited.
- Step 2 asks what has changed or is missing, not what to capture from zero.
- Step 4 edits the file in place. Preserve sections the user hasn't contradicted. Revise ones with new evidence. Add a section only for a genuinely new rule.

### 1. Mine the history

Read the transcripts yourself, in this session. There is no fan-out. Use `ReadTranscript` over this agent's recent sessions, oldest slice first, and keep a running note of every candidate pattern with the session it came from. Work one slice at a time and stop when a slice adds nothing new.

Hunt for these signals:

- Response preferences (length, tone, format, "dumb it down" corrections)
- Delegation habits (what gets handed off, what stays in one thread)
- Verification posture (what "done" means, unit tests vs live repro, who reviews)
- Code and prose discipline (style, principles cited, lint/format tools)
- Process conventions (worktrees, commits, PRs, review/merge tooling)
- Meta preferences (fixing skills mid-task, proposing new ones)

Cross-check across slices before elevating a signal. Patterns seen in two or more slices are high-confidence. A lone signal is weak and usually gets dropped.

### 2. Ask the user directly

Mining misses intent that hasn't come up yet. Ask with a question card of four to six real options, multi-select for category questions, rather than asking the user to type from scratch.

Start broad ("Which areas matter most?"), then follow up on the selected areas with specific options. After the structured rounds, one free-form question catches anything the options missed.

Don't dump 20 questions.

### 3. Cluster findings

Group the combined signals into sections. Use only what applies:

- **Response style**: length, tone, format.
- **Autonomy**: how much to do without asking.
- **Understand first**: which skills to reach for when scoping or investigating a change.
- **One thread**: how work is sequenced, what never gets handed off.
- **Prose / code discipline**: principles, lint tools, style guides.
- **Review and verify**: repro posture, verification skills, live-testing tools.
- **Process**: git worktrees, commits, PRs, review/merge tooling.
- **Skills**: skill-authoring habits, fix-the-skill-first, proposing new skills.

The current onigiri-mode shows the shape and the granularity. Don't copy its content into a new skill; it is the file you are editing.

### 4. Draft the skill

Edit `skills/onigiri-mode/SKILL.md` in place. Follow the **Authoring or modifying a skill** playbook in `playbooks/authoring-a-skill.md` for the writing rules.

- Frontmatter `description`: trigger on the mode name and on "work in my style", not on generic keywords like "write code" or "review PR".
- Keep `description` as one YAML scalar, quoted or `>-` if it wraps.

### 5. Iterate on prose

Apply the **unslop** skill to every line. Show the draft to the user and take feedback. Expect multiple iterations. Cut ruthlessly. A mode skill is not a manual.

### 6. Land it

Commit and open a PR. Don't push to main directly. Run the **Opening a PR** playbook.

## Guardrails

- **Don't overfit to one conversation.** A preference stated once and contradicted another time is noise. Require multiple instances before codifying it.
- **Don't be clever.** Restating other skills' contents, inventing metaphors, or writing "poetic" prose for an agent reader is cost without benefit. Keep it operational.
- **Reference, don't inline.** Other skills appear as path references, not pasted excerpts.
- **Keep sections minimal.** Only add a section if the user has a specific, non-default rule there. "Communicate clearly" is not a section.
- **Name conventions generic.** Use "the user" in imperatives, not the author's first name.
- **Don't force symmetry.** If the user has no process rules worth writing down, skip that section entirely.

## Evaluation

A mode skill is subjective output. A test-and-iterate benchmark loop isn't useful here. Check with the user: does it read like them? Did it miss anything? Then ship.

## When not to use

- The user wants a task-specific skill rather than working conventions: author it directly with the **Authoring or modifying a skill** playbook, no mining needed.
- The user wants one narrow workflow captured (e.g. "how I write commit messages"). That's a regular skill, not a mode skill.
