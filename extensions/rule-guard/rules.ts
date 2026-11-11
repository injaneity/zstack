// onigiri-mode's Hard rules' mechanical bans, enforced on bash commands and file writes instead of trusted to the prompt.
// Pure: the caller passes the repo lookup, so tests run without pi or git. See docs/RULE-GUARD.md.
import * as path from "node:path";
import { fold } from "./holds.ts";
import { base, commands, program } from "./shell.ts";

/** Repos whose merges belong to the merge job (Hard rules, Merging: in trycua/cua the tick is the only merger). */
export const MERGE_OWNED: ReadonlySet<string> = new Set(["trycua/cua"]);

/** Whether the session's brief allows merging, so it may ready its own draft. The launcher sets $ONIGIRI_READY_ALLOWED
 * from the brief's `merge: allowed` header; the extension reads it once at load, so the session can't set it later. */
export function readyAllowed(env = process.env.ONIGIRI_READY_ALLOWED ?? ""): boolean {
	return env === "1";
}

/** What the guard asks of the machine: the GitHub repo (owner/name) checked out at a directory (null when it is not a
 * git checkout, so gh has no repo there; undefined when it can't be told), and whether a path (absolute, or starting
 * with ~) is one of the real holds files (holds.ts). `ghRepo` is the session's inherited $GH_REPO, which gh reads in any directory. */
export interface Host {
	repoAt(dir: string): string | null | undefined;
	isHolds(p: string): boolean;
	ghRepo?: string;
	/** A workflow run's or job's conclusion in `repo` (owner/name), as GitHub reports it; undefined when it can't be told. */
	ciConclusion?(repo: string, kind: "run" | "job", id: string): string | undefined;
}
const NO_HOST: Host = { repoAt: () => undefined, isHolds: () => false };

// A quoted word under these programs is a command line too: `ssh host "git push -f"`, `bash -c "..."`, `vm "..."`.
const SHELLS = new Set(["ssh", "vm", "bash", "sh", "zsh", "eval"]);
// git global options that take the next word as their value.
const GIT_VALUE_OPTS = new Set(["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix", "--exec-path", "--list-cmds", "--attr-source"]);
const HOLDS = "hold-prs.txt";
// the one sanctioned write: bin/hold-row, which appends a row relaying Zane's explicit call (Zane, 11 Nov)
const HOLDS_WAY = "Only Zane edits hold-prs.txt (Hard rules, Work). To relay his explicit call, append one row with `bin/hold-row add '<row>; Zane <date> <time>'` on the merge host; otherwise put the row you want under questions:.";

/**
 * Whether the word `w`, a path in a command run in `dir`, may name a real holds file. A path the guard can resolve
 * (absolute, ~/, or relative to a known directory) counts only when it is one, symlinks followed, so a test root's
 * hold-prs.txt is an ordinary file. One it can't (a variable, a glob or brace, an unknown directory, or a path on a
 * remote host) counts when its file name is hold-prs.txt.
 */
