# Feedback-Derived Rule Capture

Harness develop flows now require a close-time judgment for user corrective feedback: `none`, `captured`, or `rejected`. Captured feedback must become a reusable conditional behavior rule, not an incident report. The expected writing style is readable prose: "When <trigger>, <action>. Verify by <observable check>."

The learning promotion script now renders `type="feedback-rule"` entries into readable Tier 2 pattern docs instead of dumping structured fields. Tier 1 promotion remains conservative and manual; this change forces judgment, not documentation spam.
