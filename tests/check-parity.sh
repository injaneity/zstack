#!/bin/bash
set -u
src="$(cd "$(dirname "$0")/.." && pwd)"
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok    $1"; else echo "FAIL  $1: got '$2', want '$3'"; fail=1; fi; }
has() { case "$2" in *"$3"*) echo "ok    $1";; *) echo "FAIL  $1: '$2' lacks '$3'"; fail=1;; esac; }
tmp=$(cd "$(mktemp -d)" && pwd -P); trap 'rm -rf "$tmp"' EXIT
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

git init -q --bare -b main "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/seed" 2>/dev/null
mkdir -p "$tmp/seed/services" "$tmp/seed/agents" "$tmp/seed/bin" "$tmp/seed/config"
cp "$src/services/check-parity" "$src/services/pre-push" "$tmp/seed/services/"; cp "$src/agents/"*.md "$tmp/seed/agents/"
cp "$src/bin/policy" "$tmp/seed/bin/"; cp "$src/config/"*.toml "$tmp/seed/config/"; cp -R "$src/vendor" "$tmp/seed/"
cp "$src/.gitignore" "$tmp/seed/"
git -C "$tmp/seed" add -A; git -C "$tmp/seed" commit -qm one; git -C "$tmp/seed" push -q origin main
git clone -q "$tmp/origin.git" "$tmp/pkg"
git init -q "$tmp/root/app"
mkdir -p "$tmp/agent/agents"
hook="$tmp/root/app/.git/hooks/pre-push"
ln -s "$tmp/pkg/services/pre-push" "$hook"
run() { out=$(ZSTACK_CLONE="$tmp/root/app" PI_CODING_AGENT_DIR="$tmp/agent" "$tmp/pkg/services/check-parity" 2>&1); rc=$?; }
fails() { echo "$out" | grep -c '^FAIL'; }

run; check "a host on merged main passes" "$rc" 0
check "three ok lines" "$(echo "$out" | grep -c '^ok')" 3
cp "$tmp/pkg/agents/verifier.md" "$tmp/agent/agents/"
run; check "an agent file equal to the package's passes" "$rc" 0
rm "$tmp/agent/agents/verifier.md"

echo two > "$tmp/seed/f"; git -C "$tmp/seed" add f; git -C "$tmp/seed" commit -qm two; git -C "$tmp/seed" push -q origin main
run; check "a package behind main fails" "$rc" 1
has "it names both shas" "$out" "is at $(git -C "$tmp/pkg" rev-parse HEAD), origin/main is $(git -C "$tmp/seed" rev-parse HEAD)"
check "only the package line fails" "$(fails)" 1
git -C "$tmp/pkg" merge -q --ff-only origin/main

echo edit >> "$tmp/pkg/services/pre-push"
run; check "a local edit fails" "$rc" 1; has "it names the file" "$out" "local changes:  M services/pre-push"
git -C "$tmp/pkg" checkout -q -- services/pre-push
git -C "$tmp/pkg" checkout -q -b side
run; check "a branch other than main fails" "$rc" 1; has "it says so" "$out" "not on branch main"
git -C "$tmp/pkg" checkout -q main

rm "$hook"; cp "$tmp/pkg/services/pre-push" "$hook"
run; check "a copied hook fails, even an identical one" "$rc" 1; has "it names the hook" "$out" "FAIL  hook: $hook is not a symlink"
rm "$hook"; ln -s "$tmp/seed/services/pre-push" "$hook"
run; check "a hook linked into another checkout fails" "$rc" 1
rm "$hook"
run; check "no hook fails" "$rc" 1
ln -s "../../../../pkg/services/pre-push" "$hook"
out=$(cd / && ZSTACK_CLONE="$tmp/root/app" PI_CODING_AGENT_DIR="$tmp/agent" "$tmp/pkg/services/check-parity" 2>&1); rc=$?
check "a relative symlink to the package's hook passes from any directory" "$rc" 0
rm "$hook"
ln -s "$tmp/pkg/services/pre-push" "$hook"

echo old > "$tmp/agent/agents/verifier.md"; echo x > "$tmp/agent/agents/onigiri-reviewer.md"; echo x > "$tmp/agent/agents/.verifier.md.bak-1105"
run; check "stale, unknown and backup agent files fail" "$rc" 1
for f in verifier.md onigiri-reviewer.md .verifier.md.bak-1105; do has "it names $f" "$out" "$f"; done
check "only the agents line fails" "$(fails)" 1

exit $fail
