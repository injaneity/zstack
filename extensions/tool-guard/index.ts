import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

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
}
