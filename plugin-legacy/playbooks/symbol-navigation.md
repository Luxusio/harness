# Symbol-Aware Navigation Playbook

## When to Use Symbol Lane vs Text Search

Use the **symbol lane** (LSP tools) when:
- You need the precise definition location of a function, class, or variable
- You are planning a rename and need all reference sites
- You are tracing call sites for an API that may be called under different aliases
- You need type/interface usage across the project
- Text search produces too many false positives (common names, string literals)

Use **text search** (grep/rg) when:
- LSP server is not available or not responding
- Searching for string literals, comments, or configuration values
- Doing a broad discovery scan before narrowing to symbols
- Cross-repo or cross-language search where LSP scope is incomplete

---

## Definition Lookup Strategy

Priority order: **cclsp** > **native LSP** > **grep fallback**

1. Check manifest flags: `cclsp_ready` and `lsp_ready`
2. If `cclsp_ready: true` — use `lsp_goto_definition` via cclsp provider
3. If `lsp_ready: true` — use `lsp_goto_definition` via native LSP server
4. Fallback: `grep -rn 'function NAME\|class NAME\|def NAME' .`

The LSP definition result returns an exact file path and line number. Prefer this over grep to avoid matching identically-named symbols in different scopes.

---

## Reference Finding for Safe Renames

Before renaming any symbol:

1. Call `lsp_prepare_rename` to verify the symbol is renameable at the cursor position.
2. Call `lsp_find_references` to enumerate every usage site.
3. Review the reference list — check for generated files, test fixtures, or external callers.
4. Call `lsp_rename` to apply the rename atomically across the workspace.

Never rename by grep-and-replace alone: it will catch string literals and comments that should not change, and miss dynamic call patterns that the LSP index correctly resolves.

---

## Callsite Tracing for API Changes

When modifying a function signature or deprecating an API:

1. Position cursor on the function definition.
2. Call `lsp_find_references` — this returns every call site.
3. For each call site, read the surrounding context to understand usage patterns.
4. Update call sites in dependency order (leaf callers first).

For large reference sets (50+), group by file and process file-by-file to avoid context overload.

---

## Type and Interface Usage Tracking

When refactoring a type or interface:

1. Call `lsp_find_references` on the type name.
2. Distinguish: type declarations vs type usages vs type imports.
3. Check for structural compatibility — LSP references include all nominal usages but may not surface structural duck-typing in dynamic languages.
4. For TypeScript: `lsp_diagnostics` after the change will surface type errors immediately.

---

## Multi-Language Partial Support

| Language | Definition | References | Rename | Diagnostics |
|----------|-----------|------------|--------|-------------|
| TypeScript/JS | Full | Full | Full | Full |
| Python | Full | Full | Partial | Full |
| Go | Full | Full | Full | Full |
| Rust | Full | Full | Full | Full |
| Java | Full | Full | Full | Partial |

Partial support means: works for most cases but may miss dynamic dispatch, reflection, or macro-generated code.

---

## Common Pitfalls

**Stale index**: LSP servers build an in-memory index. After large file operations (git checkout, bulk rename), the index may lag. If results seem wrong, wait a few seconds or restart the LSP server.

**Partial language support**: Some LSP servers do not implement `textDocument/references` fully. Fall back to grep if `lsp_find_references` returns an empty result unexpectedly.

**Monorepo roots**: LSP servers are typically scoped to a single project root. In a monorepo, you may need separate server instances per sub-package.

**Generated files**: LSP will include references in generated code. Filter these out when planning manual edits.

---

## Fallback Strategies

When the symbol lane is unavailable:

| Goal | Grep Fallback |
|------|--------------|
| Find definition | `grep -rn 'function NAME\|class NAME\|def NAME' .` |
| Find references | `grep -rn 'NAME' . --include='*.ts'` |
| Rename | grep all occurrences, review each, apply manually |
| Callsites | `grep -rn 'NAME(' .` (may include false positives) |
| Type usage | `grep -rn 'TypeName' . --include='*.ts'` |
| Diagnostics | Run `tsc --noEmit`, `pyright`, or `go vet` manually |

Always verify grep results manually — text search cannot distinguish symbol references from string literals or comments.
