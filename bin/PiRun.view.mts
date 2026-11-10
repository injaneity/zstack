// The chart's words for bin/PiRun.tla: the state a reader sees in `pi-run list`, and the wake an exit sent.
export const title = "pi-run: one run of an id";
export const state = (v: any) => {
	if (v.state === "running" && !v.live) return "lost";
	if (v.state === "exited") return { none: "exited, no wake", done: "exited, done wake", failed: "exited, failed wake" }[v.wake as string];
	if (v.state === "queued") return v.cont ? "queued after a 429" : "queued";
	return v.state === "absent" ? "no run" : v.state;
};
export const step = (action: string, from: any, to: any) => {
	if (action === "Exit") {
		const w = to.wake;
		return w === "failed" ? "pi exits rc != 0" : w === "done" ? "pi exits rc 0 [brief has wake: finish]" : "pi exits rc 0 [no wake: finish]";
	}
	if (action === "Collect") return from.state === "running" ? "pi-run collect <id> [sends the failed wake]" : "pi-run collect <id>";
	return ({ Queue: "pi-run queue <id>", Start: "pi-run <id> <role> <brief>, or the hourly drain", Kill: "KILL or OOM",
		Limit: "pi stops on an upstream 429 [frees its slot]", Resume: "hourly drain, after resume_at [same session]", Done: null } as Record<string, string | null>)[action] ?? action;
};
