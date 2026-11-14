import * as path from "node:path";
import { fold } from "./holds.ts";
import { base, commands, program } from "./shell.ts";

export type Owned = ReadonlySet<string> | null;

export function mergeOwned(dump: () => string): Owned {
	try {
		const repo = JSON.parse(dump())?.merge?.repo;
		return typeof repo === "string" && /^[^/\s]+\/[^/\s]+$/.test(repo) ? new Set([repo.toLowerCase()]) : null;
	} catch {
		return null;
	}
}

export function issuesOwned(dump: () => string): Owned {
	try {
		const repo = JSON.parse(dump())?.issues?.repo;
		return typeof repo === "string" && /^[^/\s]+\/[^/\s]+$/.test(repo) ? new Set([repo.toLowerCase()]) : null;
	} catch {
		return null;
	}
}

export function readyAllowed(env = process.env.ONIGIRI_READY_ALLOWED ?? ""): boolean {
	return env === "1";
}

export interface Host {
	repoAt(dir: string): string | null | undefined;
	isHolds(p: string): boolean;
	ghRepo?: string;
	authorOf?(repo: string, n: number): string | undefined;
	reviewOnly?(): ReadonlySet<string> | null;
}
const NO_HOST: Host = { repoAt: () => undefined, isHolds: () => false };

export function reviewOnlyLogins(texts: (string | undefined)[]): ReadonlySet<string> | null {
	if (!texts.some((t) => t !== undefined)) return null;
	const out = new Set<string>();
	for (const line of texts.join("\n").split("\n")) {
		const f = line.split("|").map((x) => x.trim());
		if (!line.trimStart().startsWith("#") && f[0] === "review-only" && f[1]) out.add(f[1].toLowerCase());
	}
	return out;
}

const SHELLS = new Set(["ssh", "vm", "bash", "sh", "zsh", "eval"]);
const GIT_VALUE_OPTS = new Set(["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix", "--exec-path", "--list-cmds", "--attr-source"]);
const HOLDS = "hold-prs.txt";
const HOLDS_WAY = "Only Zane edits hold-prs.txt (Hard rules, Work). To relay his explicit call, use `bin/hold-row add '<row>; Zane <date> <time>'` (or `retire '<row>' '<source>'`, `replace '<row>' '<new row>'`) on the merge host; otherwise put the row you want under questions:.";

function holdsAt(w: string, dir: string | undefined, remote: boolean, host: Host): boolean {
	const named = fold(base(w)) === HOLDS;
	if (remote || /[$`*?[{]/.test(w) || /^~[^/]/.test(w)) return named;
	const abs = /^[/~]/.test(w) ? w : dir && `${dir}/${w}`;
	return abs === undefined ? named : host.isHolds(abs);
}

interface Cmd { words: string[]; raw: string[]; dirs: (string | undefined)[]; at: (string | undefined)[]; remote: boolean }
const REMOTE = new Set(["ssh", "vm"]);

function unquotedEnd(s: string): boolean {
	let q = "";
	for (let i = 0; i < s.length; i++) {
		const c = s[i];
		if (q) { if (c === q) q = ""; else if (c === "\\" && q === '"') i++; }
		else if (c === "'" || c === '"') q = c;
		else if (c === "\\") { if (++i === s.length) return false; }
		else if (c === "#" && (i === 0 || /[\s;&|()]/.test(s[i - 1]))) return false;
	}
	return !q;
}

function stripHeredocs(line: string): string {
	const lines = line.split("\n");
	const out: string[] = [];
	for (let i = 0; i < lines.length; i++) {
		out.push(lines[i]);
		for (const m of lines[i].matchAll(/<<(-?)\s*(['"]?)([A-Za-z_][A-Za-z0-9_]*)\2(?=$|[\s;&|<>()])/g)) {
			if (!unquotedEnd(lines[i].slice(0, m.index))) continue;
			const end = lines.findIndex((l, k) => k > i && (m[1] ? l.replace(/^\t+/, "") : l) === m[3]);
			if (end < 0) continue;
			if (!m[2] && lines.slice(i + 1, end).some((l) => /\$\(|`/.test(l))) continue;
			if (/\\$/.test(lines[i]) || commands(lines[i]).some((c) => SHELLS.has(program(c)[0] ?? ""))) continue;
			lines.splice(i + 1, end - i - 1);
		}
	}
	return out.join("\n");
}

