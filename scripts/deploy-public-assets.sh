#!/usr/bin/env bash
set -euo pipefail

SERVER="ws"
RELEASE="${1:-${ASSET_RELEASE:-$(git rev-parse --verify HEAD)}}"
HOST_DEPLOY_SCRIPT="scripts/host-deploy-public-assets.sh"
COMPRESSOR_SCRIPT="scripts/compress-public-assets.sh"
CATALOGUE_BUILDER="scripts/build_flag_catalog.py"

if [[ ! "$RELEASE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
    echo "Invalid asset release: $RELEASE" >&2
    exit 2
fi

REMOTE_STAGE="/tmp/worldstage-public-$RELEASE"
EXCLUDES=(
    --exclude=.DS_Store
    --exclude=old/
    --exclude=wip/
    --exclude='*.br'
    --exclude='*.gz'
    --exclude='*.zst'
)

ssh "$SERVER" "rm -rf -- '$REMOTE_STAGE' && mkdir -p '$REMOTE_STAGE/files'"
rsync -a --delete "${EXCLUDES[@]}" world_stage/static/ "$SERVER:$REMOTE_STAGE/static/"
rsync -a --delete "${EXCLUDES[@]}" world_stage/files/flags/ "$SERVER:$REMOTE_STAGE/files/flags/"
rsync -a --delete "${EXCLUDES[@]}" world_stage/files/favicons/ "$SERVER:$REMOTE_STAGE/files/favicons/"
rsync -a world_stage/files/robots.txt "$SERVER:$REMOTE_STAGE/files/robots.txt"
rsync "$HOST_DEPLOY_SCRIPT" "$COMPRESSOR_SCRIPT" "$CATALOGUE_BUILDER" "$SERVER:/tmp/"

ssh "$SERVER" "sudo install -o worldstage -g worldstage -m 0755 /tmp/host-deploy-public-assets.sh /opt/worldstage/deploy-public-assets.sh && sudo install -o worldstage -g worldstage -m 0755 /tmp/compress-public-assets.sh /opt/worldstage/compress-public-assets.sh && sudo install -o worldstage -g worldstage -m 0755 /tmp/build_flag_catalog.py /opt/worldstage/build-flag-catalog.py && sudo -H -u worldstage /opt/worldstage/deploy-public-assets.sh '$REMOTE_STAGE' '$RELEASE' && rm -rf -- '$REMOTE_STAGE' /tmp/host-deploy-public-assets.sh /tmp/compress-public-assets.sh /tmp/build_flag_catalog.py"
