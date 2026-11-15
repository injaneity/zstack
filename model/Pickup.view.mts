export const title = "bin/pickup: one scheduled pass";
export const state = (v: any) => {
	const p = v.lock === "NoPass" ? null : v.lock;
	const ph = p ? v.pc[p] : "between passes";
	return ({
		"between passes": v.unsent ? "Between passes, outbox not delivered" : "Between passes",
		collect: "Collecting ended runs into the outbox",
		prune: "Listing old stopped lume workers",
		delete: "Deleting the ones only collected runs name",
		remove: "Deleting under lume-e2e's run.lock",
		drain: "Draining the queue",
		pick: "Picking from GitHub",
		report: "Listing needs-Zane",
		wake: v.failed ? "Pass killed at its deadline" : v.had ? "Has news for the chat" : "Nothing new",
	} as Record<string, string>)[ph] ?? ph;
};
export const group = (v: any) => (v.lock === "NoPass" ? "idle" : v.pc[v.lock] === "wake" ? "wake" : "pass");
export const colors = { idle: "blue", pass: "teal", wake: "yellow" };
export const step = (action: string, from: any, to: any) => {
	const steps: Record<string, string | null> = {
		Lock: "launchd fires, the pass takes the lock",
		Collect: "every ended run's report to the outbox, then collect",
		PruneList: "stopped workers past lume_prune_age_h that only collected runs name",
		PruneLocked: "none to delete, or a lume-e2e run holds run.lock",
		PruneCheck: "run.lock taken, each worker checked still stopped",
		PruneDelete: "each still stopped is deleted, run.lock freed",
		DrainDone: "bin/hourly queue done, free auto slots counted",
		PickDone: "every candidate launched or skipped by its rule, or no slot left",
		Report: "needs-Zane and chat items read",
		Kill: "deadline_s passes: the pass is killed",
		WakeOk: "wake delivered: outbox emptied, items seen",
		WakeFails: "wake not delivered: outbox kept",
	};
	if (action === "End")
		return !from.woke ? "nothing new: no wake, lock freed" : to.unsent ? "wake not delivered: outbox kept, lock freed" : "wake delivered, lock freed";
	return action in steps ? steps[action] : null;
};
