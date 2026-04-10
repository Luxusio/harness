# ast-grep Rules for Harness

## What are ast-grep rules?

ast-grep rules define structural code patterns for detection and transformation. Unlike text-based regex, ast-grep understands the syntax tree of your code, reducing false positives.

## How the harness uses these rules

The harness uses ast-grep as a **hint-based** tool:
- Rules suggest improvements but do not block execution
- The structural search lane activates automatically when ast-grep is detected
- Rules complement (not replace) existing critic and verify flows

## Adding custom rules

1. Create a `.yml` file in your project's rule directory (e.g., `.ast-grep/rules/`)
2. Define the pattern, language, message, and severity
3. Run `sg scan --rule <your-rule.yml>` to test

### Rule format

```yaml
id: unique-rule-id
language: typescript  # or javascript, python, go, rust, etc.
rule:
  pattern: "code pattern with $METAVAR placeholders"
fix: "optional replacement with $METAVAR references"  # omit for detection-only
message: "Human-readable description"
severity: error | warning | hint
```

### Metavariables

- `$NAME` — matches a single AST node
- `$$$NAME` — matches zero or more nodes (variadic)
- `$$NAME` — matches zero or one node (optional)

## Sample rules included

| Rule | What it detects |
|------|-----------------|
| `no-console-log.yml` | `console.log()` calls |
| `no-ts-ignore.yml` | `// @ts-ignore` comments |
| `no-any-type.yml` | Explicit `: any` type annotations |
| `deprecated-import.yml` | Template for import path migration |

## Resources

- [ast-grep documentation](https://ast-grep.github.io/)
- [Rule examples](https://ast-grep.github.io/catalog/)
- [Pattern syntax](https://ast-grep.github.io/guide/pattern-syntax.html)
