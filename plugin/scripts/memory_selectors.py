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


def _split_identifier(word):
    """Decompose snake_case, kebab-case, camelCase into constituent words."""
    # Split on underscores and hyphens
    parts = re.split(r'[_\-]', word)
    result = []
    for part in parts:
        if not part:
            continue
        # Split camelCase: insert space before uppercase letters following lowercase
        camel_split = re.sub(r'([a-z])([A-Z])', r'\1 \2', part)
        result.extend(camel_split.lower().split())
    return result


def extract_keywords(prompt):
    """Extract meaningful keywords from a prompt string.

    Handles:
    - Unicode / CJK characters (Korean, Japanese, Chinese, etc.)
    - File path segments (split on /)
    - snake_case, kebab-case, camelCase decomposition
    - 2-gram and 3-gram fallback for short prompts
    - English stopword filtering
    """
    if not prompt:
        return []

    seen = set()
    result = []

    def add_kw(w):
        if w and w not in seen and len(w) > 1:
            seen.add(w)
            result.append(w)

    # 1. Extract file paths (sequences with /) and keep them as-is + split segments
    # Use ASCII-only character class to avoid matching Unicode word chars
    path_pattern = re.compile(r'[a-zA-Z0-9_.@\-]+(?:/[a-zA-Z0-9_.@\-]+)+')
    for path_match in path_pattern.finditer(prompt):
        path_str = path_match.group(0).strip('/')
        add_kw(path_str)
        for seg in path_str.split('/'):
            seg = seg.lower()
            if seg:
                add_kw(seg)
                # Also decompose each segment
                for part in _split_identifier(seg):
                    if part not in STOPWORDS and len(part) > 2:
                        add_kw(part)

    # 2. Extract ASCII identifiers (snake_case, camelCase, kebab-case, plain words)
    ascii_words = re.findall(r'[a-zA-Z][a-zA-Z0-9_\-]*', prompt)
    for word in ascii_words:
        lower = word.lower()
        # Keep the full identifier
        if lower not in STOPWORDS and len(lower) > 2:
            add_kw(lower)
        # Decompose compound identifiers — pass original (pre-lowercase) word
        # so camelCase boundaries are preserved before lowercasing
        parts = _split_identifier(word)
        for part in parts:
            if part not in STOPWORDS and len(part) > 2:
                add_kw(part)

    # 3. Extract CJK / Unicode non-ASCII sequences
    # CJK Unified Ideographs, Hangul, Hiragana, Katakana, etc.
    unicode_words = re.findall(
        r'[^\x00-\x7F\s\.,!?;:\'"()\[\]{}<>/@#$%^&*+=|\\~`]+',
        prompt
    )
    for w in unicode_words:
        w = w.strip()
        if w:
            add_kw(w)
            # For CJK, also add individual characters if the word is long
            # (Korean/CJK words are often single characters or short sequences)
            if len(w) > 1:
                for ch in w:
                    if ord(ch) > 0x7F:
                        add_kw(ch)

    # 4. N-gram fallback for short prompts (fewer than 3 keywords so far)
    if len(result) < 3:
        # Tokenize entire prompt into words for n-grams
        all_tokens = re.findall(r'\S+', prompt.lower())
        filtered = [t for t in all_tokens if t not in STOPWORDS and len(t) > 2]
        # 2-grams
        for i in range(len(filtered) - 1):
            bigram = filtered[i] + ' ' + filtered[i + 1]
            add_kw(bigram)
        # 3-grams
        for i in range(len(filtered) - 2):
            trigram = filtered[i] + ' ' + filtered[i + 1] + ' ' + filtered[i + 2]
            add_kw(trigram)

    return result


FRESHNESS_WEIGHTS = {
    "current": 1.0,
    "suspect": 0.5,
    "stale": 0.1,
    "superseded": 0.0,
}


def parse_note_frontmatter(content):
    """Extract YAML-like header fields from note content."""
    result = {
        "freshness": "current",
        "status": "active",
        "summary": "",
        "verified_at": "",
        "derived_from": "",
        "invalidated_by_paths": "",
    }
    if not content.startswith("---"):
        return result
    end = content.find("---", 3)
    if end == -1:
        return result
    header = content[3:end]
    for line in header.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if key in result:
                result[key] = val
    return result


