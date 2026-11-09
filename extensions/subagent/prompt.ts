// The child's system prompt, kept free of pi imports so tests/subagent-prompt.test.ts can load it. See docs/SUBAGENT.md.

export const NO_POLLING_LINE =
	"Do not use `sleep` or repeated polling to wait for anything. Long waits are the tick's job: report what you are waiting on and stop.";
export const ROLE_FRAMING =
	"You are a subagent the builder session spawned for one step. Your agent prompt above is your whole role: where you sit in the pipeline, the skill you load, and the session rules you could break. Where it limits your tools, that limit wins.";

// The role prompt from agents/<name>.md, then the two fixed lines, and nothing of the session's Hard rules: each
// agent file carries the rules its role could break, so a read-only scout is not handed merge rules it cannot act on.
export function buildChildPrompt(agentPrompt: string): string {
	return `${[agentPrompt.trim(), NO_POLLING_LINE, ROLE_FRAMING].filter(Boolean).join("\n\n")}\n`;
}
