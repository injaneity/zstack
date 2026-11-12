// The reader's words for Hourly.tla: one hourly pickup pass after another.
export const title = "zstack hourly pickup: a pass that could pick never ends silently";
const TOTAL = 7, AUTO = 5;   // config/policy.toml [slots], as Hourly.cfg sets them
// Hourly.tla's CouldPick: an auto slot known free (none while the peer is unreachable) and a Todo no rule blocks.
const could = (v: any) => {
  const seenAuto = v.auto + (v.blind ? 0 : v.peer);
  const free = v.blind ? 0 : Math.max(0, Math.min(TOTAL - (seenAuto + v.chat + v.quick), AUTO - seenAuto));
  return free > 0 && (v.todo as string[]).some((i) => v.blocker[i] === "none");
};
export const state = (v: any) => {
  switch (v.pc) {
    case "idle": return could(v) ? "Between passes, a Todo is pickable" : "Between passes, nothing to pick";
    case "skipped": return "Pass skipped on the fingerprint";
    case "collect": return "Collecting ended runs";
    case "drain": return "Draining the chat queue";
    case "pick": return "Picking Linear Todos";
    case "end": return (v.rows as any[]).some((r) => r[1] === "todo") ? "Pass ended, Todos declined as a group" : "Pass ended";
  }
  return v.pc;
};
export const group = (v: any) => (v.pc === "idle" ? "between" : v.pc === "skipped" ? "silent" : "pass");
export const colors = { between: "blue", silent: "orange", pass: "teal" };
const steps: Record<string, string | null> = {
  PassStarts: "the hourly pass starts",
  Collects: "it collects ended runs",
  Drains: "it launches a queued run",
  DrainDone: "the queue is drained or the total is full",
  Picks: "it launches a Todo",
  Skips: "it skips a Todo with one of the five rules",
  Declines: "the chat declines the whole list in one row",   // the main-4e08648 mutants only
  PickDone: "auto slots full, or every Todo launched or skipped",
  Done: "bin/hourly done stores the fingerprint",
  StaysSilent: "the pass stops with no row",
  Changes: "a hold, an answer from Zane, a PR or a run changes",
  Files: "the chat files a Todo",
  Moves: "a Todo leaves Todo",
  RunEnds: "a run exits",
  ChatQueues: "the chat queues a run",
  ChatStartsNow: "the chat starts a run itself (outside the cap)",
  PeerFlips: "the peer stops or starts answering",
};
export const step = (action: string) => (action in steps ? steps[action] : action);