function simpleCommands(line: string, dirs: (string | undefined)[], remote = false, depth = 0, env: string[] = [], at = dirs): Cmd[] {
	const out: Cmd[] = [];
	const subst = /`|\$\(/.test(line);
	for (const words of commands(stripHeredocs(line))) {
		const w = program(words);
		if (w[0] === "cd" || w[0] === "pushd" || w[0] === "popd") {
			if (w[0] === "cd") {
				const to = w[1] ?? "~";
				const next = /^[~$]/.test(to) ? [undefined] : to.startsWith("/") ? [path.posix.resolve(to)] : dirs.map((d) => d && path.posix.resolve(d, to));
				dirs = [...new Set([...dirs, ...next])];
			}
			const to = w[0] === "popd" ? "-" : w.slice(1).find((a) => !/^-[LPe@]+$/.test(a)) ?? "~";
			const nextAt = subst || !to.startsWith("/") || /[$`*?[{]/.test(to) ? [undefined] : [to, path.posix.resolve(to)];
			at = [...new Set([...at, ...nextAt])];
			continue;
		}
		const raw = [...env, ...words];
		const pre = words.slice(0, words.length - w.length);
		const moved = pre.some((a) => /^(-C|--chdir|-D)(=|$)|^-C./.test(a) || /^-[A-Za-z]*[CD]$/.test(a));
		const here = moved ? [undefined] : at;
		out.push({ words: w, raw, dirs, at: here, remote });
		const inner = REMOTE.has(w[0]) ? [] : raw.slice(0, raw.length - w.length);
		if (depth < 3 && SHELLS.has(w[0] ?? ""))
			for (const a of w.slice(1)) if (/\s/.test(a)) out.push(...simpleCommands(a, [undefined], remote || REMOTE.has(w[0]), depth + 1, inner, REMOTE.has(w[0]) ? [undefined] : here));
	}
	return out;
}

function gitPush(w: string[], raw: string[]): string | undefined {
	let i = 1;
	while (i < w.length && w[i].startsWith("-")) i += GIT_VALUE_OPTS.has(w[i]) ? 2 : 1;
	if (w[i] !== "push") return undefined;
	if ([...raw.slice(0, raw.length - w.length), ...w.slice(1, i)].some((a) => /core\.hookspath/i.test(a)))
		return "it overrides core.hooksPath, which skips the pre-push hook";
	for (const a of w.slice(i + 1)) {
		if (a === "--no-verify") return "`--no-verify` skips the pre-push hook (the patent hold)";
		if (/^--force(-with-lease|-if-includes)?(=|$)/.test(a) || /^-[a-zA-Z]*f[a-zA-Z]*$/.test(a)) return "it force-pushes";
		if (/^\+/.test(a)) return `the refspec \`${a}\` forces the update`;
	}
	return undefined;
}

