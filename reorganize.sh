#!/usr/bin/env bash
set -euo pipefail

source_dir=${1:-new}

while IFS= read -r -d '' file; do
    relative_path=${file#"$source_dir"/}
    first_line=$(grep -m 1 '^category: ' "$file" || true)

    case "$first_line" in
        category:*)
            category=${first_line#category: }
            ;;
        *)
            printf 'Skipping %s: no category header\n' "$file" >&2
            continue
            ;;
    esac

    [[ -n "$category" ]] || {
        printf 'Skipping %s: empty category\n' "$file" >&2
        continue
    }

    relative_name=${relative_path//\//-}
    destination="$source_dir/$category/$relative_name"
    mkdir -p "$(dirname "$destination")"
    mv -- "$file" "$destination"
done < <(find "$source_dir" -type f -name '*.txt' -print0)

find "$source_dir" -type d -empty -delete
