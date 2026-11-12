// The child's system prompt, kept free of pi imports so tests/subagent-prompt.test.ts can load it. See docs/SUBAGENT.md.

export const NO_POLLING_LINE =
	"Do not use `sleep` or repeated polling to wait for anything. Long waits are the tick's job: report what you are waiting on and stop.";
export const ROLE_FRAMING =
	"You are a subagent the builder session spawned for one step. Your agent prompt above is your whole role: where you sit in the pipeline, the skill you load, and the session rules you could break. Where it limits your tools, that limit wins.";

/** The agent that owns verification. Its seat never falls back to the parent's model: a run that cannot reach it fails. */
export const VERIFIER = "verifier";

/** What to do when an agent's configured seat cannot be used: the verifier refuses, any other agent runs on the parent's model. */
export function onSeatProblem(agent: string): "refuse" | "fallback" {
	return agent === VERIFIER ? "refuse" : "fallback";
}

/** A `{{rule <id>}}` line in an agents/<name>.md stands for that Hard rule, rendered from config/rules.toml by
 * `bin/policy rules <id>`, so an agent quotes a rule without a second copy of its text. */
export const RULE_REF = /^\{\{rule ([a-z]+(?:-[a-z]+)*)\}\}$/gm;

/** The ids an agent prompt quotes, in order. */
export function ruleIds(agentPrompt: string): string[] {
	return [...agentPrompt.matchAll(RULE_REF)].map((m) => m[1]);
}

/** The prompt with each `{{rule <id>}}` line replaced by `render(id)`, one rendered bullet without its newline. */
export function expandRules(agentPrompt: string, render: (id: string) => string): string {
	return agentPrompt.replace(RULE_REF, (_line, id: string) => render(id).replace(/\n$/, ""));
}

// The role prompt from agents/<name>.md, then the two fixed lines, and nothing of the session's Hard rules: each
// agent file carries the rules its role could break, so a read-only scout is not handed merge rules it cannot act on.
export function buildChildPrompt(agentPrompt: string): string {
	return `${[agentPrompt.trim(), NO_POLLING_LINE, ROLE_FRAMING].filter(Boolean).join("\n\n")}\n`;
}
