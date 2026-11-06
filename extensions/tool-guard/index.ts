// tool-guard: refuses bash commands that burn a session's wall clock (see find-guard.ts), and adds the matching
// rules to every session's system prompt, subagent children included. See docs/TOOL-GUARD.md.
import * as os from "node:os";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { checkFind } from "./find-guard.ts";

export const GUIDELINES = [
	"Find a tool with `command -v`, `which -a` or `brew --prefix`, or its known install directory. Never `find /`, and never walk a whole home directory to find one.",
	"Run a long build or test in the background with its output in a log, then block on it once: `wait <pid>`, or a loop that exits when the log shows its end marker or the pid is gone. Never `sleep N; tail` a log to see if it is done.",
	"Measure with a fixed, time-bounded matrix (a set number of runs, each under `timeout`), not a long serial loop in the foreground.",
];

export default function (pi: ExtensionAPI) {
	pi.on("before_agent_start", (event) => {
		const rules = event.systemPromptOptions.promptGuidelines;
		for (const g of GUIDELINES) if (!rules.includes(g)) rules.push(g);
	});
	pi.on("tool_call", (event) => {
		if (event.toolName !== "bash") return undefined;
		const reason = checkFind(String((event.input as { command?: unknown }).command ?? ""), os.homedir());
		return reason ? { block: true, reason } : undefined;
	});
}
