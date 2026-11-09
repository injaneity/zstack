// The chart's words for bin/PiRun.tla: the state a reader sees in `pi-run list`, and the wake an exit sent.
export const title = "pi-run: one run of an id";
export const state = (v: any) => {
	if (v.state === "running" && !v.live[v.attempt]) return "lost";
	if (v.state === "exited") return { none: "exited, no wake", done: "exited, done wake", failed: "exited, failed wake" }[v.wake[v.attempt] as string];
	return v.state === "absent" ? "no run" : v.state;
};
export const step = (action: string, from: any, to: any) => {
	if (action === "Exit") {
		const w = to.wake[to.attempt];
		return w === "failed" ? "pi exits rc != 0" : w === "done" ? "pi exits rc 0 [brief has wake: finish]" : "pi exits rc 0 [no wake: finish]";
	}
	return ({ Start: "pi-run <id> <role> <brief>", Kill: "KILL or OOM", Collect: "pi-run collect <id>", Done: null } as Record<string, string | null>)[action] ?? action;
};
