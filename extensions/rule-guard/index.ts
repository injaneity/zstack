// rule-guard: blocks the tool calls onigiri-mode's Hard rules ban outright (force-push, skipping the pre-push hook, merging
// or rerunning CI in a repo the merge job owns, writing the holds file). See docs/RULE-GUARD.md.
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { checkBash, checkWrite, type Host, MERGE_OWNED, readyAllowed } from "./rules.ts";

const home = (p: string) => p.replace(/^~(?=\/|$)/, os.homedir());
const stat = (p: string) => fs.statSync(home(p), { throwIfNoEntry: false });

// Runs only for a gh merge, rerun or ready with no repo named, so ordinary calls spawn nothing.
function originRepo(dir: string): string | null | undefined {
	if (!stat(dir)?.isDirectory()) return undefined;
	try {
		const url = execFileSync("git", ["-C", dir, "remote", "get-url", "origin"], { encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "pipe"] }).trim();
		return url.match(/github\.com[:/]([^/]+\/[^/]+?)(\.git)?$/)?.[1].toLowerCase();
	} catch (e) {
		// not a git checkout: gh has no repo to act on there, so no owned repo either
		return /not a git repository/.test(String((e as { stderr?: unknown }).stderr)) ? null : undefined;
	}
}

const host: Host = { repoAt: originRepo, isFile: (p) => stat(p)?.isFile() ?? false, get ghRepo() { return process.env.GH_REPO || undefined; } };

export default function (pi: ExtensionAPI) {
	const ready = readyAllowed();
	// read once, then gone: the session's bash and any pi it starts inherit no grant
	delete process.env.ONIGIRI_READY_ALLOWED;
	pi.on("tool_call", (event, ctx) => {
		const input = event.input as { command?: unknown; path?: unknown };
		const reason =
			event.toolName === "bash" ? checkBash(String(input.command ?? ""), MERGE_OWNED, host, ctx.cwd, ready) :
			event.toolName === "write" || event.toolName === "edit" ? checkWrite(String(input.path ?? "")) : undefined;
		return reason ? { block: true, reason } : undefined;
	});
}
