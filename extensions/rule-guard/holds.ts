// rule-guard's holds files: the real ones this host's config names, and whether a path is one of them, with symlinks
// followed. A hold-prs.txt anywhere else (a test root, a throwaway HOME) is an ordinary file. See docs/RULE-GUARD.md.
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

/** macOS volumes ignore case, so HOLD-PRS.TXT is the holds file even before it exists. */
export const fold = (q: string) => (process.platform === "darwin" ? q.toLowerCase() : q);

/** The real holds files: the merge job's `$ONIGIRI_ROOT/hold-prs.txt` (bin/pi-run's default root is ~/onigiri), the
 * VM's /srv/onigiri/hold-prs.txt that Hard rules name, and the mini's read-only copy that hosts/mini/sync-from-vm.sh
 * keeps at ~/onigiri/sync/hold-prs.txt. */
export function holdsPaths(env: { ONIGIRI_ROOT?: string } = process.env): string[] {
	const root = env.ONIGIRI_ROOT || "~/onigiri";
	return [...new Set([`${root}/hold-prs.txt`, "/srv/onigiri/hold-prs.txt", "~/onigiri/sync/hold-prs.txt"])];
}

/** Whether `p` (absolute, or starting with ~) names one of `holds`: the same file once each component is resolved
 * in order, as the kernel does (so `link/..` is the parent of link's target, not of link, and a dangling link is its
 * target), with any part not created yet kept as written, or a hard link to it. A path that can't be walked (a symlink loop, a directory it can't read)
 * counts when its file name is hold-prs.txt, as an unresolved path does in rules.ts. */
export function isHoldsPath(p: string, holds: string[], home = os.homedir()): boolean {
	const expand = (q: string) => q.replace(/^~(?=\/|$)/, home);
	// the kernel's walk: each symlink, dangling ones included, is replaced by its target before the next component
	const canon = (q: string): string | undefined => {
		const todo = expand(q).split("/");
		let cur = "/", links = 0;
		while (todo.length) {
			const part = todo.shift()!;
			if (part === "" || part === ".") continue;
			if (part === "..") { cur = path.dirname(cur); continue; }
			const next = path.join(cur, part);
			let st: fs.Stats | undefined;
			try {
				st = fs.lstatSync(next, { throwIfNoEntry: false });
			} catch (e) {
				// under a plain file nothing exists: kept as written. Anything else can't be told.
				if ((e as { code?: string }).code !== "ENOTDIR") return undefined;
			}
			if (st?.isSymbolicLink()) {
				if (++links > 40) return undefined; // a loop
				let to: string;
				try { to = fs.readlinkSync(next); } catch { return undefined; }
				todo.unshift(...to.split("/"));
				if (to.startsWith("/")) cur = "/";
				continue;
			}
			cur = next;
		}
		return cur;
	};
	// a loop or an unreadable directory has no inode to compare; its path still is
	const stat = (q: string) => { try { return fs.statSync(expand(q), { throwIfNoEntry: false }); } catch { return undefined; } };
	const at = canon(p);
	if (at === undefined) return fold(path.basename(p)) === "hold-prs.txt";
	const c = fold(at), s = stat(p);
	return holds.some((h) => {
		const ch = canon(h);
		if (ch !== undefined && fold(ch) === c) return true;
		const t = s && stat(h);
		return !!t && t.dev === s.dev && t.ino === s.ino;
	});
}
