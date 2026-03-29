# Structural Search Playbook

## When to use structural search

Use ast-grep structural search instead of text search (rg/grep) when:

- **Pattern matching requires syntax awareness**: e.g., finding `console.log(...)` but not inside comments or strings
- **Large-scale code modification**: replacing deprecated API calls across many files
- **Anti-pattern detection**: finding code patterns that violate project conventions
- **Complete removal verification**: confirming a pattern is fully eliminated from the codebase
- **Repetitive code transformation**: applying the same structural change to many instances

Use text search (rg/grep) when:
- Searching for string literals, comments, or configuration values
- Simple filename or path matching
- The search target is not a code construct

## Pattern examples

### Deprecated API removal
```yaml
# Find all uses of deprecated function
sg scan --pattern 'oldFunction($$$ARGS)'

# Replace with new function
sg scan --pattern 'oldFunction($$$ARGS)' --rewrite 'newFunction($$$ARGS)'
```

### Anti-pattern detection
```yaml
# Find console.log in production code
sg scan --pattern 'console.log($$$)' --lang typescript

# Find @ts-ignore without explanation
sg scan --pattern '// @ts-ignore' --lang typescript

# Find broad 'any' type
sg scan --pattern ': any' --lang typescript
```

### Repetitive code replacement
```yaml
# Convert class components to functional
sg scan --pattern 'class $NAME extends Component { render() { return $BODY } }'

# Update import paths after module rename
sg scan --pattern "import { $$$NAMES } from 'old-module'" --rewrite "import { $$$NAMES } from 'new-module'"
```

## Verifying complete removal

After removing a pattern:
1. Run `sg scan --pattern '<the-pattern>' --lang <lang>`
2. If zero matches → pattern fully removed
3. If matches remain → review and handle each case
4. This is more reliable than `grep` for code patterns because ast-grep understands syntax

## Integration with harness flows

- **critic-runtime**: Can reference structural search results as evidence
- **maintain**: Can detect anti-pattern drift between task completions
- **verify**: Structural checks complement smoke/health tests

## Fallback when ast-grep unavailable

If `ast-grep` is not installed:
1. Use `rg` (ripgrep) with regex patterns
2. Results may include false positives from comments/strings
3. Manual verification needed for syntax-aware patterns
4. Consider installing ast-grep: `npm install -g @ast-grep/cli` or `cargo install ast-grep`

## Sample rules location

Pre-built sample rules are in `plugin/templates/ast-grep/rules/`:
- `no-console-log.yml` — detect console.log
- `no-ts-ignore.yml` — detect @ts-ignore
- `no-any-type.yml` — detect broad any usage
- `deprecated-import.yml` — template for import migration

These are starting points. Customize for your project's specific patterns and conventions.
