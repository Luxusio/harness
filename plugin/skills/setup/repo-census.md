# Phase 1: Repo Census

Sub-file for setup/SKILL.md. Non-destructive detection only.

---

## 1.1 Project type detection

```bash
_TYPE="unknown"

# Web frontend signals
_HAS_FRONTEND="no"
if [ -f package.json ]; then
  _DEPS=$(cat package.json)
  for fw in next react vite vue nuxt svelte astro angular remix solid gatsby; do
    echo "$_DEPS" | grep -q "\"$fw\"" && _HAS_FRONTEND="yes" && echo "FRONTEND_SIGNAL: $fw"
  done
fi

# Structure signals
for d in src/app src/pages app/ pages/ public/; do
  [ -d "$d" ] && echo "STRUCTURE_SIGNAL: $d"
done

# Config signals
for f in vite.config.* next.config.* nuxt.config.* astro.config.* angular.json; do
  ls $f 2>/dev/null && echo "CONFIG_SIGNAL: $f"
done

# API-only signals
if [ -f package.json ]; then
  for srv in express fastify @nestjs/core; do
    echo "$_DEPS" | grep -q "\"$srv\"" && echo "API_SIGNAL: $srv"
  done
fi

# Test infrastructure
[ -f jest.config.* ] || [ -f vitest.config.* ] || [ -f pytest.ini ] || [ -f .rspec ] && echo "HAS_TESTS: yes"
ls .github/workflows/*.yml 2>/dev/null && echo "HAS_CI: yes"

# Browser testing infra
which chromium 2>/dev/null && echo "BROWSER: chromium" || which google-chrome 2>/dev/null && echo "BROWSER: chrome" || which chromium-browser 2>/dev/null && echo "BROWSER: chromium-browser" || echo "BROWSER: none"
[ -f .mcp.json ] && echo "HAS_MCP_CONFIG: yes" || echo "HAS_MCP_CONFIG: no"
grep -q "chrome-devtools" .mcp.json 2>/dev/null && echo "CHROME_MCP: configured" || echo "CHROME_MCP: not_configured"

# API testing infra
which curl 2>/dev/null && echo "HAS_CURL: yes" || echo "HAS_CURL: no"
which httpie 2>/dev/null || which http 2>/dev/null && echo "HAS_HTTPLIB: yes" || echo "HAS_HTTPLIB: no"

# Dev server detection
for cmd in "npm run dev" "yarn dev" "pnpm dev" "bun run dev" "npm start" "yarn start"; do
  _BASE_CMD=$(echo "$cmd" | awk '{print $2}')
  if [ -f package.json ]; then
    grep -q "\"$_BASE_CMD\"" package.json 2>/dev/null && echo "DEV_COMMAND: $cmd" && break
  fi
done
[ -f manage.py ] && echo "DEV_COMMAND: python manage.py runserver"
[ -f go.mod ] && echo "DEV_COMMAND: go run ."

# Monorepo signals
[ -f pnpm-workspace.yaml ] || [ -f lerna.json ] || ([ -f package.json ] && grep -q workspaces package.json 2>/dev/null) && echo "MONOREPO: yes"
```

## 1.2 Build/test command detection

```bash
if [ -f package.json ]; then
  echo "--- SCRIPTS ---"
  python3 -c "import json; scripts=json.load(open('package.json')).get('scripts',{}); [print(f'{k}: {v}') for k,v in scripts.items()]" 2>/dev/null
fi
[ -f Makefile ] && echo "--- MAKEFILE TARGETS ---" && grep -E '^[a-zA-Z_-]+:' Makefile | head -10
```

## 1.3 Census summary

```
CENSUS RESULTS:
  Project: {name}
  Type: {detected type}
  Languages: {detected}
  Build: {command or "not detected"}
  Test: {command or "not detected"}
  CI: {yes/no}
  Frontend: {framework or "none"}
  Monorepo: {yes/no}
  Browser: {chromium|chrome|none}
  Chrome MCP: {configured|not_configured}
  Dev command: {detected or "not detected"}
  Source Git roots: {workspace-relative roots, or "."}
```

For a non-Git control root, record only the exact child Git roots detected
during setup. Store their normalized workspace-relative paths in
`manifest.yaml` as `source_git_roots`. Do not rediscover descendants at
runtime. Reject absolute paths, `..`, symlinks, duplicates, nested roots, and
directories whose `git rev-parse --show-toplevel` does not equal the directory.
Root names must match `[A-Za-z0-9._/-]+`; reject spaces, quotes, substitutions,
and shell metacharacters instead of interpolating filesystem names into a
command.
Record each detected child build/test command in a form executable from the
control root, such as `cd pay-api && ./gradlew test` or
`cd pay-webapp && npm test`. Keep all detected source test commands for
`verify_commands` and automatic Health scoring; do not collapse a fullstack
workspace to only one repository's test command. Render the validated root with
`shlex.quote("./" + root)` and serialize the complete command as a quoted YAML
scalar so a leading-dash root cannot be interpreted as a `cd` option.

Proceed to Phase 2 with: "Here's what I found about this project: ..."
