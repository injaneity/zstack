// Tests for extensions/subagent/prompt.ts and the agents/*.md role prompts. Run: node --test tests/subagent-prompt.test.ts
import assert from "node:assert/strict";
import * as fs from "node:fs";
import { execFileSync } from "node:child_process";
import { test } from "node:test";
import { buildChildPrompt, NO_POLLING_LINE, ROLE_FRAMING } from "../extensions/subagent/prompt.ts";

const root = new URL("..", import.meta.url).pathname;
// the agent body as agents.ts hands it over: everything after the closing frontmatter fence
const body = (name: string) => fs.readFileSync(`${root}agents/${name}.md`, "utf-8").split(/^---$/m).slice(2).join("---");
const hardRules = fs.readFileSync(`${root}skills/onigiri-mode/SKILL.md`, "utf-8").split("\n## Hard rules\n")[1].split("\n## ")[0];
// one line from each session-only section; none belongs in a child's prompt
const sessionOnly = [
	"In `trycua/cua` the tick is the only merger.",
	"Holds live in `/srv/onigiri/hold-prs.txt`",
	"spawn the `verifier` subagent on the diff range",
	"No `Co-authored-by` trailers of any kind.",
];

test("the session-only lines the child tests look for are in Hard rules", () => {
	for (const line of sessionOnly) assert.ok(hardRules.includes(line), line);
});

test("a child's prompt is its role prompt, the no-polling line and the framing line, in that order", () => {
	assert.equal(buildChildPrompt("  role body \n"), `role body\n\n${NO_POLLING_LINE}\n\n${ROLE_FRAMING}\n`);
	assert.equal(buildChildPrompt(""), `${NO_POLLING_LINE}\n\n${ROLE_FRAMING}\n`);
});

