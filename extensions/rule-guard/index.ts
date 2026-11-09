// rule-guard: blocks the tool calls the onigiri policy bans outright (force-push, skipping the pre-push hook, merging
// or rerunning CI in a repo the merge job owns, writing the holds file). See docs/RULE-GUARD.md.
import { execFileSync } from "node:child_process";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { checkBash, checkWrite, mergeOwned } from "./rules.ts";

function originRepo(dir: string): string | undefined {
	try {
		const url = execFileSync("git", ["-C", dir, "remote", "get-url", "origin"], { encoding: "utf8", timeout: 5000 }).trim();
		return url.match(/github\.com[:/]([^/]+\/[^/]+?)(\.git)?$/)?.[1].toLowerCase();
	} catch {
		return undefined;
	}
}

export default function (pi: ExtensionAPI) {
	const owned = mergeOwned();
	pi.on("tool_call", (event, ctx) => {
		const input = event.input as { command?: unknown; path?: unknown };
		const reason =
			event.toolName === "bash" ? checkBash(String(input.command ?? ""), owned, originRepo, ctx.cwd) :
			event.toolName === "write" || event.toolName === "edit" ? checkWrite(String(input.path ?? "")) : undefined;
		return reason ? { block: true, reason } : undefined;
	});
}
