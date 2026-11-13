import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

export const fold = (q: string) => (process.platform === "darwin" ? q.toLowerCase() : q);

export function holdsPaths(env: { ONIGIRI_ROOT?: string } = process.env): string[] {
	const root = env.ONIGIRI_ROOT || "~/onigiri";
	return [...new Set([`${root}/hold-prs.txt`, "/srv/onigiri/hold-prs.txt", "~/onigiri/sync/hold-prs.txt"])];
}

export function isHoldsPath(p: string, holds: string[], home = os.homedir()): boolean {
	const expand = (q: string) => q.replace(/^~(?=\/|$)/, home);
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
				if ((e as { code?: string }).code !== "ENOTDIR") return undefined;
			}
			if (st?.isSymbolicLink()) {
				if (++links > 40) return undefined;
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
