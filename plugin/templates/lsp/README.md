# LSP Symbol Lane

The symbol lane provides precise, index-backed code navigation for the harness plugin. It replaces text-search heuristics with Language Server Protocol queries for definition lookup, reference finding, safe rename, and diagnostics.

## What the Symbol Lane Provides

- **Definition lookup**: Jump to the exact definition of any symbol (function, class, variable, type).
- **Reference finding**: Enumerate every usage site of a symbol across the workspace.
- **Safe rename**: Atomically rename a symbol at all reference sites, with pre-flight validation.
- **Diagnostics**: Retrieve type errors and warnings for a file without running a full build.
- **Workspace symbol search**: Search for symbols by name across the entire project.
- **Call site tracing**: Identify all callers of a function for impact analysis before API changes.

## cclsp vs Native LSP

| Provider | Description | When Active |
|----------|-------------|-------------|
| **cclsp** | Claude Code LSP integration — uses MCP LSP tools registered in `.mcp.json` | `cclsp_ready: true` in manifest |
| **Native LSP** | Direct LSP server binary (e.g. `typescript-language-server`, `gopls`) | `lsp_ready: true` in manifest |

cclsp is preferred when available because it integrates directly with the Claude Code tool surface. Native LSP is the fallback when cclsp is not configured.

## Setting Up LSP for Your Project

1. Install the LSP server binary for your language (see Supported Languages below).
2. Verify the binary is on your PATH: `which typescript-language-server`
3. Run `plugin/scripts/lsp_detect.py` to confirm detection.
4. Optionally copy `plugin/templates/lsp/.lsp.json.optional` to `.lsp.json` and customize server arguments.
5. Set `lsp_ready: true` in `.claude/harness/manifest.yaml` to activate the symbol lane.

For cclsp setup, ensure the MCP LSP tools are registered in `.mcp.json` and set `cclsp_ready: true` in the manifest.

## Supported Languages

| Language | Server Binary | Install |
|----------|--------------|---------|
| TypeScript / JavaScript | `typescript-language-server` | `npm i -g typescript-language-server typescript` |
| Python | `pyright-langserver` | `pip install pyright` |
| Python (alt) | `pylsp` | `pip install python-lsp-server` |
| Go | `gopls` | `go install golang.org/x/tools/gopls@latest` |
| Rust | `rust-analyzer` | `rustup component add rust-analyzer` |
| Java | `jdtls` | Download Eclipse JDT LS |

## How the Harness Uses Symbol Info

When `symbol_lane_enabled: true` is set in the manifest profile, the harness routes symbol-related queries through LSP tools instead of grep:

- **Definition queries** use `lsp_goto_definition`
- **Reference queries** use `lsp_find_references`
- **Rename operations** use `lsp_prepare_rename` + `lsp_rename`
- **Type-check verification** uses `lsp_diagnostics`

Run `plugin/scripts/symbol_lane_hint.py <query-type>` to get routing hints for a specific query type. Valid query types: `definition`, `references`, `rename`, `callsite`, `type-usage`, `symbols`, `diagnostics`.
