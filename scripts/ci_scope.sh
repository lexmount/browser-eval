#!/usr/bin/env bash
# Compare the entire PR merge tree with its base; unknown inputs require full CI.
set -euo pipefail

changed="$(mktemp)"
trap 'rm -f "$changed"' EXIT
docs_only=false
if [[ "${GITHUB_EVENT_NAME:-}" == pull_request ]] &&
    git diff --no-renames --name-only -z HEAD^1 HEAD -- > "$changed"; then
    docs_only=true
    has_changes=false
    while IFS= read -r -d '' path; do
        has_changes=true
        case "$path" in
            README.md|CONTRIBUTING.md|SECURITY.md|AGENTS.md|docs/*.md|docs/assets/*.png|docs/assets/*.jpg|docs/assets/*.jpeg|docs/assets/*.webp|docs/assets/*.gif) ;;
            *) docs_only=false; break ;;
        esac
    done < "$changed"
    if [[ "$has_changes" == false ]]; then
        docs_only=false
    fi
fi
printf 'docs_only=%s\n' "$docs_only"
