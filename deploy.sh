#!/usr/bin/env bash
set -euo pipefail

SERVER="ws"
HOST_DEPLOY_SCRIPT="scripts/host-deploy.sh"
PUBLIC_ASSET_DEPLOY_SCRIPT="scripts/deploy-public-assets.sh"
ASSET_RELEASE="${ASSET_RELEASE:-$(git rev-parse --verify HEAD)}"

# Public files are deployed separately from the application wheel. Publish
# them first so the following application release never references a missing
# static asset release.
bash "$PUBLIC_ASSET_DEPLOY_SCRIPT" "$ASSET_RELEASE"

# Build a fresh wheel
python -m build --wheel

# Grab the most recent wheel
WHEEL=$(ls -t dist/*.whl | head -n1)
REMOTE_WHEEL="/tmp/$(basename "$WHEEL")"

echo "Deploying $WHEEL to $SERVER"

rsync "$WHEEL" "$HOST_DEPLOY_SCRIPT" "$SERVER:/tmp/"
ssh -t "$SERVER" "sudo install -o worldstage -g worldstage -m 0755 /tmp/host-deploy.sh /opt/worldstage/deploy.sh && /opt/worldstage/deploy.sh $REMOTE_WHEEL && rm $REMOTE_WHEEL /tmp/host-deploy.sh"
