
export const NO_POLLING_LINE =
	"Do not use `sleep` or repeated polling to wait for anything. Long waits are the tick's job: report what you are waiting on and stop.";
export const ROLE_FRAMING =
	"You are a subagent the builder session spawned for one step. Your agent prompt above is your whole role: where you sit in the pipeline, the skill you load, and the session rules you could break. Where it limits your tools, that limit wins.";

export const VERIFIER = "verifier";

export function onSeatProblem(agent: string): "refuse" | "fallback" {
	return agent === VERIFIER ? "refuse" : "fallback";
}

export const RULE_REF = /^\{\{rule ([a-z]+(?:-[a-z]+)*)\}\}$/gm;

export function ruleIds(agentPrompt: string): string[] {
	return [...agentPrompt.matchAll(RULE_REF)].map((m) => m[1]);
}

export function expandRules(agentPrompt: string, render: (id: string) => string): string {
	return agentPrompt.replace(RULE_REF, (_line, id: string) => render(id).replace(/\n$/, ""));
}

export function buildChildPrompt(agentPrompt: string): string {
	return `${[agentPrompt.trim(), NO_POLLING_LINE, ROLE_FRAMING].filter(Boolean).join("\n\n")}\n`;
}
