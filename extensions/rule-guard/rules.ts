// The onigiri policy's mechanical bans, enforced on bash commands and file writes instead of trusted to the prompt.
// Pure: the caller passes the repo lookup, so tests run without pi or git. See docs/RULE-GUARD.md.
import * as path from "node:path";
import { commands } from "../tool-guard/find-guard.ts";

/** Repos whose merges belong to someone else (the merge job), from $ONIGIRI_MERGE_OWNED, comma-separated. */
export function mergeOwned(env = process.env.ONIGIRI_MERGE_OWNED ?? ""): Set<string> {
	return new Set(env.split(",").map((r) => r.trim().toLowerCase()).filter(Boolean));
}

/** What the guard asks of the machine: the GitHub repo (owner/name) checked out at a directory, and whether a path
 * (absolute, or starting with ~) is an existing directory. */
export interface Host { repoAt(dir: string): string | undefined; isDir(p: string): boolean }
const NO_HOST: Host = { repoAt: () => undefined, isDir: () => false };

// A quoted word under these programs is a command line too: `ssh host "git push -f"`, `bash -c "..."`, `vm "..."`.
const SHELLS = new Set(["ssh", "vm", "bash", "sh", "zsh", "eval"]);
const WRAPPERS = new Set(["sudo", "env", "nohup", "nice", "time", "command", "exec", "timeout", "gtimeout", "xargs", "stdbuf"]);
// Wrapper options that take the next word as their value (`sudo -u bob`, `env --unset X`, `nice -n 5`, `timeout -k 5`).
const VALUE_OPTS = new Set(["-u", "-g", "-n", "-k", "-s", "-C", "-S", "-p", "-h", "-D", "-i", "-o", "-e", "-r", "-t", "-U", "-T",
	"--user", "--group", "--unset", "--chdir", "--adjustment", "--signal", "--kill-after", "--prompt", "--host", "--role", "--type",
	"--split-string", "--close-from", "--other-user", "--max-args", "--max-procs", "--delimiter", "--arg-file", "--input", "--output", "--error"]);
// git global options that take the next word as their value.
const GIT_VALUE_OPTS = new Set(["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix", "--exec-path", "--list-cmds", "--attr-source"]);
const HOLDS = "hold-prs.txt";

const base = (w: string) => w.slice(w.lastIndexOf("/") + 1);
const isHolds = (w: string) => base(w) === HOLDS;

/**
 * A simple command, its raw words (env prefixes included) and every directory it might run in. A `cd` may fail
 * (`cd x || gh ...`), so a command keeps the directories from before each `cd` too; undefined is one that can't be
 * told (`cd ~/x`, `cd $X`, or a remote shell).
 */
interface Cmd { words: string[]; raw: string[]; dirs: (string | undefined)[] }

