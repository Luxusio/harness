---
date: 2026-05-13
task: TASK__hygiene-defer-commit
kind: behavior-change
---

SessionStart hygiene no longer auto-commits archive moves. The hygiene scan classifies and stages archive moves; commits happen only when you invoke Skill(maintain), which now offers a batch commit prompt. A stale .git/index.lock self-cleanup runs at SessionStart for the case where a prior SessionStart was killed mid-write (size==0 + age>=60s + flock test, never touches active locks). This prevents the recurring index.lock corruption from the no-timeout git commit subprocess.
