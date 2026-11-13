// rule-guard: blocks the tool calls onigiri-mode's Hard rules ban outright (force-push, skipping the pre-push hook, merging
// or readying in a repo the merge job owns, closing an issue there without a comment naming its PR, writing the holds file). See docs/RULE-GUARD.md.
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { holdsPaths, isHoldsPath } from "./holds.ts";
import { checkBash, checkWrite, type Host, issuesOwned, mergeOwned, readyAllowed } from "./rules.ts";

const home = (p: string) => p.replace(/^~(?=\/|$)/, os.homedir());
const stat = (p: string) => fs.statSync(home(p), { throwIfNoEntry: false });

// Runs only for a gh merge or ready with no repo named, so ordinary calls spawn nothing.
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

// read once at load, like the ready flag: the session's bash can't move the real holds file
const holds = holdsPaths();
// the repo the merge job owns, config/policy.toml merge.repo through bin/policy (the one parser), also once at load
const POLICY = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../bin/policy");
const dump = () => execFileSync("python3", ["-I", POLICY, "dump"], { encoding: "utf8", timeout: 10000, stdio: ["ignore", "pipe", "pipe"] });
const owned = mergeOwned(dump);
// the repo whose issues are closed by hand only with a comment naming the PR that fixed them, issues.repo, the same way
const issues = issuesOwned(dump);
const host: Host = { repoAt: originRepo, isHolds: (p) => isHoldsPath(p, holds), get ghRepo() { return process.env.GH_REPO || undefined; } };

export default function (pi: ExtensionAPI) {
	const ready = readyAllowed();
	// read once, then gone: the session's bash and any pi it starts inherit no grant
	delete process.env.ONIGIRI_READY_ALLOWED;
	pi.on("tool_call", (event, ctx) => {
		const input = event.input as { command?: unknown; path?: unknown };
		const reason =
			event.toolName === "bash" ? checkBash(String(input.command ?? ""), owned, host, ctx.cwd, ready, issues) :
			event.toolName === "write" || event.toolName === "edit" ? checkWrite(String(input.path ?? ""), host, ctx.cwd) : undefined;
		return reason ? { block: true, reason } : undefined;
	});
}
