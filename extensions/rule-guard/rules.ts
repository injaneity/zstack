// The onigiri policy's mechanical bans, enforced on bash commands and file writes instead of trusted to the prompt.
// Pure: the caller passes the repo lookup, so tests run without pi or git. See docs/RULE-GUARD.md.
import * as path from "node:path";
import { commands } from "../tool-guard/find-guard.ts";

/** Repos whose merges belong to someone else (the merge job), from $ONIGIRI_MERGE_OWNED, comma-separated. */
export function mergeOwned(env = process.env.ONIGIRI_MERGE_OWNED ?? ""): Set<string> {
	return new Set(env.split(",").map((r) => r.trim().toLowerCase()).filter(Boolean));
}

/** owner/name of the GitHub repo checked out at a directory, or undefined. */
export type RepoAt = (dir: string) => string | undefined;

// A quoted word under these programs is a command line too: `ssh host "git push -f"`, `bash -c "..."`, `vm "..."`.
const SHELLS = new Set(["ssh", "vm", "bash", "sh", "zsh", "eval"]);
const WRAPPERS = new Set(["sudo", "env", "nohup", "nice", "time", "command", "exec", "timeout", "gtimeout", "xargs", "stdbuf"]);
// Wrapper options that take the next word as their value (`sudo -u bob`, `env -u X`, `nice -n 5`, `timeout -k 5`).
const VALUE_OPTS = new Set(["-u", "-g", "-n", "-k", "-s", "-C", "-S", "-p", "-h", "-D", "-i", "-o", "-e"]);
const HOLDS = "hold-prs.txt";

const base = (w: string) => w.slice(w.lastIndexOf("/") + 1);
const isHolds = (w: string) => base(w) === HOLDS;

/** A simple command with its effective directory (undefined when a `cd` can't be resolved or it runs remotely). */
interface Cmd { words: string[]; cwd: string | undefined }

function simpleCommands(line: string, cwd: string | undefined, depth = 0): Cmd[] {
	const out: Cmd[] = [];
	for (const words of commands(line)) {
		if (words[0] === "cd") {
			const to = words[1] ?? "~";
			cwd = /^[~$]/.test(to) ? undefined : to.startsWith("/") ? path.posix.resolve(to) : cwd && path.posix.resolve(cwd, to);
			continue;
		}
		const w = program(words);
		out.push({ words: w, cwd });
		if (depth < 3 && SHELLS.has(w[0] ?? ""))
			for (const a of w.slice(1)) if (/\s/.test(a)) out.push(...simpleCommands(a, undefined, depth + 1));
	}
	return out;
}

/** The command with wrappers, their options and VAR=value prefixes removed, and the program name reduced to its base. */
function program(words: string[]): string[] {
	let i = 0;
	while (i < words.length) {
		const w = words[i];
		if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(w)) { i++; continue; }
		if (!WRAPPERS.has(base(w))) break;
		i++;
		while (i < words.length && (/^-/.test(words[i]) || /^\d+(\.\d+)?[smhd]?$/.test(words[i]) || /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[i]))) {
			if (VALUE_OPTS.has(words[i]) && i + 1 < words.length) i++;
			i++;
		}
	}
	const w = words.slice(i);
	if (w[0]) w[0] = base(w[0]);
	return w;
}

function gitPush(w: string[]): string | undefined {
	let i = 1;
	let hooksOff = false;
	while (i < w.length && w[i].startsWith("-")) {
		if (w[i] === "-c" && /^core\.hookspath=/i.test(w[i + 1] ?? "")) hooksOff = true;
		i += w[i] === "-C" || w[i] === "-c" ? 2 : 1;
	}
	if (w[i] !== "push") return undefined;
	if (hooksOff) return "it overrides core.hooksPath, which skips the pre-push hook";
	for (const a of w.slice(i + 1)) {
		if (a === "--no-verify") return "`--no-verify` skips the pre-push hook (the patent hold)";
		if (/^--force(-with-lease|-if-includes)?(=|$)/.test(a) || /^-[a-zA-Z]*f[a-zA-Z]*$/.test(a)) return "it force-pushes";
		if (/^\+/.test(a)) return `the refspec \`${a}\` forces the update`;
	}
	return undefined;
}

