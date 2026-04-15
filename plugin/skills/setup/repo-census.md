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
[ -f .mcp.json ] && echo "HAS_MCP_CONFIG: yes" && cat .mcp.json || echo "HAS_MCP_CONFIG: no"
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
```

Proceed to Phase 2 with: "Here's what I found about this project: ..."