function simpleCommands(line: string, dirs: (string | undefined)[], depth = 0): Cmd[] {
	const out: Cmd[] = [];
	for (const words of commands(line)) {
		if (words[0] === "cd") {
			const to = words[1] ?? "~";
			const last = dirs[dirs.length - 1];
			dirs = [...dirs, /^[~$]/.test(to) ? undefined : to.startsWith("/") ? path.posix.resolve(to) : last && path.posix.resolve(last, to)];
			continue;
		}
		const w = program(words);
		out.push({ words: w, raw: words, dirs });
		if (depth < 3 && SHELLS.has(w[0] ?? ""))
			for (const a of w.slice(1)) if (/\s/.test(a)) out.push(...simpleCommands(a, [undefined], depth + 1));
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

function gitPush(w: string[], raw: string[]): string | undefined {
	let i = 1;
	while (i < w.length && w[i].startsWith("-")) i += GIT_VALUE_OPTS.has(w[i]) ? 2 : 1;
	if (w[i] !== "push") return undefined;
	// -c core.hooksPath=, -ccore.hooksPath=, --config-env=core.hooksPath=..., or GIT_CONFIG_KEY_0=core.hooksPath
	if ([...raw.slice(0, raw.length - w.length), ...w.slice(1, i)].some((a) => /core\.hookspath/i.test(a)))
		return "it overrides core.hooksPath, which skips the pre-push hook";
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
		if (a === "-R" || a === "--repo") return repoName(w[i + 1]);
		const m = a.match(/^(?:-R|--repo=)(.+)$/) ?? a.match(/github\.com\/([^/\s]+\/[^/\s#?]+)\/(?:pull|actions)\//) ?? a.match(/(?:^|\/)repos\/([^/]+\/[^/]+)\//);
		if (m && !m[1].includes("{")) return repoName(m[1]);
	}
	return undefined;
}

/** `[HOST/]OWNER/REPO` or a URL, as gh accepts it, reduced to lower-case owner/repo. */
function repoName(r: string | undefined): string | undefined {
	return r?.toLowerCase().replace(/^(https?:\/\/)?/, "").replace(/\.git$/, "").split("/").filter(Boolean).slice(-2).join("/");
}

function ghApiWrites(w: string[]): boolean {
	let method: string | undefined;
	let fields = false;
	for (let i = 2; i < w.length; i++) {
		const a = w[i];
		const m = a === "-X" || a === "--method" ? w[i + 1] : a.match(/^(?:-X|--method=)(.+)$/)?.[1];
		if (m !== undefined) method = m.toUpperCase();
		if (/^(-f|-F|--field|--raw-field|--input)(=|$)/.test(a) || /^-[fF]./.test(a)) fields = true;
	}
	return method ? method !== "GET" : fields;
}

function ghOwned({ words: w, raw, dirs }: Cmd, owned: Set<string>, repoAt: Host["repoAt"]): string | undefined {
	if (!owned.size || w[0] !== "gh") return undefined;
	const api = w[1] === "api" && ghApiWrites(w);
	const act =
		w[1] === "pr" && w[2] === "merge" ? "`gh pr merge`" :
		w[1] === "run" && w[2] === "rerun" ? "`gh run rerun`" :
		api && w.some((a) => /\/pulls\/[^/]+\/merge(\b|$)/.test(a)) ? "a merge through `gh api`" :
		api && w.some((a) => /\/actions\/(runs|jobs)\/[^/]+\/rerun/.test(a)) ? "a CI rerun through `gh api`" : undefined;
	if (!act) return undefined;
	const env = raw.slice(0, raw.length - w.length).map((a) => a.match(/^GH_REPO=(.+)$/)?.[1]).find(Boolean);
	const named = ghNamedRepo(w) ?? repoName(env);
	// every directory the command might run in counts, so a failed `cd` can't hide an owned checkout
	const repos = named ? [named] : dirs.map((d) => (d === undefined ? undefined : repoAt(d)));
	if (repos.includes(undefined)) return `${act} where the target repo can't be told (no -R, and the directory's remote is unknown); ${[...owned].join(", ")} is merged only by its merge job`;
	const hit = repos.find((r) => owned.has(r as string));
	return hit ? `${act} in ${hit}: the merge job is its only merger (policy, Merging)` : undefined;
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

function writesHolds(w: string[], dir: string | undefined, host: Host): boolean {
	// redirections: `> f`, `>> f`, `2>f`, `&>> f`, `>|f`, and `x>f` (the tokenizer keeps it one word)
	for (let i = 0; i < w.length; i++) {
		const m = w[i].match(/>>?\|?(.*)$/);
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
			// with -t DIR every operand is a source copied into DIR under its own name
			if (args.some((a) => a === "-t" || a.startsWith("--target-directory"))) return ops.some(isHolds);
			// the destination is the file itself, or a directory that receives a source named hold-prs.txt
			if (ops.length < 2) return false;
			const dest = ops[ops.length - 1];
			if (isHolds(dest)) return true;
			if (!ops.slice(0, -1).some(isHolds)) return false;
			const abs = /^[/~]/.test(dest) ? dest : dir === undefined || dest.startsWith("$") ? undefined : path.posix.resolve(dir, dest);
			return dest.endsWith("/") || abs === undefined || host.isDir(abs);
		case "dd":
			return args.some((a) => a.startsWith("of=") && isHolds(a.slice(3)));
	}
	return false;
}

/** The reason to refuse a bash `command` run in `cwd`, or undefined. */
export function checkBash(command: string, owned: Set<string>, host: Host = NO_HOST, cwd?: string): string | undefined {
	for (const c of simpleCommands(command, [cwd])) {
		if (writesHolds(c.words, c.dirs[c.dirs.length - 1], host))
			return "blocked: it writes the holds file. Only Zane edits hold-prs.txt (policy, Work); put the row you want under questions:.";
		const why = c.words[0] === "git" ? gitPush(c.words, c.raw) : ghOwned(c, owned, (d) => host.repoAt(d));
		if (why) return `blocked: ${why}. The onigiri policy forbids it; if you think it is needed, put it under questions:.`;
	}
	return undefined;
}

/** The reason to refuse a write or edit to `file`, or undefined. */
export function checkWrite(file: string): string | undefined {
	return isHolds(file) ? "blocked: only Zane edits hold-prs.txt (policy, Work); put the row you want under questions:." : undefined;
}