function ghNamedRepo(w: string[]): string | undefined {
	const named = (m: RegExpMatchArray | null | undefined) => (m ? repoName(m[1]) : undefined);
	if (w[1] === "api") return named(operands(w.slice(2), GH_API_VALUE_OPTS)[0]?.match(/^(?:https?:\/\/[^/]+\/)?\/?repos\/([^/]+\/[^/]+)\//));
	for (let i = 0; i < w.length; i++) {
		const a = w[i];
		if (a === "-R" || a === "--repo") return repoName(w[i + 1]);
		const m = a.match(/^(?:-R|--repo=)(.+)$/);
		if (m) return repoName(m[1]);
		if (GH_VALUE_OPTS.has(a)) i++;
	}
	for (const a of operands(w.slice(1), GH_VALUE_OPTS)) {
		const r = named(a.match(/github\.com\/([^/\s]+\/[^/\s#?]+)\/(?:pull|actions|issues)\//));
		if (r) return r;
	}
	return undefined;
}

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

const GRAPHQL_MERGE = /\b(mergePullRequest|enablePullRequestAutoMerge)\b/;
const GRAPHQL_READY = /\bmarkPullRequestReadyForReview\b/;

const GH_API_VALUE_OPTS = new Set(["-X", "--method", "-f", "--raw-field", "-F", "--field", "-H", "--header", "--input", "-q", "--jq", "-t", "--template", "--hostname", "--cache", "-p", "--preview"]);

const GH_VALUE_OPTS = new Set(["-R", "--repo", "-t", "--subject", "-b", "--body", "-F", "--body-file", "-A", "--author-email", "--match-head-commit", "-c", "--comment", "-r", "--reason", "--duplicate-of"]);

function targetRepos({ words: w, raw, dirs, remote }: Cmd, host: Host): (string | undefined)[] {
	const pre = raw.slice(0, raw.length - w.length);
	const inline = pre.map((a) => a.match(/^GH_REPO=(.*)$/)?.[1]).filter((v) => v !== undefined).pop();
	const cleared = pre.some((a, i) => a === "-i" || a === "--ignore-environment" || a === "--unset=GH_REPO" || ((a === "-u" || a === "--unset") && pre[i + 1] === "GH_REPO"));
	const pattern = ghNamedRepo(w);
	const named = pattern && !pattern.includes("{") ? pattern : repoName(inline !== undefined ? inline || undefined : cleared || remote ? undefined : host.ghRepo);
	const current = named ? [named] : dirs.map((d) => (d === undefined ? undefined : host.repoAt(d))).filter((r) => r !== null);
	const fill = (r: string | undefined) => {
		if (r === undefined) return undefined;
		const [o, n] = r.split("/");
		return pattern!.replace(/\{owner\}/g, o).replace(/\{repo\}/g, n);
	};
	return pattern?.includes("{") ? current.map(fill).map((r) => (r?.includes("{") ? undefined : r)) : current;
}

function ghOwned(c: Cmd, owned: Owned, host: Host, ready: boolean): string | undefined {
	const w = c.words;
	if ((owned && !owned.size) || w[0] !== "gh") return undefined;
	const who = owned ? `${[...owned].join(", ")} is merged only by its merge job` : "the repo the merge job owns is unknown (bin/policy dump failed)";
	const api = w[1] === "api" && ghApiWrites(w);
	if (api && operands(w.slice(2), GH_API_VALUE_OPTS)[0] === "graphql" && w.some((a) => GRAPHQL_MERGE.test(a) || (!ready && GRAPHQL_READY.test(a))))
		return `a GraphQL ready, merge or auto-merge mutation while ${who}`;
	const flag = (f: string) => w.some((a, i) => a === f && !GH_VALUE_OPTS.has(w[i - 1]));
	const help = flag("--help") || flag("-h");
	const act = help ? undefined :
		w[1] === "pr" && w[2] === "merge" && !flag("--disable-auto") ? "`gh pr merge`" :
		w[1] === "pr" && w[2] === "ready" && !flag("--undo") && !ready ? "`gh pr ready` (a draft is a hold)" :
		api && w.some((a) => /\/pulls\/[^/]+\/merge(\b|$)/.test(a)) ? "a merge through `gh api`" : undefined;
	if (!act) return undefined;
	if (!owned) return `${act} while ${who}`;
	const repos = targetRepos(c, host);
	if (repos.includes(undefined)) return `${act} where the target repo can't be told (no -R, and the directory's remote is unknown); ${who}`;
	const hit = repos.find((r) => owned.has(r as string));
	return hit ? `${act} in ${hit}: the merge job is its only merger (Hard rules, Merging)` : undefined;
}

const NAMES_PR = /(^|[^\w&])#\d+\b|github\.com\/[^/\s]+\/[^/\s]+\/pull\/\d+/;
const CLOSE_WAY = "Close a trycua/cua issue by hand only when one of our merged PRs fixed it, with a comment naming that PR: `gh issue close <n> -R trycua/cua --comment \"Fixed by #<pr>.\"` (Hard rules, Scope: issue-close). Otherwise leave it open, or put it under questions:.";

function ghIssueClose(c: Cmd, issues: Owned, host: Host): string | undefined {
	const w = c.words;
	if ((issues && !issues.size) || w[0] !== "gh") return undefined;
	const opt = (names: string[]) => {
		for (let i = 0; i < w.length; i++) {
			const m = w[i].match(/^(--?[\w-]+)=(.*)$/s);
			if (m && names.includes(m[1])) return m[2];
			if (names.includes(w[i])) return w[i + 1] ?? "";
			const short = names.find((n) => /^-\w$/.test(n) && w[i].startsWith(n) && w[i].length > 2 && !w[i].startsWith("--"));
			if (short) return w[i].slice(2);
		}
		return undefined;
	};
	let act: string | undefined;
	if (w[1] === "issue" && w[2] === "close" && !w.includes("--help") && !w.includes("-h")) {
		const comment = opt(["-c", "--comment"]);
		const reason = opt(["-r", "--reason"]);
		if (opt(["--duplicate-of"]) !== undefined || (reason !== undefined && !/^completed$/i.test(reason)))
			act = "`gh issue close` as not planned or a duplicate";
		else if (comment === undefined || !NAMES_PR.test(comment) || /^\$/.test(comment))
			act = "`gh issue close` without a `--comment` that names the PR that fixed it";
	} else if (w[1] === "api" && ghApiWrites(w)) {
		const ops = operands(w.slice(2), GH_API_VALUE_OPTS);
		const issue = /\/issues\/[^/]+\/?$/.test((ops[0] ?? "").replace(/[?#].*$/s, ""));
		if (ops[0] === "graphql" && w.some((a) => /\bcloseIssue\b/.test(a))) act = "a GraphQL closeIssue mutation";
		else if (issue && w.some((a) => /^state=closed$/i.test(a) || /"state"\s*:\s*"closed"/i.test(a)))
			act = "an issue close through `gh api`";
		else if (issue && w.some((a) => /^--input(=|$)/.test(a)))
			act = "an issue update through `gh api --input`, which may close it";
	}
	if (!act) return undefined;
	if (!issues) return `${act} while the repo the issue-close rule covers is unknown (bin/policy dump failed)`;
	if (act.includes("GraphQL")) return act;
	const repos = targetRepos(c, host);
	if (repos.includes(undefined)) return `${act} where the target repo can't be told (no -R, and the directory's remote is unknown)`;
	const hit = repos.find((r) => issues.has(r as string));
	return hit ? `${act} in ${hit}` : undefined;
}

const TEAMMATE_WAY = "PRs and issues by a review-only teammate (a `review-only` row in hold-prs.txt) get a first review and nothing else; they are never closed by us (Hard rules, Scope). Leave it open, or put it under questions:.";

function apiFields(w: string[]): { key: string; value: string; file: boolean }[] {
	const out: { key: string; value: string; file: boolean }[] = [];
	for (let i = 2; i < w.length; i++) {
		const m = w[i].match(/^(-f|-F|--raw-field|--field)(?:=(.*)|(.+))?$/s);
		if (!m || (m[3] !== undefined && m[1].startsWith("--"))) continue;
		const kv = m[2] ?? m[3] ?? w[++i] ?? "";
		const eq = kv.indexOf("=");
		const value = eq < 0 ? "" : kv.slice(eq + 1);
		out.push({ key: eq < 0 ? kv : kv.slice(0, eq), value, file: /^(-F|--field)$/.test(m[1]) && value.startsWith("@") });
	}
	return out;
}

function closeTarget(w: string[]): { n?: number; what: string } | undefined {
	if ((w[1] === "issue" || w[1] === "pr") && w[2] === "close" && !w.includes("--help") && !w.includes("-h")) {
		const a = operands(w.slice(3), GH_VALUE_OPTS)[0] ?? "";
		const m = a.match(/^#?(\d+)$/) ?? a.match(/github\.com\/[^/\s]+\/[^/\s]+\/(?:issues|pull)\/(\d+)/);
		return { n: m ? Number(m[1]) : undefined, what: `\`gh ${w[1]} close\`` };
	}
	if (w[1] !== "api" || !ghApiWrites(w)) return undefined;
	const ops = operands(w.slice(2), GH_API_VALUE_OPTS);
	const fields = apiFields(w);
	const input = w.some((a) => /^--input(=|$)/.test(a));
	if (ops[0] === "graphql")
		return w.some((a) => /\b(closeIssue|closePullRequest)\b/.test(a)) ? { what: "a GraphQL close mutation" } :
			input || fields.some((f) => f.key === "query" && f.file) ? { what: "a GraphQL query read from a file or stdin, which may close" } : undefined;
	const m = (ops[0] ?? "").replace(/[?#].*$/s, "").match(/^(?:https?:\/\/[^/]+\/)?\/?repos\/[^/]+\/[^/]+\/(?:issues|pulls)\/(\d+)\/?$/);
	const closes = input || fields.some((f) => f.key === "state" && (f.file || /^closed$/i.test(f.value))) || w.some((a) => /"state"\s*:\s*"closed"/i.test(a));
	return m && closes ? { n: Number(m[1]), what: "a close through `gh api`" } : undefined;
}

function ghTeammateClose(c: Cmd, issues: Owned, host: Host): string | undefined {
	if (c.words[0] !== "gh" || (issues && !issues.size)) return undefined;
	const t = closeTarget(c.words);
	if (!t) return undefined;
	if (!issues) return `${t.what} while the repo the teammate rule covers is unknown (bin/policy dump failed)`;
	if (t.what.includes("GraphQL")) return `${t.what}, whose target and author can't be checked; use \`gh pr close\` or \`gh issue close\`, or pass the query inline`;
	const repos = targetRepos(c, host);
	if (repos.includes(undefined)) return `${t.what} where the target repo can't be told, so its author can't be checked`;
	const repo = repos.find((r) => issues.has(r as string));
	if (!repo) return undefined;
	if (t.n === undefined) return `${t.what} in ${repo} without a number or URL, so its author can't be checked against the review-only teammates; name it`;
	const team = host.reviewOnly?.() ?? null;
	if (!team) return `${t.what} of ${repo}#${t.n} while no hold-prs.txt could be read, so the review-only teammates are unknown`;
	const who = host.authorOf?.(repo, t.n);
	if (who === undefined) return `${t.what} of ${repo}#${t.n} whose author \`gh api repos/${repo}/issues/${t.n}\` could not tell`;
	return team.has(who.toLowerCase()) ? `${t.what} of ${repo}#${t.n}, which ${who} opened, a review-only teammate` : undefined;
}

function operands(args: string[], valued: Set<string> = new Set()): string[] {
	const out: string[] = [];
	for (let i = 0; i < args.length; i++) {
		if (args[i] === "--") { out.push(...args.slice(i + 1)); break; }
		if (args[i].startsWith("-") && args[i] !== "-") { if (valued.has(args[i])) i++; continue; }
		out.push(args[i]);
	}
	return out;
}

function writesHolds(w: string[], dir: string | undefined, remote: boolean, host: Host): boolean {
	const isHolds = (a: string) => holdsAt(a, dir, remote, host);
	for (let i = 0; i < w.length; i++) {
		const m = /\s/.test(w[i]) ? null : w[i].match(/>>?\|?(.*)$/);
		const to = m && (m[1] || w[i + 1]);
		if (to && isHolds(to)) return true;
	}
	const [p, ...all] = w;
	const args = all.filter((a, i) => !/^(?:\d*|&)?[<>]/.test(a) && !/^(?:\d*|&)?[<>]{1,2}\|?$/.test(all[i - 1] ?? ""));
	const ops = operands(args, new Set(["-t", "-S", "-m", "-o", "-g", "-e", "--target-directory", "--suffix"]));
	switch (p) {
		case "tee": case "rm": case "unlink": case "truncate": case "chmod": case "chown": case "touch": case "shred":
			return ops.some(isHolds);
		case "sed": case "perl":
			return args.some((a) => /^-[a-zA-Z]*i/.test(a) || a.startsWith("--in-place")) && ops.some(isHolds);
		case "mv": case "rsync": case "cp": case "install": case "ln": case "ditto": {
			const t = args.findIndex((a) => a === "-t" || a.startsWith("--target-directory"));
			const into = t < 0 ? undefined : args[t].includes("=") ? args[t].slice(args[t].indexOf("=") + 1) : args[t + 1];
			const sources = into !== undefined ? ops : ops.slice(0, -1);
			if ((p === "mv" || args.includes("--remove-source-files")) && sources.some(isHolds)) return true;
			const dest = into ?? ops[ops.length - 1];
			if (dest === undefined || !sources.length) return false;
			return (into === undefined && isHolds(dest)) || sources.some((s) => isHolds(`${dest}/${base(s)}`));
		}
		case "dd":
			return args.some((a) => a.startsWith("of=") && isHolds(a.slice(3)));
	}
	return false;
}

export function checkBash(command: string, owned: Owned, host: Host = NO_HOST, cwd?: string, ready = false, issues: Owned = new Set()): string | undefined {
	if (/ONIGIRI_READY_ALLOWED/.test(command.replace(/(?<!\\)\$ONIGIRI_READY_ALLOWED(?![A-Za-z0-9_])/g, "").replace(/["'\\]/g, "")))
		return "blocked: only the launcher sets ONIGIRI_READY_ALLOWED, from the brief's `merge: allowed` line. If you think it is needed, put it under questions:.";
	for (const c of simpleCommands(command, [cwd])) {
		if (c.at.some((d) => writesHolds(c.words, d, c.remote, host)))
			return `blocked: it writes the holds file. ${HOLDS_WAY}`;
		const why = c.words[0] === "git" ? gitPush(c.words, c.raw) : ghOwned(c, owned, host, ready);
		if (why) return `blocked: ${why}. onigiri-mode's Hard rules forbid it; if you think it is needed, put it under questions:.`;
		const close = ghIssueClose(c, issues, host);
		if (close) return `blocked: ${close}. ${CLOSE_WAY}`;
		const mate = ghTeammateClose(c, issues, host);
		if (mate) return `blocked: ${mate}. ${TEAMMATE_WAY}`;
	}
	return undefined;
}

export function checkWrite(file: string, host: Host = NO_HOST, cwd?: string): string | undefined {
	return holdsAt(file.replace(/^@/, ""), cwd, false, host) ? `blocked: it writes the holds file. ${HOLDS_WAY}` : undefined;
}
