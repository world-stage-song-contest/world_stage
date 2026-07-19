#!/usr/bin/env bash
set -euo pipefail

if (($# == 0)); then
    echo "Usage: compress-public-assets.sh ASSET_PATH [...]" >&2
    exit 2
fi

initial_directory="$PWD"
roots=()
for root in "$@"; do
    if [[ "$root" = /* ]]; then
        roots+=("$root")
    else
        roots+=("$initial_directory/$root")
    fi
done

# The deployment command changes user but inherits the invoking user's working
# directory. It may not be traversable by worldstage, and GNU find attempts to
# restore its initial directory before exiting even when given absolute paths.
cd /

for root in "${roots[@]}"; do
    [[ -e "$root" ]] || continue

    if [[ -d "$root" ]]; then
        find "$root" \
            -type d \( -name old -o -name wip \) -prune -o \
            -type f \( -name '*.br' -o -name '*.gz' -o -name '*.zst' \) \
            -exec rm -f -- {} +
    else
        rm -f -- "$root.br" "$root.gz" "$root.zst"
    fi

    compress_asset() {
        local asset="$1"
        case "$asset" in
            *.css|*.js|*.mjs|*.html|*.svg|*.txt|*.xml|*.json|*.map|*.webmanifest)
                /usr/bin/brotli --quality=11 --keep --force "$asset"
                /usr/bin/gzip --best --keep --force "$asset"
                /usr/bin/zstd --ultra -22 --quiet --keep --force "$asset"
                ;;
        esac
    }

    if [[ -d "$root" ]]; then
        while IFS= read -r -d '' asset; do
            compress_asset "$asset"
        done < <(
            find "$root" \
                -type d \( -name old -o -name wip \) -prune -o \
                -type f -print0
        )
    else
        compress_asset "$root"
    fi
done
