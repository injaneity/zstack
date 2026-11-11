// Tests for extensions/subagent/prompt.ts and the agents/*.md role prompts. Run: node --test tests/subagent-prompt.test.ts
import assert from "node:assert/strict";
import * as fs from "node:fs";
import { test } from "node:test";
import { buildChildPrompt, NO_POLLING_LINE, onSeatProblem, ROLE_FRAMING, VERIFIER } from "../extensions/subagent/prompt.ts";

const root = new URL("..", import.meta.url).pathname;
// the agent body as agents.ts hands it over: everything after the closing frontmatter fence
const body = (name: string) => fs.readFileSync(`${root}agents/${name}.md`, "utf-8").split(/^---$/m).slice(2).join("---");

test("a child's prompt is its role prompt, the no-polling line and the framing line, in that order", () => {
	assert.equal(buildChildPrompt("  role body \n"), `role body\n\n${NO_POLLING_LINE}\n\n${ROLE_FRAMING}\n`);
	assert.equal(buildChildPrompt(""), `${NO_POLLING_LINE}\n\n${ROLE_FRAMING}\n`);
});

test("the verifier is the one agent that verifies; every agent file is one of the three roles", () => {
	const agents = fs.readdirSync(`${root}agents`).filter((f) => f.endsWith(".md")).map((f) => f.slice(0, -3)).sort();
	assert.deepEqual(agents, ["designer", "explorer", "verifier"]);
	const roles = Object.keys(JSON.parse(fs.readFileSync(`${root}config/models.json`, "utf-8")).roles);
	assert.deepEqual(roles.filter((r) => /verif|review/.test(r)), ["verifier"]);
});

test("a verifier whose seat fails refuses; other agents fall back to the parent's model", () => {
	assert.equal(VERIFIER, "verifier");
	assert.equal(onSeatProblem("verifier"), "refuse");
	assert.equal(onSeatProblem("explorer"), "fallback");
	assert.equal(onSeatProblem("designer"), "fallback");
});

for (const name of ["verifier", "explorer", "designer"]) {
	test(`${name} gets its own role prompt and not the session's Hard rules`, () => {
		const prompt = buildChildPrompt(body(name));
		assert.ok(prompt.startsWith(body(name).trim()));
		assert.doesNotMatch(prompt, /^##+ (Hard rules|Merging|Patent hold|Scope)$/m);
	});
}