function holdsAt(w: string, dir: string | undefined, remote: boolean, host: Host): boolean {
	const named = fold(base(w)) === HOLDS;
	if (remote || /[$`*?[{]/.test(w) || /^~[^/]/.test(w)) return named;
	// joined, not resolved: `..` after a symlink is the target's parent, which only the host can tell
	const abs = /^[/~]/.test(w) ? w : dir && `${dir}/${w}`;
	return abs === undefined ? named : host.isHolds(abs);
}

/**
 * A simple command, its raw words (env prefixes included) and every directory it might run in. A `cd` may fail
 * (`cd x || gh ...`), so a command keeps the directories from before each `cd` too; undefined is one that can't be
 * told (`cd ~/x`, `cd $X`, or a remote shell). `at` is the same for the holds check, which can't tell a relative cd
 * either, since $CDPATH may redirect it.
 */
interface Cmd { words: string[]; raw: string[]; dirs: (string | undefined)[]; at: (string | undefined)[]; remote: boolean }
const REMOTE = new Set(["ssh", "vm"]);

/** Whether `s` ends outside any quote or comment, so a `<<` after it is an operator and not text. */
function unquotedEnd(s: string): boolean {
	let q = "";
	for (let i = 0; i < s.length; i++) {
		const c = s[i];
		if (q) { if (c === q) q = ""; else if (c === "\\" && q === '"') i++; }
		else if (c === "'" || c === '"') q = c;
		else if (c === "\\") { if (++i === s.length) return false; } // `\<<` escapes the operator
		else if (c === "#" && (i === 0 || /[\s;&|()]/.test(s[i - 1]))) return false;
	}
	return !q;
}

/**
 * The line with each here-document body removed when it is only data: a `python3 - <<'EOF'` script or a
 * `cat > f <<'EOF'` body. A body stays when a shell on its line may read it (`bash <<EOF`, `ssh host <<EOF`, `cat <<EOF | sh`), when its delimiter
 * is unquoted and the body has a `$(` or backtick (the shell runs those), when the `<<` sits inside quotes, when its
 * line ends in a `\\` continuation, or when it has no closing line.
 */
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
			// any shell on the line may read it: `bash <<EOF`, `cat <<EOF | sh`; a continued line may add one
			if (/\\$/.test(lines[i]) || commands(lines[i]).some((c) => SHELLS.has(program(c)[0] ?? ""))) continue;
			lines.splice(i + 1, end - i - 1);
		}
	}
	return out.join("\n");
}

function simpleCommands(line: string, dirs: (string | undefined)[], remote = false, depth = 0, env: string[] = [], at = dirs): Cmd[] {
	const out: Cmd[] = [];
	// the tokenizer splits a substitution out of the word it sits in (`cd /x/$(y)` reads as `cd /x/`), so on a line
	// with one no cd can be told
	const subst = /`|\$\(/.test(line);
	for (const words of commands(stripHeredocs(line))) {
		const w = program(words);
		if (w[0] === "cd" || w[0] === "pushd" || w[0] === "popd") { // after keywords too: `{ cd x; ...; }`, `if cd x; then ...`
			if (w[0] === "cd") {
				// relative to every directory it might start in, since an earlier cd may have failed
				const to = w[1] ?? "~";
				const next = /^[~$]/.test(to) ? [undefined] : to.startsWith("/") ? [path.posix.resolve(to)] : dirs.map((d) => d && path.posix.resolve(d, to));
				dirs = [...new Set([...dirs, ...next])];
			}
			// `cd -P x` takes x; `cd -`, `pushd +1` and popd go back to a directory the guard did not keep
			const to = w[0] === "popd" ? "-" : w.slice(1).find((a) => !/^-[LPe@]+$/.test(a)) ?? "~";
			// for holds only a literal absolute path is told: a relative one may go through $CDPATH, however it is set
			// or spelled. It is kept as written (`cd -P`) and with `..` collapsed (bash's default logical cd).
			const nextAt = subst || !to.startsWith("/") || /[$`*?[{]/.test(to) ? [undefined] : [to, path.posix.resolve(to)];
			at = [...new Set([...at, ...nextAt])];
			continue;
		}
		const raw = [...env, ...words];
		// a wrapper that changes directory (`env -C dir`, `sudo -D dir`) runs the command somewhere the guard can't tell
		const pre = words.slice(0, words.length - w.length);
		const moved = pre.some((a) => /^(-C|--chdir|-D)(=|$)|^-C./.test(a) || /^-[A-Za-z]*[CD]$/.test(a));
		const here = moved ? [undefined] : at;
		out.push({ words: w, raw, dirs, at: here, remote });
		// a local shell inherits this command's env prefix (`GH_REPO=x bash -c ...`); a remote one does not
		const inner = REMOTE.has(w[0]) ? [] : raw.slice(0, raw.length - w.length);
		if (depth < 3 && SHELLS.has(w[0] ?? ""))
			// a local shell starts where this command runs, so a holds path in it resolves from there too
			for (const a of w.slice(1)) if (/\s/.test(a)) out.push(...simpleCommands(a, [undefined], remote || REMOTE.has(w[0]), depth + 1, inner, REMOTE.has(w[0]) ? [undefined] : here));
	}
	return out;
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

/** The repo a gh command names: -R/--repo or a PR or run URL operand, or for `gh api` the endpoint's repos/<owner>/<name>/
 * path, whose `{owner}` and `{repo}` gh fills from the current repo (ghOwned resolves them). Option values (a header, a
 * field, a body) name nothing. */
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
		const r = named(a.match(/github\.com\/([^/\s]+\/[^/\s#?]+)\/(?:pull|actions)\//));
		if (r) return r;
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

// GraphQL mutations that ready, merge or auto-merge a PR. The query names a node id, not a repo, so any owned repo counts.
const GRAPHQL_MERGE = /\b(mergePullRequest|enablePullRequestAutoMerge)\b/;
const GRAPHQL_READY = /\bmarkPullRequestReadyForReview\b/;

// Zane, 11 Nov: cancelled or stuck CI is rerun without asking; only real failures go to him. A stuck job is cancelled
// first (GitHub reruns only finished runs), so these two cover it.
const RERUNNABLE = new Set(["cancelled", "timed_out"]);

// gh api options that take the next word as their value
const GH_API_VALUE_OPTS = new Set(["-X", "--method", "-f", "--raw-field", "-F", "--field", "-H", "--header", "--input", "-q", "--jq", "-t", "--template", "--hostname", "--cache", "-p", "--preview"]);

/** What a CI rerun targets: `gh run rerun <run> [--job <job>]`, or an actions runs/jobs rerun API path. */
function rerunTarget(w: string[]): { kind: "run" | "job"; id: string } | undefined {
	if (w[1] === "api") {
		// the endpoint is the first operand; a field value (`-f x=.../runs/7/rerun`) names nothing
		const m = operands(w.slice(2), GH_API_VALUE_OPTS)[0]?.match(/^\/?repos\/[^/]+\/[^/]+\/actions\/(runs|jobs)\/(\d+)\/rerun(?:-failed-jobs)?$/);
		return m ? { kind: m[1] === "runs" ? "run" : "job", id: m[2] } : undefined;
	}
	let run: string | undefined, job: string | undefined;
	for (let i = 3; i < w.length; i++) {
		const a = w[i];
		const v = a === "-j" || a === "--job" ? w[++i] : a.match(/^(?:-j|--job=)(.+)$/)?.[1];
		if (v !== undefined) job = v;
		else if (a === "-R" || a === "--repo") i++;
		else if (!a.startsWith("-")) run = a;
	}
	const target = job !== undefined ? { kind: "job" as const, id: job } : run !== undefined ? { kind: "run" as const, id: run } : undefined;
	return target && /^\d+$/.test(target.id) ? target : undefined;
}

// gh pr merge/ready options that take the next word as their value
const GH_VALUE_OPTS = new Set(["-R", "--repo", "-t", "--subject", "-b", "--body", "-F", "--body-file", "-A", "--author-email", "--match-head-commit"]);

function ghOwned({ words: w, raw, dirs, remote }: Cmd, owned: ReadonlySet<string>, host: Host, ready: boolean): string | undefined {
	if (!owned.size || w[0] !== "gh") return undefined;
	const api = w[1] === "api" && ghApiWrites(w);
	if (api && operands(w.slice(2), GH_API_VALUE_OPTS)[0] === "graphql" && w.some((a) => GRAPHQL_MERGE.test(a) || (!ready && GRAPHQL_READY.test(a))))
		return `a GraphQL ready, merge or auto-merge mutation while ${[...owned].join(", ")} is merged only by its merge job`;
	// a flag, not the value of the option before it (`--subject --help`)
	const flag = (f: string) => w.some((a, i) => a === f && !GH_VALUE_OPTS.has(w[i - 1]));
	const help = flag("--help") || flag("-h");
	const act = help ? undefined :
		w[1] === "pr" && w[2] === "merge" && !flag("--disable-auto") ? "`gh pr merge`" :
		w[1] === "pr" && w[2] === "ready" && !flag("--undo") && !ready ? "`gh pr ready` (a draft is a hold)" :
		w[1] === "run" && w[2] === "rerun" ? "`gh run rerun`" :
		api && w.some((a) => /\/pulls\/[^/]+\/merge(\b|$)/.test(a)) ? "a merge through `gh api`" :
		api && w.some((a) => /\/actions\/(runs|jobs)\/[^/]+\/rerun/.test(a)) ? "a CI rerun through `gh api`" : undefined;
	if (!act) return undefined;
	// GH_REPO from the command's own prefix wins; `GH_REPO=`, `env -u GH_REPO` and `env -i` drop the inherited one,
	// and a remote shell never had it
	const pre = raw.slice(0, raw.length - w.length);
	const inline = pre.map((a) => a.match(/^GH_REPO=(.*)$/)?.[1]).filter((v) => v !== undefined).pop();
	const cleared = pre.some((a, i) => a === "-i" || a === "--ignore-environment" || a === "--unset=GH_REPO" || ((a === "-u" || a === "--unset") && pre[i + 1] === "GH_REPO"));
	const pattern = ghNamedRepo(w);
	const named = pattern && !pattern.includes("{") ? pattern : repoName(inline !== undefined ? inline || undefined : cleared || remote ? undefined : host.ghRepo);
	// every directory the command might run in counts, so a failed `cd` can't hide an owned checkout
	const current = named ? [named] : dirs.map((d) => (d === undefined ? undefined : host.repoAt(d))).filter((r) => r !== null);
	// `repos/{owner}/cua/...` takes each placeholder from the current repo, whichever it is
	const fill = (r: string | undefined) => {
		if (r === undefined) return undefined;
		const [o, n] = r.split("/");
		return pattern!.replace(/\{owner\}/g, o).replace(/\{repo\}/g, n);
	};
	const repos = pattern?.includes("{") ? current.map(fill).map((r) => (r?.includes("{") ? undefined : r)) : current;
	if (repos.includes(undefined)) return `${act} where the target repo can't be told (no -R, and the directory's remote is unknown); ${[...owned].join(", ")} is merged only by its merge job`;
	const hit = repos.find((r) => owned.has(r as string));
	if (hit && act.includes("rerun")) {
		// every owned repo the command might act in must report the run or job cancelled or timed out
		const t = rerunTarget(w);
		for (const r of new Set(repos.filter((r) => owned.has(r as string)) as string[])) {
			const c = t && host.ciConclusion?.(r, t.kind, t.id);
			if (!c || !RERUNNABLE.has(c))
				return `${act} in ${r}: only a cancelled or timed-out run or job is rerun without asking (it is ${c ?? "unknown"}); a real failure goes to Zane`;
		}
		return undefined;
	}
	return hit ? `${act} in ${hit}: the merge job is its only merger (Hard rules, Merging)` : undefined;
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

function writesHolds(w: string[], dir: string | undefined, remote: boolean, host: Host): boolean {
	const isHolds = (a: string) => holdsAt(a, dir, remote, host);
	// redirections: `> f`, `>> f`, `2>f`, `&>> f`, `>|f`, and `x>f` (the tokenizer keeps it one word). A word with
	// whitespace in it was quoted, so its `>` is text: `echo "x >hold-prs.txt"` writes nothing.
	for (let i = 0; i < w.length; i++) {
		const m = /\s/.test(w[i]) ? null : w[i].match(/>>?\|?(.*)$/);
		const to = m && (m[1] || w[i + 1]);
		if (to && isHolds(to)) return true;
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
		case "mv": case "rsync": case "cp": case "install": case "ln": case "ditto": {
			// with -t DIR every operand is a source that lands in DIR under its own name; otherwise the last operand is
			// the file written, or a directory each source lands in
			const t = args.findIndex((a) => a === "-t" || a.startsWith("--target-directory"));
			const into = t < 0 ? undefined : args[t].includes("=") ? args[t].slice(args[t].indexOf("=") + 1) : args[t + 1];
			const sources = into !== undefined ? ops : ops.slice(0, -1);
			// moving it away removes it, wherever it goes
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

/** The reason to refuse a bash `command` run in `cwd`, or undefined. `ready` is readyAllowed(). */
export function checkBash(command: string, owned: ReadonlySet<string>, host: Host = NO_HOST, cwd?: string, ready = false): string | undefined {
	// the launcher sets it from the brief. A session that set it (`X=1`, `export`, `printf -v`, `read`, a heredoc
	// script, `${X:=1}`) would start a child pi that may ready, so any mention but a bare `$X` read is refused, heredoc
	// bodies included. The bare reads go first, then quotes and backslashes, as the shell joins `ONIGIRI_READY_""ALLOWED`
	// and `$'ONIGIRI_READY_ALLOWED'` into the name.
	if (/ONIGIRI_READY_ALLOWED/.test(command.replace(/(?<!\\)\$ONIGIRI_READY_ALLOWED(?![A-Za-z0-9_])/g, "").replace(/["'\\]/g, "")))
		return "blocked: only the launcher sets ONIGIRI_READY_ALLOWED, from the brief's `merge: allowed` line. If you think it is needed, put it under questions:.";
	for (const c of simpleCommands(command, [cwd])) {
		// a `cd` may fail, so every directory the command might run in counts, as for gh
		if (c.at.some((d) => writesHolds(c.words, d, c.remote, host)))
			return `blocked: it writes the holds file. ${HOLDS_WAY}`;
		const why = c.words[0] === "git" ? gitPush(c.words, c.raw) : ghOwned(c, owned, host, ready);
		if (why) return `blocked: ${why}. onigiri-mode's Hard rules forbid it; if you think it is needed, put it under questions:.`;
	}
	return undefined;
}

/** The reason to refuse a write or edit to `file` (as the tool got it, relative to `cwd`), or undefined. */
export function checkWrite(file: string, host: Host = NO_HOST, cwd?: string): string | undefined {
	// pi's tools drop a leading @ from the path
	return holdsAt(file.replace(/^@/, ""), cwd, false, host) ? `blocked: it writes the holds file. ${HOLDS_WAY}` : undefined;
}
