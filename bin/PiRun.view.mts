// The chart's words for bin/PiRun.tla: the state a reader sees in `pi-run list`.
export const title = "pi-run: one run of an id";
export const state = (v: any) => {
	if (v.state === "running" && !v.live[v.attempt]) return "lost";
	return v.state === "absent" ? "no run" : v.state;
};
export const step = (action: string) =>
	({ Start: "pi-run <id> <role> <brief>", Exit: "pi exits, supervisor writes rc", Kill: "KILL or OOM", Collect: "pi-run collect <id>", Done: null })[action] ?? action;
