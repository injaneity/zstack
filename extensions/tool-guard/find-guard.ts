// Refuses a bash `find` that walks the whole disk or a whole home directory. One session lost over an hour to
// `find / -name cmake` and `find /tmp /Users/<user> ...` while looking for a tool. Pure, so tests run it without pi.

const WRAPPERS = new Set(["sudo", "time", "command", "exec", "nohup", "env", "nice", "ionice", "timeout", "gtimeout", "xargs", "stdbuf"]);
// Directories too big to walk. A top-level directory (/usr, /tmp, /opt, ...) counts too, by depth below.
const BROAD = new Set(["/usr/local", "/opt/homebrew", "/private/tmp", "/private/var", "/home", "/Users"]);
const MAX_DEPTH = 2;

export const FIND_HINT =
	"Find a tool with `command -v <name>`, `which -a <name>` or `brew --prefix <formula>`, or look in its known install " +
	"directory (/opt/homebrew/bin, /usr/local/bin, ~/.cargo/bin, ~/.local/bin). Scope `find` to the project directory, " +
	`or bound it with -maxdepth ${MAX_DEPTH} or less.`;

/** Split a command line into words per simple command. Quotes are honoured; expansions are left as text. */
function commands(line: string): string[][] {
	const out: string[][] = [];
	let words: string[] = [];
	let word = "";
	let inWord = false;
	let quote = "";
	const endWord = () => {
		if (inWord) words.push(word);
		word = "";
		inWord = false;
	};
	const endCommand = () => {
		endWord();
		if (words.length) out.push(words);
		words = [];
	};
	for (let i = 0; i < line.length; i++) {
		const c = line[i];
		if (quote) {
			if (c === quote) quote = "";
			else if (c === "\\" && quote === '"' && i + 1 < line.length) word += line[++i];
			else word += c;
			continue;
		}
		if (c === "'" || c === '"') {
			quote = c;
			inWord = true;
		} else if (c === "\\" && i + 1 < line.length) {
			word += line[++i];
			inWord = true;
		} else if (c === " " || c === "\t") endWord();
		else if (";&|\n()`{}".includes(c) || (c === "$" && line[i + 1] === "(")) {
			if (c === "$") i++;
			endCommand();
		} else {
			word += c;
			inWord = true;
		}
	}
	endCommand();
	return out;
}

/** The find arguments of a simple command, or undefined when it does not run find. */
function findArgs(words: string[]): string[] | undefined {
	let i = 0;
	while (i < words.length) {
		const w = words[i];
		const base = w.slice(w.lastIndexOf("/") + 1);
		if (base === "find") return words.slice(i + 1);
		if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(w) || WRAPPERS.has(base)) {
			i++;
			// wrapper options and their values: `timeout -k 5 60`, `nice -n 5`, `sudo -u x`
			while (i < words.length && (/^-/.test(words[i]) || /^\d+[smhd]?$/.test(words[i]) || /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[i]))) {
				if (/^-(n|u|g|k|s|[ioe])$/.test(words[i]) && i + 1 < words.length) i++;
				i++;
			}
			continue;
		}
		return undefined;
	}
	return undefined;
}

function normalize(p: string, home: string): string {
	let s = p.replace(/^(~|\$HOME|\$\{HOME\})(?=\/|$)/, home);
	if (s.length > 1) s = s.replace(/\/+$/, "");
	return s.replace(/\/\*$/, "") || "/";
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
	for (const words of commands(command)) {
		const args = findArgs(words);
		if (!args) continue;
		let j = 0;
		while (j < args.length && /^-[HLP]$|^-O\d$/.test(args[j])) j++;
		if (args[j] === "-D") j += 2;
		const paths: string[] = [];
		while (j < args.length && !/^[-(!]/.test(args[j])) paths.push(args[j++]);
		const depth = args.indexOf("-maxdepth");
		if (depth >= 0 && Number(args[depth + 1]) <= MAX_DEPTH) continue;
		const hit = paths.map((p) => normalize(p, home)).filter((p) => broad(p, home));
		if (hit.length)
			return `blocked: \`find ${hit.join(" ")}\` walks the whole disk or a whole home directory and can run for tens of minutes. ${FIND_HINT}`;
	}
	return undefined;
}
