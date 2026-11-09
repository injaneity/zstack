// The onigiri policy's mechanical bans, enforced on bash commands and file writes instead of trusted to the prompt.
// Pure: the caller passes the repo lookup, so tests run without pi or git. See docs/RULE-GUARD.md.
import * as path from "node:path";
import { commands } from "../tool-guard/find-guard.ts";

/** Repos whose merges belong to someone else (the merge job), from $ONIGIRI_MERGE_OWNED, comma-separated. */
export function mergeOwned(env = process.env.ONIGIRI_MERGE_OWNED ?? ""): Set<string> {
	return new Set(env.split(",").map((r) => r.trim().toLowerCase()).filter(Boolean));
}

// A quoted word under these programs is a command line too: `ssh host "git push -f"`, `bash -c "..."`, `vm "..."`.
const SHELLS = new Set(["ssh", "vm", "bash", "sh", "zsh", "eval"]);
const WRAPPERS = new Set(["sudo", "env", "nohup", "nice", "time", "command", "exec", "timeout", "gtimeout"]);

function simpleCommands(line: string, depth = 0): string[][] {
	const out: string[][] = [];
	for (const words of commands(line)) {
		out.push(words);
		const base = words[0]?.slice(words[0].lastIndexOf("/") + 1);
		if (depth < 3 && SHELLS.has(base))
			for (const w of words.slice(1)) if (/\s/.test(w)) out.push(...simpleCommands(w, depth + 1));
	}
	return out;
}

/** The command with wrappers and VAR=value prefixes removed. */
function program(words: string[]): string[] {
	let i = 0;
	while (i < words.length && (WRAPPERS.has(words[i]) || /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[i]) || (i > 0 && /^-|^\d+[smhd]?$/.test(words[i])))) i++;
	const w = words.slice(i);
	if (w[0]) w[0] = w[0].slice(w[0].lastIndexOf("/") + 1);
	return w;
}

function gitPush(w: string[]): string | undefined {
	let i = 1;
	while (i < w.length && w[i].startsWith("-")) {
		if (w[i] === "-c" && /^core\.hookspath=/i.test(w[i + 1] ?? "")) return "it overrides core.hooksPath, which skips the pre-push hook";
		i += w[i] === "-C" || w[i] === "-c" ? 2 : 1;
	}
	if (w[i] !== "push") return undefined;
	for (const a of w.slice(i + 1)) {
		if (a === "--no-verify") return "`--no-verify` skips the pre-push hook (the patent hold)";
		if (/^--force(-with-lease|-if-includes)?(=|$)/.test(a) || /^-[a-zA-Z]*f[a-zA-Z]*$/.test(a)) return "it force-pushes";
		if (/^\+/.test(a)) return `the refspec \`${a}\` force-pushes`;
	}
	return undefined;
}

/** The repo a gh command acts on: -R/--repo, a PR URL, or undefined (the caller looks at the working tree). */
function ghRepo(w: string[]): string | undefined {
	for (let i = 0; i < w.length; i++) {
		const a = w[i];
		if (a === "-R" || a === "--repo") return w[i + 1]?.toLowerCase();
		const eq = a.match(/^(?:-R|--repo=)(.+)$/);
		if (eq) return eq[1].toLowerCase();
		const url = a.match(/github\.com\/([^/\s]+\/[^/\s#?]+)\/(pull|actions)\//);
		if (url) return url[1].toLowerCase();
	}
	return undefined;
}

function ghOwned(w: string[], owned: Set<string>, cwdRepo: () => string | undefined): string | undefined {
	if (!owned.size) return undefined;
	const act =
		w[1] === "pr" && w[2] === "merge" ? "`gh pr merge`" :
		w[1] === "run" && w[2] === "rerun" ? "`gh run rerun`" :
		w[1] === "api" && w.some((a) => /\/pulls\/\d+\/merge\b/.test(a)) ? "a merge through `gh api`" : undefined;
	if (!act) return undefined;
	const api = w[1] === "api" ? w.map((a) => a.match(/repos\/([^/]+\/[^/]+)\/pulls/)?.[1]).find(Boolean) : undefined;
	const repo = api?.toLowerCase() ?? ghRepo(w) ?? cwdRepo();
	if (repo === undefined) return `${act} with no repo named, in a working tree whose GitHub repo is unknown; ${[...owned].join(", ")} is merged only by its merge job`;
	return owned.has(repo) ? `${act} in ${repo}: the merge job is its only merger (policy, Merging)` : undefined;
}

const HOLDS = "hold-prs\\.txt(?![\\w.-])";
const HOLDS_WRITES = [
	new RegExp(`>\\s*["']?[^\\s;&|"']*${HOLDS}`),
	new RegExp(`\\btee\\b[^;&|]*${HOLDS}`),
	new RegExp(`\\b(sed|perl)\\b[^;&|]*\\s-i[^;&|]*${HOLDS}`),
	new RegExp(`\\b(rm|truncate|chmod|chown)\\b[^;&|]*${HOLDS}`),
	new RegExp(`\\b(cp|mv|install|rsync|ln)\\b[^;&|]*\\s["']?[^\\s;&|"']*${HOLDS}["']?\\s*($|[;&|)])`),
];

/** The reason to refuse a bash `command`, or undefined. `cwdRepo` names the working tree's GitHub repo (owner/name). */
export function checkBash(command: string, owned: Set<string>, cwdRepo: () => string | undefined = () => undefined): string | undefined {
	if (HOLDS_WRITES.some((r) => r.test(command)))
		return "blocked: it writes the holds file. Only Zane edits hold-prs.txt (policy, Work); put the row you want under questions:.";
	for (const words of simpleCommands(command)) {
		const w = program(words);
		const why = w[0] === "git" ? gitPush(w) : w[0] === "gh" ? ghOwned(w, owned, cwdRepo) : undefined;
		if (why) return `blocked: ${why}. The onigiri policy forbids it; if you think it is needed, put it under questions:.`;
	}
	return undefined;
}

/** The reason to refuse a write or edit to `file`, or undefined. */
export function checkWrite(file: string): string | undefined {
	return path.basename(file) === "hold-prs.txt"
		? "blocked: only Zane edits hold-prs.txt (policy, Work); put the row you want under questions:."
		: undefined;
}
