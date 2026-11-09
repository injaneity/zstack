// The shell reading both guards share: a command line split into simple commands, and a command's program with its
// wrappers removed. Pure, so tests run it without pi.

export const base = (w: string) => w.slice(w.lastIndexOf("/") + 1);

/** Split a command line into words per simple command. Quotes are honoured; expansions are left as text. */
export function commands(line: string): string[][] {
	const out: string[][] = [];
	let words: string[] = [];
	let word = "";
	let inWord = false;
	let quote = "";
	const resume: string[] = []; // closers of substitutions opened inside double quotes: ")" or "`"
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
		if (quote === '"' && (c === "`" || (c === "$" && line[i + 1] === "("))) {
			// a substitution inside double quotes still runs: read it as its own command, then go back into the quotes
			if (c === "$") i++;
			resume.push(c === "`" ? "`" : ")");
			quote = "";
			endCommand();
			continue;
		}
		if (!quote && resume.length && c === resume[resume.length - 1]) {
			resume.pop();
			endCommand();
			quote = '"';
			inWord = true;
			continue;
		}
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
		else if (";&|\n()`".includes(c) || (c === "$" && line[i + 1] === "(")) {
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

// Shell keywords that can start a simple command's words: `{ git push -f; }`, `if x; then git push -f; fi`, `! cmd`.
const KEYWORDS = new Set(["{", "}", "!", "if", "then", "else", "elif", "fi", "do", "done", "while", "until"]);
const WRAPPERS = new Set(["sudo", "env", "nohup", "nice", "ionice", "time", "command", "exec", "timeout", "gtimeout", "xargs", "stdbuf"]);
// Per wrapper, the options that take the next word as their value (`sudo -u bob`, `env --unset X`, `nice -n 5`).
// A wrapper's other options take none, so `env -i git push -f` still reaches git.
const VALUE_OPTS: Record<string, Set<string>> = {
	sudo: new Set(["-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U", "-T", "-D", "--user", "--group", "--close-from", "--host", "--prompt", "--role", "--type", "--other-user", "--chdir", "--command-timeout"]),
	env: new Set(["-u", "-C", "-S", "--unset", "--chdir", "--split-string"]),
	nice: new Set(["-n", "--adjustment"]),
	ionice: new Set(["-c", "-n", "-p", "--class", "--classdata", "--pid"]),
	timeout: new Set(["-k", "-s", "--kill-after", "--signal"]),
	gtimeout: new Set(["-k", "-s", "--kill-after", "--signal"]),
	xargs: new Set(["-n", "-P", "-L", "-s", "-d", "-a", "-E", "-I", "--max-args", "--max-procs", "--delimiter", "--arg-file", "--max-lines", "--max-chars"]),
	stdbuf: new Set(["-i", "-o", "-e", "--input", "--output", "--error"]),
};

/** The command with wrappers, their options and VAR=value prefixes removed, and the program name reduced to its base. */
export function program(words: string[]): string[] {
	let i = 0;
	while (i < words.length) {
		const w = words[i];
		if (/^[A-Za-z_][A-Za-z0-9_]*=/.test(w) || KEYWORDS.has(w)) { i++; continue; }
		// `perl -e '...exec @ARGV' [n] cmd...` is the timeout wrapper on hosts without timeout(1)
		if (base(w) === "perl" && words[i + 1] === "-e" && /\bexec\s+@ARGV\b/.test(words[i + 2] ?? "")) {
			i += 3;
			if (/^\d+$/.test(words[i] ?? "")) i++;
			continue;
		}
		if (!WRAPPERS.has(base(w))) break;
		const valued = VALUE_OPTS[base(w)] ?? new Set<string>();
		i++;
		while (i < words.length && (/^-/.test(words[i]) || /^\d+(\.\d+)?[smhd]?$/.test(words[i]) || /^[A-Za-z_][A-Za-z0-9_]*=/.test(words[i]))) {
			if (valued.has(words[i]) && i + 1 < words.length) i++;
			i++;
		}
	}
	const w = words.slice(i);
	if (w[0]) w[0] = base(w[0]);
	return w;
}
