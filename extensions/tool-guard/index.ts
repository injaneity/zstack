// tool-guard: adds three wall-clock rules to every session's system prompt, subagent children included. They are
// prompt rules, not blocks. It also gives a bash call that declares no timeout config/policy.toml run.idle_min, less
// the supervisor's one-minute check, as its timeout: a call blocked on a process that never ends then returns to the
// session with an error the model can act on, a minute before pi-run's supervisor would stop the whole run for showing
// no progress.
import { execFileSync } from "node:child_process";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export const GUIDELINES = [
	"Find a tool with `command -v`, `which -a` or `brew --prefix`, or its known install directory. Never `find /`, and never walk a whole home directory to find one.",
	"Run a long build or test in the background with its output in a log, then block on it once: `wait <pid>`, or a loop that exits when the log shows its end marker or the pid is gone. Never `sleep N; tail` a log to see if it is done.",
	"Measure with a fixed, time-bounded matrix (a set number of runs, each under `timeout`), not a long serial loop in the foreground.",
];

const POLICY = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../bin/policy");

/** run.idle_min in seconds less the supervisor's one-minute check, through bin/policy (the one parser), or undefined when it cannot be read. */
export function idleSeconds(get: () => string = () => execFileSync("python3", ["-I", POLICY, "get", "run.idle_min"],
	{ encoding: "utf8", timeout: 10000, stdio: ["ignore", "pipe", "pipe"] })): number | undefined {
	try {
		const n = Number(get().trim());
		return Number.isInteger(n) && n > 1 ? (n - 1) * 60 : undefined;
	} catch {
		return undefined;
	}
}

/** Give a bash call's input the idle limit as its timeout when it declares none. */
export function boundBash(input: { timeout?: unknown }, seconds: number | undefined): void {
	if (seconds && (typeof input.timeout !== "number" || !(input.timeout > 0))) input.timeout = seconds;
}

export default function (pi: ExtensionAPI) {
	const seconds = idleSeconds();
	pi.on("before_agent_start", (event) => {
		const rules = event.systemPromptOptions.promptGuidelines;
		for (const g of GUIDELINES) if (!rules.includes(g)) rules.push(g);
	});
	pi.on("tool_call", (event) => {
		if (event.toolName === "bash") boundBash(event.input as { timeout?: unknown }, seconds);
	});
}
