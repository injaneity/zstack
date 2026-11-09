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
// playbooks, the /onigiri-mode prompt, AGENTS.md and the mini's brief prefix. Each sentence (split at . ; : and line
// ends) that names the verify skill, E2E or Lume must give it to the verifier or forbid it, and a "yourself" sentence
// must forbid it, so "the verifier is available; run Lume yourself" fails.
const CHECKS = /\bE2E\b|\blume\b|verify skill|verify-skill|verify-cua|\.cursor\/skills/i;
const NEGATED = /\b(do not|don't|never|not)\b/i;
function builderLeaks(text: string): string[] {
	return text
		.split(/\n|(?<=[.;:!?])\s+/)
		.filter((s) => CHECKS.test(s))
		.filter((s) => (/\byourself\b/i.test(s) ? !NEGATED.test(s) : !/\bverifier\b/i.test(s) && !NEGATED.test(s)));
}

test("the leak check catches a builder told to run the checks itself", () => {
	for (const bad of [
		"The verifier is available; run Lume yourself.",
		"Run the verify-cua-driver skill before opening the PR.",
		"Then run E2E.",
		"Ask the verifier for a review. Run the Lume lane yourself.",
	])
		assert.deepEqual(builderLeaks(bad).length > 0, true, bad);
	for (const ok of ["You do not run the verify skill, E2E or Lume yourself.", "The `verifier` subagent runs the repo verify skill, with E2E and Lume."])
		assert.deepEqual(builderLeaks(ok), [], ok);
});

test("builder prompts leave the verify skill, E2E and Lume to the verifier", () => {
	const sources = ["skills/onigiri-mode/SKILL.md", "prompts/onigiri-mode.md", "AGENTS.md", "hosts/mini/host-mini.md"];
	for (const f of fs.readdirSync(`${root}skills/onigiri-mode/playbooks`)) sources.push(`skills/onigiri-mode/playbooks/${f}`);
	let named = 0;
	for (const f of sources) {
		const text = fs.readFileSync(`${root}${f}`, "utf-8");
		named += text.split("\n").filter((l) => CHECKS.test(l)).length;
		assert.deepEqual(builderLeaks(text), [], f);
	}
	assert.ok(named >= 4, `expected the hand-off lines, saw ${named}`);
});

test("the verifier runs on terra, from config/models.json by agent name", () => {
	const roles = JSON.parse(fs.readFileSync(`${root}config/models.json`, "utf-8")).roles;
	assert.deepEqual(roles.verifier, { provider: "openai-codex", model: "gpt-6.1-terra" });
	assert.equal(execFileSync(`${root}bin/model-for`, ["verifier"], { encoding: "utf-8" }).trim(), "openai-codex gpt-6.1-terra");
	assert.ok(fs.existsSync(`${root}agents/verifier.md`), "the seat key is the agent name the subagent tool looks up");
});
