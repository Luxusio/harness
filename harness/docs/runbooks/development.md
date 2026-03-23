# Development runbook

## Commands

- **Dev server:** `claude --plugin-dir ./plugin`
- **Build:** (none — plugin is config/docs only)
- **Test:** `claude --plugin-dir ./plugin --print 'list harness skills'`
- **Lint:** (none)

## Common tasks

- **Test plugin locally:** `claude --plugin-dir ./plugin` then invoke `/harness:setup`
- **Install via marketplace:** `/plugin marketplace add https://github.com/Luxusio/harness.git` then `/plugin install harness@harness`
- **Update marketplace clone:** `cd ~/.claude/plugins/marketplaces/harness && git fetch origin master && git reset --hard origin/master`

## Debugging notes

<!-- Add debugging insights from bug fixes -->

- Marketplace install: This repo uses Git-based marketplace add (`/plugin marketplace add <git-url>`). The relative plugin source (`./plugin`) in `marketplace.json` works with Git-based installs. Raw `marketplace.json` URL-based installs are not compatible with this repo's distribution shape.
- Validation scripts: `harness/scripts/validate.sh` and `harness/scripts/smoke.sh` use manifest commands (`harness/manifest.yaml > commands.*`) as their primary source. Auto-detect fallback is used only when a manifest command is empty.