for (const name of ["verifier", "explorer", "designer"]) {
	test(`${name} gets its role prompt and no session rules`, () => {
		const prompt = buildChildPrompt(body(name));
		assert.match(prompt, new RegExp(`^You are (a|an|the) ${name}\\b`));
		assert.ok(prompt.includes("builder session"), `${name} is told who spawned it`);
		assert.doesNotMatch(prompt, /## Hard rules/);
		for (const line of sessionOnly) assert.ok(!prompt.includes(line), `${name} prompt carries "${line}"`);
	});
}

test("the verifier loads the repo verify skill, runs E2E and Lume, and returns only failing lines", () => {
	const v = buildChildPrompt(body("verifier"));
	for (const s of [".cursor/skills/verify-cua-driver/SKILL.md", ".cursor/skills/verify-cua-spaces/SKILL.md", "fmt, clippy and test", "E2E and Lume", "Never paste full logs", "result <pass|fail>"])
		assert.ok(v.includes(s), s);
	// the rules for the line the parent posts, quoted as the session has them
	for (const s of [
		"The verification comment states only what actually ran and names anything skipped.",
		"Say in the verdict which platform checks could not run.",
		"Bench numbers and timings stay out of commits and PR text.",
	])
		assert.ok(v.includes(s) && hardRules.includes(s), s);
});

test("the designer carries the size and patent-hold rules word for word", () => {
	const d = body("designer");
	for (const s of [
		"Production lines, tests and docs excluded, aim for add<=200 and net<=100. When over, put a one-line size-limit reason in the PR body. No justification block.",
		"The patent hold decides only which repo a change is pushed to. Work on pairing, Teleport and Keyvault normally.",
	])
		assert.ok(d.includes(s) && hardRules.includes(s), s);
});

test("the builder leaves the verify skill, E2E and Lume to the verifier", () => {
	assert.match(hardRules, /You do not run the verify skill, E2E or Lume yourself\./);
	assert.doesNotMatch(hardRules, /run fmt, clippy and test for the touched crates\/projects yourself/);
});

// Everything a builder session reads as instructions: the appended Hard rules (inside SKILL.md), the skill and its
// playbooks, the /onigiri-mode prompt, AGENTS.md and the mini's brief prefix. A sentence there that names the verify
// skill, E2E or Lume, says "yourself", or runs "them", "it" or "those" (a pronoun can point back at the checks) must be
// one of HANDOFF. Each listed sentence gives the checks to the verifier, forbids the builder to run them, or tells the
// builder to do something else itself. Any other such sentence fails, so a new or edited one is reviewed here.
const CHECKS = /\bE2E\b|\blume\b|verify skill|verify-skill|verify-cua|\.cursor\/skills|\byourself\b|\b(re)?run (the |its |every |any )?(\w+ )?(checks?|tests?)\b|\brun (them|those)\b|\brun it\b(?! well)/i;
const HANDOFF = new Set([
	"The `verifier` subagent runs the repo verify skill, with E2E and Lume where that skill calls for them (`agents/verifier.md`).",
	"You do not run the verify skill, E2E or Lume yourself.",
	"The verifier runs the repo verify skill, E2E and Lume.",
	"The verifier is the only place the repo verify skill, E2E and Lume run.",
	"- verify: the `verifier` subagent runs the repo verify skill (Hard rules, Checks).",
	"Linux and Windows platform crates, Linux desktop E2E (KDE, GNOME, X11, Wayland, PipeWire) do not: the verifier says in its verdict which checks could not run here.",
	"- Pruning is allowed on the mini to stay productive: stale lume worker VMs (verifier runs stop them, never delete them), finished sessions' build output and caches.",
	"Never delete live sessions' VMs or worktrees, seeds the verify skills reference, or the cua clone.",
	"A bug's original repro now passes, a refactor's outputs match the old ones, and the verifier runs the verify-skill lanes the change touches.",
	"Install a missing tool yourself.",
	"Then synthesize yourself.",
	"- **Nowhere else.** Do everything else yourself.",
	"Without the tool or the agent, do that step yourself and say so.",
	"- **Bug.** Reproduce it yourself on the real surface, synthesizing the trigger or instrumenting until it fires.",
	"- **Verify against the real thing.** Resolve the repo's tools and the platform, then run it and read the value.",
	"Run it on the machine that does the work, never in the chat.",
	"Run it through **unslop**.",
	"For a conflict, update the branch the way Hard rules' Merging section says and have the verifier rerun the checks.",
	"After a push to an existing PR, the verifier reruns the scoped checks on the new head.",
]);
const builderLeaks = (text: string) =>
	text
		.split(/\n|(?<=[.;!?])\s+/)
		.map((s) => s.trim())
		.filter((s) => CHECKS.test(s) && !HANDOFF.has(s));

test("the leak check catches a builder told to run the checks itself", () => {
	for (const bad of [
		"The verifier is available; run Lume yourself.",
		"Ask the verifier for review and run Lume.",
		"If the verifier is not available, run E2E yourself.",
		"Run the verify-cua-driver skill before opening the PR.",
		"You do not run the verify skill, E2E or Lume yourself. Run them yourself instead.",
		"You do not run the verify skill, E2E or Lume yourself. Then run those.",
		"Run the checks before opening the PR.",
		"Rerun the scoped checks on the new head.",
	])
		assert.notDeepEqual(builderLeaks(bad), [], bad);
});

test("builder prompts leave the verify skill, E2E and Lume to the verifier", () => {
	const sources = ["skills/onigiri-mode/SKILL.md", "prompts/onigiri-mode.md", "AGENTS.md", "hosts/mini/host-mini.md"];
	for (const f of fs.readdirSync(`${root}skills/onigiri-mode/playbooks`)) sources.push(`skills/onigiri-mode/playbooks/${f}`);
	const seen = new Set<string>();
	for (const f of sources) {
		const text = fs.readFileSync(`${root}${f}`, "utf-8");
		assert.deepEqual(builderLeaks(text), [], f);
		for (const s of text.split(/\n|(?<=[.;!?])\s+/)) if (HANDOFF.has(s.trim())) seen.add(s.trim());
	}
	// the Checks hand-off and its ban must stay in the builder's prompt, not just be allowed
	for (const s of ["You do not run the verify skill, E2E or Lume yourself.", [...HANDOFF][0]]) assert.ok(seen.has(s), s);
});

test("the verifier runs on terra, from config/models.json by agent name", () => {
	const roles = JSON.parse(fs.readFileSync(`${root}config/models.json`, "utf-8")).roles;
	assert.deepEqual(roles.verifier, { provider: "openai-codex", model: "gpt-6.1-terra" });
	assert.equal(execFileSync(`${root}bin/model-for`, ["verifier"], { encoding: "utf-8" }).trim(), "openai-codex gpt-6.1-terra");
	assert.ok(fs.existsSync(`${root}agents/verifier.md`), "the seat key is the agent name the subagent tool looks up");
});
