---
name: create-verification-skill
description: "Generate a project-local verification skill that drives your app the way a user does, in any language, framework, or platform. Use for /skill:create-verification-skill, \"make a verify skill for this repo\", or when a project has no scripted way to prove UI, CLI, or service behavior."
---

# Create a verification skill

onigiri-mode says verify against the real thing. This skill makes that repeatable for one repo: a project-local skill (`.pi/skills/verify-<app>/`) that launches the real app, exercises a feature the way a user would, and captures evidence. Write it for the next session, not for a human. It will be read cold, mid-task, by a session that has never seen the app.

## 1. Interview the repo, not the user

Answer these from the codebase. Ask the user only what you cannot observe.

- **Surface:** what does a user actually touch? A web UI, a CLI or TUI, a desktop app, an API, a mobile app, a library? A repo can have several. Pick the primary one and note the rest.
- **Run:** how does the app start locally? Prefer the repo's own documented dev command (package scripts, Makefile, README quickstart). Note ports, env vars, seed data, auth.
- **Drive:** how can a session interact with it programmatically? Existing harnesses first: Playwright or Cypress specs, expect scripts, PTY helpers, curl-able endpoints, a debug port. Only then pick a generic recipe: browser or CDP for web and Electron, a tmux or PTY harness for CLI and TUI, plain HTTP for services.
- **Observe:** what evidence can be captured? Screenshots, terminal transcripts, response bodies, logs, exit codes, DB state.
- **Isolate:** can two instances run side by side (ports, data dirs, profiles)? If not, say so in the generated skill. Refusing to double-drive a shared instance beats corrupting the user's session.

If the checkout doesn't build or start as-is, fix that first or report it precisely before generating. A skill written against a broken base teaches wrong steps. When an irrelevant missing asset blocks startup (a static dir the API never serves, a sample config), the generated skill may create it, clearly marked as verification scaffolding, and remove it in cleanup.

## 2. Generate the skill

Write `.pi/skills/verify-<app>/SKILL.md` with YAML frontmatter: `name: verify-<app>` and a `description` that names the app, the surface, and when to reach for it. Without frontmatter the skill never registers. Give it these sections, each grounded in what the interview found, with no placeholders left:

- **Launch:** the exact command that starts the app for verification, and how to tell it's ready (a log line, a prompt). Launch must reach a usable state from an empty data dir, including any migrations and seed data. Ready means usable, not just that a process answers. A database health check can pass while its init scripts are still running. Include teardown. A short-lived CLI or TUI has no server to keep alive: launch means build the binary (or install deps) once, then start each drive in its own isolated PTY or tmux session.
- **Doctor:** one read-only check that answers "is this instance worth driving?": process up, right version or build, port owned by us, auth valid. Run it first whenever anything looks off.
- **Drive:** the harness recipe with real selectors and commands from this repo, not examples. Prefer stable handles (ARIA labels, data attributes, prompt strings, route paths) over coordinates and tab order.
- **Evidence:** what to capture for a proof and where it goes. State the proof standards. Exercise the real user path, not internal setters or test-only endpoints. Capture the action and the resulting state, not just the final screen. Verify side effects (files written, rows inserted, messages sent) alongside what's visible. Mock only where a production boundary already isolates the external system. When the safe path is a dry-run or test mode, observe what it actually skips (files, network, git refs) rather than trusting its name. Some dry-runs still touch the network or open a browser.
- **Cleanup:** how to tear down instances the run created. Never kill by process name. Kill what you started. Cleanup removes instances and scratch state, never the evidence. Proof artifacts survive the teardown, in a location the skill names.
- **Helpers:** any script the skill ships is executable, and its invocation is shown in the skill body. A helper the reader has to reverse-engineer is not a helper.

## 3. Seed the feature map

Create `.pi/skills/verify-<app>/features/README.md` plus one file per user-facing feature you can identify. Aim for the top 3 to 5 to start, from routes, commands, menus, or docs. Follow the shape in [`references/feature-map-example/`](references/feature-map-example/): a README index and one file per feature. Each file answers, from the user's point of view, what the feature is, how to reach it, how to drive it with the harness, and what observable end state proves it works. The four H2s are `Sub-features`, `How to get to it (user POV)`, `Driving it with <harness>`, and `Gotchas`. If the seed data lacks a precondition a journey needs, the journey reports a failure and the reason goes in that feature's `Gotchas`. Never weaken the journey to make it pass. The map is the repo's maintained verification source. A proof that drives one convenient entry point is incomplete when the map lists others.

## 4. Prove the generated skill before handing it over

Run its own instructions end to end once: launch, doctor, drive ONE mapped feature (the map exists so later runs can cover the rest), capture evidence, clean up. After cleanup, confirm the evidence still exists at the named location. A cleanup that eats the proof fails this step. Fix what fails, and run the generated cleanup after every failed iteration too, so broken attempts don't strand processes and ports. A generated skill that was never executed is a draft, not a deliverable. If you proved it in a stand-in for the real target (a container in place of a fresh VM, a local stack in place of a remote one), list every setup step the stand-in did that the target will not. Those steps stay unverified until a run on the real target covers them.

The generated skill lands in the target repo, so ship it as a Change through onigiri-mode's Pull requests playbook.

## 5. Offer the maintenance loop

Point the user at `/skill:maintain-verification-skill` for keeping the map honest as the app changes. Suggest a cadence only if they ask.