def parse_note_metadata(frontmatter_text):
    """Parse optional retrieval-metadata fields from note frontmatter text.

    Handles: root, lane, path_scope (block list), topic_tags (block list).
    Returns dict with defaults when fields are absent.
    """
    result = {
        "root": "common",
        "lane": None,
        "path_scope": [],
        "topic_tags": [],
    }
    if not frontmatter_text:
        return result

    lines = frontmatter_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()

            if key == "root" and val:
                result["root"] = val
            elif key == "lane" and val:
                result["lane"] = val
            elif key in ("path_scope", "topic_tags"):
                # Check for inline list: path_scope: [a, b]
                if val.startswith("["):
                    inner = val.strip("[]")
                    items = [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
                    result[key] = items
                else:
                    # Block list: next lines starting with "  -"
                    block_items = []
                    j = i + 1
                    while j < len(lines):
                        next_line = lines[j]
                        m = re.match(r'^\s+-\s+(.*)', next_line)
                        if m:
                            item = m.group(1).strip().strip('"').strip("'")
                            if item:
                                block_items.append(item)
                            j += 1
                        else:
                            break
                    result[key] = block_items
                    i = j
                    continue
        i += 1

    return result


def _extract_frontmatter_text(content):
    """Extract just the frontmatter text (between --- delimiters) from note content."""
    if not content.startswith("---"):
        return ""
    end = content.find("---", 3)
    if end == -1:
        return ""
    return content[3:end]


def keyword_match_ratio(keywords, text):
    """Compute fraction of keywords present in text. Returns 0.0-1.0."""
    if not keywords or not text:
        return 0.0
    text_lower = text.lower()
    matches = sum(1 for k in keywords if k in text_lower)
    return matches / len(keywords)


def compute_path_overlap(keywords, path_scope):
    """Score overlap between extracted keywords and note's path_scope list."""
    if not keywords or not path_scope:
        return 0.0
    # Flatten path_scope into segments
    scope_tokens = set()
    for p in path_scope:
        scope_tokens.add(p.lower())
        for seg in p.lower().split("/"):
            if seg:
                scope_tokens.add(seg)
    kw_set = set(k.lower() for k in keywords)
    overlap = kw_set & scope_tokens
    if not scope_tokens:
        return 0.0
    return min(1.0, len(overlap) / max(len(kw_set), 1))


def score_note(keywords, note_text, note_metadata, query_context):
    """Multi-signal note scoring. Returns 0.0-1.0.

    Signals (weights sum to 1.0):
      lexical      0.40  — keyword match ratio
      freshness    0.25  — freshness weight (current/suspect/stale/superseded)
      root_match   0.15  — whether note root is in active_roots
      path_overlap 0.10  — overlap between keywords and note's path_scope
      lane_match   0.10  — whether note lane matches current_lane
    """
    lexical = keyword_match_ratio(keywords, note_text)
    freshness = FRESHNESS_WEIGHTS.get(note_metadata.get("freshness", "current"), 1.0)
    active_roots = query_context.get("active_roots", [])
    note_root = note_metadata.get("root", "common")
    root_match = 1.0 if note_root in active_roots else 0.5
    path_overlap = compute_path_overlap(keywords, note_metadata.get("path_scope", []))
    current_lane = query_context.get("current_lane")
    note_lane = note_metadata.get("lane")
    if current_lane and note_lane:
        lane_match = 1.0 if note_lane == current_lane else 0.7
    else:
        lane_match = 0.7  # unknown lane — neutral

    return (lexical * 0.4 + freshness * 0.25 + root_match * 0.15
            + path_overlap * 0.1 + lane_match * 0.1)


def score_relevance(keywords, text):
    """Score how relevant a text is to a set of keywords. Returns 0.0-1.0.
    Legacy single-signal scorer kept for select_active_tasks compatibility."""
    if not keywords or not text:
        return 0.0
    text_lower = text.lower()
    matches = sum(1 for k in keywords if k in text_lower)
    return matches / len(keywords)


def _get_registered_roots(doc_base="doc"):
    """Return list of root names from manifest's registered_roots, or scan doc/*."""
    manifest_path = ".claude/harness/manifest.yaml"
    roots = []

    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as f:
                content = f.read()
            # Look for registered_roots: [a, b, c]  or block list
            m = re.search(r'^registered_roots:\s*\[([^\]]*)\]', content, re.MULTILINE)
            if m:
                items = [x.strip().strip('"').strip("'") for x in m.group(1).split(",") if x.strip()]
                roots = items
            else:
                # Try block list
                lines = content.split("\n")
                in_field = False
                for line in lines:
                    if re.match(r'^registered_roots:', line):
                        in_field = True
                        continue
                    if in_field:
                        bm = re.match(r'^\s+-\s+(.*)', line)
                        if bm:
                            val = bm.group(1).strip().strip('"').strip("'")
                            if val:
                                roots.append(val)
                        elif line.strip() and not line.strip().startswith("-"):
                            break
        except OSError:
            pass

    # If no registered_roots in manifest, scan doc/* directories
    if not roots:
        if os.path.isdir(doc_base):
            for entry in os.listdir(doc_base):
                if os.path.isdir(os.path.join(doc_base, entry)):
                    roots.append(entry)

    # Always ensure "common" is present
    if "common" not in roots:
        roots.insert(0, "common")

    return roots


def _get_first_line(content):
    """Extract the first meaningful non-frontmatter line from note content."""
    lines = content.split("\n")
    in_header = content.startswith("---")
    past_header = not in_header
    for line in lines:
        if not past_header:
            if line.strip() == "---" and lines.index(line) > 0:
                past_header = True
            continue
        stripped = line.strip("# ").strip()
        if stripped:
            return stripped
    return lines[0].strip("# ").strip() if lines else ""


def select_relevant_notes(prompt, notes_dir=None, query_context=None):
    """Find notes across all doc/* directories matching prompt keywords.

    Returns list of (path, weighted_score, first_line, freshness, root_name).

    Falls back to doc/common only when manifest has no registered_roots and
    doc/* scan yields nothing.

    Backward compatible: notes without new metadata fields use defaults.
    """
    keywords = extract_keywords(prompt)

    if query_context is None:
        query_context = {}

    # Determine which roots to scan
    doc_base = "doc"
    if notes_dir is not None:
        # Legacy single-directory call — still supported
        roots_dirs = [(os.path.basename(notes_dir), notes_dir)]
    else:
        root_names = _get_registered_roots(doc_base)
        roots_dirs = []
        for rname in root_names:
            rpath = os.path.join(doc_base, rname)
            if os.path.isdir(rpath):
                roots_dirs.append((rname, rpath))

    # Populate active_roots in query_context if not provided
    if "active_roots" not in query_context:
        query_context = dict(query_context)
        query_context["active_roots"] = [r for r, _ in roots_dirs]

    if not keywords:
        # No keywords — nothing to score
        if not roots_dirs:
            return []
        # Still need to check if any directory exists
        return []

    scored = []
    for root_name, root_dir in roots_dirs:
        if not os.path.isdir(root_dir):
            continue
        for fname in os.listdir(root_dir):
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root_dir, fname)
            try:
                with open(fpath, encoding="utf-8") as f:
                    content = f.read(1500)  # Slightly more for richer metadata
            except Exception:
                continue

            frontmatter = parse_note_frontmatter(content)
            # Skip superseded notes entirely
            if frontmatter["status"] == "superseded":
                continue

            # Parse extended metadata (optional fields)
            fm_text = _extract_frontmatter_text(content)
            metadata = parse_note_metadata(fm_text)
            # Merge core freshness from parse_note_frontmatter
            metadata["freshness"] = frontmatter["freshness"]
            metadata["status"] = frontmatter["status"]
            # If root not set in frontmatter, use directory name
            if metadata.get("root") == "common" and root_name != "common":
                # Only override default if the note doesn't explicitly set root
                if "root:" not in fm_text:
                    metadata["root"] = root_name

            weighted_score = score_note(keywords, content, metadata, query_context)

            if weighted_score > 0.1:
                first_line = _get_first_line(content)
                scored.append((fpath, weighted_score, first_line,
                               frontmatter["freshness"], root_name))
        # end for fname
    # end for root_name

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:2]  # Top 2


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
    return tasks[:1]


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
    return verdicts[:1]


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
