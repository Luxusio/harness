---
name: knowledge
description: Find, record and maintain shared business knowledge through the confluence MCP, including native document edits, comments and history. Use for company/project knowledge questions or document work when this connection is configured.
---

Use the `confluence` MCP for business documents. Its shared local corpus is a
read projection managed automatically; do not edit corpus files or create
competing repository copies. Runtime instructions, source README/API assets and
Harness execution evidence remain repository-owned.

Search for relevant documents, inspect the returned passages and expand the
necessary sections or source documents. Distinguish missing results from absent
knowledge; respect freshness, truncation and unavailable scope. Historical and
production claims need the applicable source/version/environment, not the latest
edit timestamp. A search snippet alone may omit exceptions.

Record user statements, questions and hypotheses as attributed source material;
keep AI interpretations/proposals distinct from requested or confirmed facts.
Never record secrets or unrelated personal information. Reuse existing topic
documents rather than creating a page per utterance. A configured memory
destination permits concise append; unavailable or ambiguous destinations must
not be guessed or silently replaced.

For edits, read the actual version and use the smallest supported unambiguous
native edit. Never roundtrip an entire Markdown projection over rich storage.
Preserve macros, images, anchors, inline markers and unrelated content. Follow
conflict/unknown responses; an uncertain write is not permission to retry.
Verified remote success with local publication failure requires replica recovery,
not another mutation. The plugin normally updates its replica after a write.

Use backend-specific comments/history when necessary. A resolved comment is not
proof that its suggestion was accepted. Remote read permission alone does not
authorize copying restricted material into a broader audience.

The agent that learned the facts writes the knowledge. For substantive knowledge
consolidation, correction or migration, use the independent document reviewer at
`../../agents/document-reviewer.md`; a short source append does not require a
new development workflow. No WBS/Jira issue is required for ordinary wiki work.

