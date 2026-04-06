#!/usr/bin/env bash
# fix-diff.sh — classify and fix CRLF-only modifications in the working tree
# Usage: ./fix-diff.sh
# CRLF-only files are fixed in-place and staged; real content changes are reported.

set -euo pipefail

# ── macOS ownership + quarantine fix ─────────────────────────────────────────
if [[ "$(uname)" == "Darwin" ]]; then
    CURRENT_USER="$(whoami)"
    sudo chown -R "${CURRENT_USER}:" . 2>/dev/null && echo "fix-diff: ownership fixed → ${CURRENT_USER}" || true
    xattr -rc . 2>/dev/null && echo "fix-diff: quarantine xattrs cleared" || true
fi

crlf_fixed=0
real_changes=0

while IFS= read -r file; do
    # Skip files that no longer exist on disk
    if [[ ! -f "$file" ]]; then
        continue
    fi

    # Check whether the file contains any CR byte
    if grep -qP "\r" "$file" 2>/dev/null; then
        sed -i 's/\r//' "$file"
        git add "$file"
        echo "fix-diff: CRLF-fixed (staged): $file"
        crlf_fixed=$((crlf_fixed + 1))
    else
        echo "fix-diff: real change (not staged): $file"
        real_changes=$((real_changes + 1))
    fi
done < <(git diff --name-only)

echo "fix-diff: ${crlf_fixed} CRLF-fixed (staged), ${real_changes} real changes (not staged)"
