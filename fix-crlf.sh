#!/usr/bin/env bash
# fix-crlf.sh — Convert CRLF line endings to LF.
# Usage: fix-crlf.sh [path ...]
#   Defaults to current directory if no paths are given.
#   Excludes __pycache__ directories and binary file extensions.

set -euo pipefail

# Binary extensions to skip
BINARY_EXTS="pyc|png|jpg|jpeg|gif|ico|zip|tar|gz|bz2|xz|pdf|exe|so|o|a|dll"

# Paths to scan (default: current directory)
SCAN_PATHS=("${@:-.}")

fixed=0

for scan_path in "${SCAN_PATHS[@]}"; do
    # Build grep command to find files with CRLF, excluding __pycache__ dirs
    while IFS= read -r file; do
        # Skip binary extensions
        ext="${file##*.}"
        if [[ "$file" =~ \.($BINARY_EXTS)$ ]]; then
            continue
        fi

        # Convert CRLF -> LF in-place
        sed -i 's/\r//' "$file"
        echo "  fixed: $file"
        (( fixed++ )) || true
    done < <(grep -rlP "\r" "$scan_path" \
        --exclude-dir=__pycache__ \
        --exclude="*.pyc" \
        --exclude="*.png" \
        --exclude="*.jpg" \
        --exclude="*.jpeg" \
        --exclude="*.gif" \
        --exclude="*.ico" \
        --exclude="*.zip" \
        --exclude="*.tar" \
        --exclude="*.gz" \
        --exclude="*.bz2" \
        --exclude="*.xz" \
        --exclude="*.pdf" \
        --exclude="*.exe" \
        --exclude="*.so" \
        --exclude="*.o" \
        --exclude="*.a" \
        --exclude="*.dll" \
        2>/dev/null || true)
done

echo "fix-crlf: $fixed file(s) converted from CRLF to LF."
