#!/usr/bin/env bash
set -euo pipefail

STAGE_DIR="${1:?Usage: host-deploy-public-assets.sh STAGE_DIR RELEASE}"
RELEASE="${2:?Usage: host-deploy-public-assets.sh STAGE_DIR RELEASE}"

# sudo preserves the caller's working directory, which may not be accessible
# to worldstage. All deployment paths below are absolute.
cd /

STATIC_ROOT="/opt/worldstage/static"
ASSET_ROOT="$STATIC_ROOT/assets"
CATALOGUE_ROOT="$STATIC_ROOT/catalogues"
COMPRESSOR="/opt/worldstage/compress-public-assets.sh"
CATALOGUE_BUILDER="/opt/worldstage/build-flag-catalog.py"
PYTHON="/opt/worldstage/venv/bin/python"

if [[ ! "$RELEASE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
    echo "Invalid asset release: $RELEASE" >&2
    exit 2
fi

EXPECTED_STAGE="/tmp/worldstage-public-$RELEASE"
if [[ "$STAGE_DIR" != "$EXPECTED_STAGE" ]]; then
    echo "Refusing unexpected staging directory: $STAGE_DIR" >&2
    exit 2
fi

for required_path in \
    "$STAGE_DIR/static" \
    "$STAGE_DIR/files/flags" \
    "$STAGE_DIR/files/favicons" \
    "$STAGE_DIR/files/robots.txt"; do
    if [[ ! -e "$required_path" ]]; then
        echo "Missing staged public asset: $required_path" >&2
        exit 1
    fi
done

install -d -m 0755 "$ASSET_ROOT" "$CATALOGUE_ROOT"

RELEASE_DIR="$ASSET_ROOT/$RELEASE"
CATALOGUE_PATH="$CATALOGUE_ROOT/$RELEASE.sqlite"
if [[ -e "$RELEASE_DIR" || -e "$CATALOGUE_PATH" ]]; then
    if [[ -d "$RELEASE_DIR" && -f "$CATALOGUE_PATH" ]]; then
        current_tmp="$STATIC_ROOT/.current-$RELEASE"
        rm -f -- "$current_tmp"
        ln -s "assets/$RELEASE" "$current_tmp"
        mv -Tf "$current_tmp" "$STATIC_ROOT/current"
        echo "Reused public asset release $RELEASE"
        exit 0
    fi
    echo "Incomplete public asset release already exists: $RELEASE" >&2
    exit 1
fi

RELEASE_TMP=$(mktemp -d "$ASSET_ROOT/.${RELEASE}.XXXXXX")
CATALOGUE_TMP=$(mktemp "$CATALOGUE_ROOT/.${RELEASE}.XXXXXX")
CURRENT_TMP="$STATIC_ROOT/.current-$RELEASE"
rm -f -- "$CURRENT_TMP"

cleanup() {
    rm -rf -- "$RELEASE_TMP" "$CATALOGUE_TMP" "$CURRENT_TMP"
}
trap cleanup EXIT

rsync -a --delete "$STAGE_DIR/static/" "$RELEASE_TMP/"
rsync -a --delete --exclude=old/ --exclude=wip/ \
    "$STAGE_DIR/files/flags/" "$RELEASE_TMP/flags/"
rsync -a --delete "$STAGE_DIR/files/favicons/" "$RELEASE_TMP/favicons/"
install -m 0644 "$STAGE_DIR/files/robots.txt" "$RELEASE_TMP/robots.txt"

"$PYTHON" "$CATALOGUE_BUILDER" \
    "$RELEASE_TMP/flags" \
    "$CATALOGUE_TMP" \
    "$RELEASE_TMP/flag-manifest.js"
"$COMPRESSOR" "$RELEASE_TMP"
chmod 0640 "$CATALOGUE_TMP"

mv "$RELEASE_TMP" "$RELEASE_DIR"
mv "$CATALOGUE_TMP" "$CATALOGUE_PATH"
ln -s "assets/$RELEASE" "$CURRENT_TMP"
mv -Tf "$CURRENT_TMP" "$STATIC_ROOT/current"

trap - EXIT

echo "Published public asset release $RELEASE"
