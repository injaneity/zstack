export const title = "zstack, consolidated: a piece of work from the chat to merged";
export const state = (v: any) => {
  switch (v.phase) {
    case "queued": return "Queued";
    case "running": return "pi session working";
    case "failed": return v.woke ? "Failed, chat woken" : "Killed, wake at collect";
    case "done": return "Done, no trycua/cua PR";
    case "open": return v.fixing ? "Ready PR, its thread fixing red CI" : v.draft ? "Draft PR" : "Ready PR";
    case "inqueue": return v.byJob ? "In merge queue, job's entry" : "In merge queue, someone else's entry";
    case "merged": return v.by === "job" ? "Merged by the job" : "Merged outside the job";
  }
  return v.phase;
};
export const group = (v: any) =>
  v.phase === "queued" ? "filing"
  : v.phase === "running" ? "run"
  : ["failed", "done"].includes(v.phase) ? "ended"
  : v.phase === "merged" ? "merged"
  : "pr";
export const colors = { filing: "blue", run: "teal", ended: "tan", pr: "purple", merged: "yellow" };
const steps: Record<string, string | null> = {
  Drain: "hourly drains the queue (a 429'd run once resume_at passed, same session)",
  StartNow: "pi-run starts it now",
  Limit: "upstream 429: frees its slot, resume_at set",
  ResumeAtPasses: null,
  ExitFailed: "pi exits non-zero, failed wake",
  Killed: "KILL or OOM",
  Collect: "pickup collects, failed wake",
  Retry: "chat files it again under a fresh id",
  ExitOk: "pi exits 0",
  CiFinishes: null,
  Push: "a push (new head)",
  DeclareTier: null,
  MainMoves: null,
  Ready: "Zane or merge: allowed readies it",
  ZaneHolds: null,
  ZanePreapproves: null,
  JobEnqueues: "job pass: gate green, gh pr merge",
  JobReruns: null,
  JobDequeues: "job pass: its entry fails the gate, dequeue",
  JobLogsMerged: null,
  JobAlerts: null,
  OtherEnqueues: "someone else queues it",
  QueueMerges: "queue merges",
  QueueDrops: "queue drops it",
  OtherMerges: "someone else merges it",
  CiReopen: "red after the rerun: the hourly reopens the thread that pushed it",
  ThreadFixes: "the thread pushes its fix, or exits with questions",
  Ends: null,
};
export const step = (action: string) => (action in steps ? steps[action] : action);
