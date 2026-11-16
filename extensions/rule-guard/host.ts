import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { holdsPaths, isHoldsPath } from "./holds.ts";
import { checkBash, checkWrite, type Host, mergeOwned, reviewOnlyLogins } from "./rules.ts";

const home = (p: string) => p.replace(/^~(?=\/|$)/, os.homedir());
const stat = (p: string) => fs.statSync(home(p), { throwIfNoEntry: false });

function originRepo(dir: string): string | null | undefined {
	if (!stat(dir)?.isDirectory()) return undefined;
	try {
		const url = execFileSync("git", ["-C", dir, "remote", "get-url", "origin"], { encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "pipe"] }).trim();
		return url.match(/github\.com[:/]([^/]+\/[^/]+?)(\.git)?$/)?.[1].toLowerCase();
	} catch (e) {
		return /not a git repository/.test(String((e as { stderr?: unknown }).stderr)) ? null : undefined;
	}
}

const holds = holdsPaths();
const POLICY = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../bin/policy");
const dump = () => execFileSync("python3", ["-I", POLICY, "dump"], { encoding: "utf8", timeout: 10000, stdio: ["ignore", "pipe", "pipe"] });
const owned = mergeOwned(dump);
function authorOf(repo: string, n: number): string | undefined {
	try {
		const login = execFileSync("gh", ["api", `repos/${repo}/issues/${n}`, "--jq", ".user.login"], { encoding: "utf8", timeout: 15000, stdio: ["ignore", "pipe", "pipe"] }).trim();
		return /^[\w.\[\]/-]+$/.test(login) ? login : undefined;
	} catch {
		return undefined;
	}
}

const reviewOnly = () => reviewOnlyLogins(holds.map((p) => { try { return fs.readFileSync(home(p), "utf8"); } catch { return undefined; } }));
function readText(file: string, dir: string | undefined): string | undefined {
	try {
		return fs.readFileSync(path.resolve(dir ?? ".", home(file)), "utf8");
	} catch {
		return undefined;
	}
}

const host: Host = { repoAt: originRepo, isHolds: (p) => isHoldsPath(p, holds), authorOf, reviewOnly, readText, get ghRepo() { return process.env.GH_REPO || undefined; } };

export function check(tool: "bash" | "write", target: string, cwd: string | undefined, ready: boolean): string | undefined {
	return tool === "bash" ? checkBash(target, owned, host, cwd, ready, owned) : checkWrite(target, host, cwd);
}
