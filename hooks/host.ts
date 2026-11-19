import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { checkBash, type Host, mergeOwned, THREAD_VAR } from "./rules.ts";

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

const QUEUE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../queue.py");
const dump = () => execFileSync("python3", ["-I", QUEUE, "dump"], { encoding: "utf8", timeout: 10000, stdio: ["ignore", "pipe", "pipe"] });
function config(): { merge: { hold_label: string; review_label: string; preapproved_labels: string[] }; people: { review_only: string[] } } | null {
	try {
		return JSON.parse(dump());
	} catch {
		return null;
	}
}
const owned = mergeOwned(dump);
const labels = ((m) => (m ? [m.hold_label, m.review_label, ...m.preapproved_labels] : null))(config()?.merge);
function authorOf(repo: string, n: number): string | undefined {
	try {
		const login = execFileSync("gh", ["api", `repos/${repo}/issues/${n}`, "--jq", ".user.login"], { encoding: "utf8", timeout: 15000, stdio: ["ignore", "pipe", "pipe"] }).trim();
		return /^[\w.\[\]/-]+$/.test(login) ? login : undefined;
	} catch {
		return undefined;
	}
}

function reviewOnly(): ReadonlySet<string> | null {
	const people = config()?.people;
	return people ? new Set(people.review_only.map((x) => x.toLowerCase())) : null;
}
function readText(file: string, dir: string | undefined): string | undefined {
	try {
		return fs.readFileSync(path.resolve(dir ?? ".", home(file)), "utf8");
	} catch {
		return undefined;
	}
}

function gate(repo: string, pr: number, head: string): string | undefined {
	try {
		execFileSync("python3", [QUEUE, "gate", String(pr), head], { encoding: "utf8", timeout: 60000, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, ZSTACK_REPO: repo } });
		return undefined;
	} catch (e) {
		return String((e as { stderr?: unknown }).stderr || "queue: the gate could not be read").trim();
	}
}

export function release(): void {
	try {
		execFileSync("python3", [QUEUE, "release"], { timeout: 60000, stdio: "ignore" });
	} catch {}
}

const host: Host = { gate, labels, repoAt: originRepo, authorOf, reviewOnly, readText, get ghRepo() { return process.env.GH_REPO || undefined; }, thread: process.env[THREAD_VAR] || undefined };

const ROLES: Record<string, string> = { research: "explorer", design: "designer", review: "verifier" };

export function checkDelegate(tool: string, input: { subagent_type?: unknown; role?: unknown; mode?: unknown; target?: { model?: unknown } }): string | undefined {
	if (tool === "Agent" || tool === "Task") {
		const type = String(input.subagent_type ?? "");
		const role = type.replace(/^zstack:/, "");
		return type.startsWith("zstack:") || Object.values(ROLES).includes(type)
			? `blocked: the ${role} runs on its own model, not as a subagent of this one. Start it with the delegate tool, on the model \`queue.py seat ${role}\` prints.`
			: undefined;
	}
	const agent = ROLES[String(input.role ?? "")];
	if (!agent) return undefined;
	let seats: { model: string }[];
	try {
		const entry = JSON.parse(dump()).models[agent];
		seats = Array.isArray(entry) ? entry : [entry];
	} catch {
		return `blocked: the config could not be read, so the ${agent}'s model is unknown.`;
	}
	const model = input.target?.model;
	if (seats.some((x) => x.model === model))
		return input.mode === "wait" ? undefined
			: `blocked: start the ${agent} with mode "wait" and a timeout long enough for its work, and wait for its answer inside this turn. When your turn ends, the queue takes your item as finished.`;
	return seats.some((x) => x.model === model) ? undefined
		: `blocked: the ${agent} runs on ${seats.map((x) => x.model).join(" or ")}, not ${typeof model === "string" ? model : "the model this thread runs on"}. Pass it as target.model.`;
}

export function check(command: string, cwd: string | undefined): string | undefined {
	return checkBash(command, owned, host, cwd, owned);
}
