// Refuses a bash `find` that walks the whole disk or a whole home directory. One session lost over an hour to
// `find / -name cmake` and `find /tmp /Users/<user> ...` while looking for a tool. Pure, so tests run it without pi.
import * as path from "node:path";
import { commands, program } from "./shell.ts";

// Directories too big to walk. A top-level directory (/usr, /tmp, /opt, ...) counts too, by depth below.
const BROAD = new Set(["/usr/local", "/opt/homebrew", "/private/tmp", "/private/var", "/home", "/Users"]);
const MAX_DEPTH = 2;

export const FIND_HINT =
	"Find a tool with `command -v <name>`, `which -a <name>` or `brew --prefix <formula>`, or look in its known install " +
	"directory (/opt/homebrew/bin, /usr/local/bin, ~/.cargo/bin, ~/.local/bin). Scope `find` to the project directory, " +
	`or bound it with -maxdepth ${MAX_DEPTH} or less.`;

/** The find arguments of a simple command, or undefined when it does not run find. */
function findArgs(words: string[]): string[] | undefined {
	const w = program(words);
	return w[0] === "find" ? w.slice(1) : undefined;
}

function normalize(p: string, home: string): string {
	const s = p.replace(/^(~|\$HOME|\$\{HOME\})(?=\/|$)/, home).replace(/\/\*$/, "/");
	return s.startsWith("/") ? path.posix.resolve(s) : s; // folds //, . and .., drops a trailing /
}

function broad(p: string, home: string): boolean {
	if (!p.startsWith("/")) return false;
	if (p === "/" || BROAD.has(p) || p === home) return true;
	const parts = p.split("/").filter(Boolean);
	if (parts.length === 1) return true; // /usr, /tmp, /opt, /var, /root, /Library ...
	return parts.length === 2 && (parts[0] === "home" || parts[0] === "Users"); // someone's home
}

/** The reason to refuse `command`, or undefined to let it run. */
export function checkFind(command: string, home = "/home/user"): string | undefined {
	let cwd = ""; // set by a literal `cd <dir>` earlier in the line, so `cd / && find .` is seen
	for (const words of commands(command)) {
		if (words[0] === "cd") {
			const to = normalize(words[1] ?? "~", home);
			cwd = to.startsWith("/") ? to : cwd && path.posix.resolve(cwd, to);
			continue;
		}
		const args = findArgs(words);
		if (!args) continue;
		let j = 0;
		while (j < args.length && /^-[HLP]$|^-O\d$/.test(args[j])) j++;
		if (args[j] === "-D") j += 2;
		const paths: string[] = [];
		while (j < args.length && !/^[-(!]/.test(args[j])) paths.push(args[j++]);
		// only a -maxdepth before the first action counts; after -exec it is the command's argument
		const actions = args.findIndex((a, k) => k >= j && /^-(exec|execdir|ok|okdir)$/.test(a));
		const depth = args.slice(j, actions < 0 ? undefined : actions).indexOf("-maxdepth");
		if (depth >= 0 && Number(args[j + depth + 1]) <= MAX_DEPTH) continue;
		const hit = (paths.length ? paths : ["."])
			.map((p) => normalize(p, home))
			.map((p) => (cwd && !p.startsWith("/") ? path.posix.resolve(cwd, p) : p))
			.filter((p) => broad(p, home));
		if (hit.length)
			return `blocked: \`find ${hit.join(" ")}\` walks the whole disk or a whole home directory and can run for tens of minutes. ${FIND_HINT}`;
	}
	return undefined;
}
