#!/usr/bin/env python3
"""Memory selection helpers for prompt_memory.py."""
import os
import re

# Common English stopwords for keyword extraction
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "it",
    "they", "them", "this", "that", "these", "those", "what", "which",
    "who", "whom", "where", "when", "why", "how",
    "and", "but", "or", "not", "no", "nor", "so", "if", "then",
    "to", "of", "in", "on", "at", "by", "for", "with", "from",
    "up", "out", "off", "into", "over", "about", "after", "before",
    "all", "each", "every", "both", "few", "more", "most", "some",
    "just", "also", "very", "too", "quite", "rather",
    "please", "help", "want", "like", "make", "get", "let",
}

def extract_keywords(prompt):
    """Extract meaningful keywords from a prompt string."""
    if not prompt:
        return []
    # Lowercase, split on non-alphanumeric
    words = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', prompt.lower())
    # Filter stopwords and very short words
    keywords = [w for w in words if w not in STOPWORDS and len(w) > 2]
    # Deduplicate while preserving order
    seen = set()
    result = []
    for k in keywords:
        if k not in seen:
            seen.add(k)
            result.append(k)
    return result

def score_relevance(keywords, text):
    """Score how relevant a text is to a set of keywords. Returns 0.0-1.0."""
    if not keywords or not text:
        return 0.0
    text_lower = text.lower()
    matches = sum(1 for k in keywords if k in text_lower)
    return matches / len(keywords)

def select_relevant_notes(prompt, notes_dir="doc/common"):
    """Find notes in notes_dir matching prompt keywords. Returns list of (path, score, first_line)."""
    keywords = extract_keywords(prompt)
    if not keywords or not os.path.isdir(notes_dir):
        return []

    scored = []
    for fname in os.listdir(notes_dir):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(notes_dir, fname)
        try:
            with open(fpath) as f:
                content = f.read(1000)  # First 1000 chars
            score = score_relevance(keywords, content)
            if score > 0.1:  # Minimum threshold
                first_line = content.split("\n")[0].strip("# ").strip()
                scored.append((fpath, score, first_line))
        except Exception:
            continue

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:3]  # Top 3

def select_active_tasks(prompt, task_dir=".claude/harness/tasks"):
    """Find active tasks relevant to prompt. Returns list of (task_id, status, relevance)."""
    keywords = extract_keywords(prompt)
    if not os.path.isdir(task_dir):
        return []

    tasks = []
    for entry in os.listdir(task_dir):
        if not entry.startswith("TASK__"):
            continue
        state_file = os.path.join(task_dir, entry, "TASK_STATE.yaml")
        plan_file = os.path.join(task_dir, entry, "PLAN.md")

        if not os.path.isfile(state_file):
            continue

        try:
            with open(state_file) as f:
                state_content = f.read()
        except Exception:
            continue

        # Skip closed tasks
        if any("status: {}".format(s) in state_content for s in ["closed", "archived", "stale"]):
            continue

        status = ""
        for line in state_content.split("\n"):
            if line.startswith("status:"):
                status = line.split(":", 1)[1].strip()
                break

        # Score against plan if available
        score = 0.0
        if keywords:
            score = score_relevance(keywords, state_content)
            if os.path.isfile(plan_file):
                try:
                    with open(plan_file) as f:
                        plan_content = f.read(2000)
                    plan_score = score_relevance(keywords, plan_content)
                    score = max(score, plan_score)
                except Exception:
                    pass

        tasks.append((entry, status, score))

    tasks.sort(key=lambda x: x[2], reverse=True)
    return tasks[:3]

def select_recent_verdicts(task_dir=".claude/harness/tasks"):
    """Get most recent critic verdicts. Returns list of (task_id, verdict_type, verdict)."""
    if not os.path.isdir(task_dir):
        return []

    verdicts = []
    entries = sorted(os.listdir(task_dir), reverse=True)
    for entry in entries[:5]:
        if not entry.startswith("TASK__"):
            continue
        for critic_type in ["runtime", "plan", "document"]:
            critic_file = os.path.join(task_dir, entry, "CRITIC__{}.md".format(critic_type))
            if os.path.isfile(critic_file):
                try:
                    with open(critic_file) as f:
                        content = f.read(200)
                    if "verdict: PASS" in content:
                        verdicts.append((entry, critic_type, "PASS"))
                    elif "verdict: FAIL" in content:
                        verdicts.append((entry, critic_type, "FAIL"))
                    elif "verdict: BLOCKED_ENV" in content:
                        verdicts.append((entry, critic_type, "BLOCKED_ENV"))
                except Exception:
                    continue
    return verdicts[:5]

def format_context(items, max_chars=500):
    """Format selected context items into a compact string."""
    if not items:
        return ""
    parts = []
    total = 0
    for item in items:
        if isinstance(item, str):
            text = item
        elif isinstance(item, tuple):
            text = " | ".join(str(x) for x in item)
        else:
            text = str(item)
        if total + len(text) + 3 > max_chars:
            break
        parts.append(text)
        total += len(text) + 3
    return " | ".join(parts)
