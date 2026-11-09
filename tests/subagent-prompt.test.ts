// Tests for extensions/subagent/prompt.ts and the agents/*.md role prompts. Run: node --test tests/subagent-prompt.test.ts
import assert from "node:assert/strict";
import * as fs from "node:fs";
import { test } from "node:test";
import { buildChildPrompt, NO_POLLING_LINE, ROLE_FRAMING } from "../extensions/subagent/prompt.ts";

const root = new URL("..", import.meta.url).pathname;
// the agent body as agents.ts hands it over: everything after the closing frontmatter fence
const body = (name: string) => fs.readFileSync(`${root}agents/${name}.md`, "utf-8").split(/^---$/m).slice(2).join("---");

test("a child's prompt is its role prompt, the no-polling line and the framing line, in that order", () => {
	assert.equal(buildChildPrompt("  role body \n"), `role body\n\n${NO_POLLING_LINE}\n\n${ROLE_FRAMING}\n`);
	assert.equal(buildChildPrompt(""), `${NO_POLLING_LINE}\n\n${ROLE_FRAMING}\n`);
});

for (const name of ["verifier", "explorer", "designer"]) {
	test(`${name} gets its own role prompt and not the session's Hard rules`, () => {
		const prompt = buildChildPrompt(body(name));
		assert.ok(prompt.startsWith(body(name).trim()));
		assert.doesNotMatch(prompt, /^##+ (Hard rules|Merging|Patent hold|Scope)$/m);
	});
}
