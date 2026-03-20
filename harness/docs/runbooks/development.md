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