/** The repo a gh command names: -R/--repo, a PR or run URL, or a repos/<owner>/<name>/ API path. `{owner}` means the cwd's. */
function ghNamedRepo(w: string[]): string | undefined {
	for (let i = 0; i < w.length; i++) {
		const a = w[i];
		if (a === "-R" || a === "--repo") return w[i + 1]?.toLowerCase();
		const m = a.match(/^(?:-R|--repo=)(.+)$/) ?? a.match(/github\.com\/([^/\s]+\/[^/\s#?]+)\/(?:pull|actions)\//) ?? a.match(/(?:^|\/)repos\/([^/]+\/[^/]+)\//);
		if (m && !m[1].includes("{")) return m[1].toLowerCase();
	}
	return undefined;
}

function ghApiWrites(w: string[]): boolean {
	for (let i = 2; i < w.length; i++) {
		const a = w[i];
		const m = a === "-X" || a === "--method" ? w[i + 1] : a.match(/^(?:-X|--method=)(.+)$/)?.[1];
		if (m !== undefined) return m.toUpperCase() !== "GET";
		if (/^(-f|-F|--field|--raw-field|--input)(=|$)/.test(a) || /^-[fF]./.test(a)) return true;
	}
	return false;
}

function ghOwned({ words: w, cwd }: Cmd, owned: Set<string>, repoAt: RepoAt): string | undefined {
	if (!owned.size || w[0] !== "gh") return undefined;
	const act =
		w[1] === "pr" && w[2] === "merge" ? "`gh pr merge`" :
		w[1] === "run" && w[2] === "rerun" ? "`gh run rerun`" :
		w[1] === "api" && w.some((a) => /\/pulls\/[^/]+\/merge(\b|$)/.test(a)) && ghApiWrites(w) ? "a merge through `gh api`" :
		w[1] === "api" && w.some((a) => /\/actions\/(runs|jobs)\/[^/]+\/rerun/.test(a)) ? "a CI rerun through `gh api`" : undefined;
	if (!act) return undefined;
	const repo = ghNamedRepo(w) ?? (cwd === undefined ? undefined : repoAt(cwd));
	if (repo === undefined) return `${act} where the target repo can't be told (no -R, and the directory's remote is unknown); ${[...owned].join(", ")} is merged only by its merge job`;
	return owned.has(repo) ? `${act} in ${repo}: the merge job is its only merger (policy, Merging)` : undefined;
}

/** The words that are not options. `valued` lists options that take the next word. */
function operands(args: string[], valued: Set<string> = new Set()): string[] {
	const out: string[] = [];
	for (let i = 0; i < args.length; i++) {
		if (args[i] === "--") { out.push(...args.slice(i + 1)); break; }
		if (args[i].startsWith("-") && args[i] !== "-") { if (valued.has(args[i])) i++; continue; }
		out.push(args[i]);
	}
	return out;
}

function writesHolds(w: string[]): boolean {
	// redirections: `> f`, `>> f`, `2>f`, `&>> f`, `>|f`
	for (let i = 0; i < w.length; i++) {
		const m = w[i].match(/^(?:\d*|&)>>?\|?(.*)$/);
		if (m && isHolds(m[1] || w[i + 1] || "")) return true;
	}
	const [p, ...all] = w;
	// drop redirections and their targets, so `tee x.bak < hold-prs.txt` reads the file instead of naming it
	const args = all.filter((a, i) => !/^(?:\d*|&)?[<>]/.test(a) && !/^(?:\d*|&)?[<>]{1,2}\|?$/.test(all[i - 1] ?? ""));
	const ops = operands(args, new Set(["-t", "-S", "-m", "-o", "-g", "-e", "--target-directory", "--suffix"]));
	switch (p) {
		case "tee": case "rm": case "unlink": case "truncate": case "chmod": case "chown": case "touch": case "shred":
			return ops.some(isHolds);
		case "sed": case "perl":
			return args.some((a) => /^-[a-zA-Z]*i/.test(a) || a.startsWith("--in-place")) && ops.some(isHolds);
		case "cp": case "mv": case "install": case "rsync": case "ln": case "ditto":
			return !args.some((a) => a === "-t" || a.startsWith("--target-directory")) && ops.length >= 2 && isHolds(ops[ops.length - 1]);
		case "dd":
			return args.some((a) => a.startsWith("of=") && isHolds(a.slice(3)));
	}
	return false;
}

/** The reason to refuse a bash `command` run in `cwd`, or undefined. */
export function checkBash(command: string, owned: Set<string>, repoAt: RepoAt = () => undefined, cwd?: string): string | undefined {
	for (const c of simpleCommands(command, cwd)) {
		if (writesHolds(c.words))
			return "blocked: it writes the holds file. Only Zane edits hold-prs.txt (policy, Work); put the row you want under questions:.";
		const why = c.words[0] === "git" ? gitPush(c.words) : ghOwned(c, owned, repoAt);
		if (why) return `blocked: ${why}. The onigiri policy forbids it; if you think it is needed, put it under questions:.`;
	}
	return undefined;
}

/** The reason to refuse a write or edit to `file`, or undefined. */
export function checkWrite(file: string): string | undefined {
	return isHolds(file) ? "blocked: only Zane edits hold-prs.txt (policy, Work); put the row you want under questions:." : undefined;
}
