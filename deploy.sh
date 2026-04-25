#!/usr/bin/env bash
set -euo pipefail

SERVER="ws"

# Build a fresh wheel
python -m build --wheel

# Grab the most recent wheel
WHEEL=$(ls -t dist/*.whl | head -n1)
REMOTE_WHEEL="/tmp/$(basename "$WHEEL")"

echo "Deploying $WHEEL to $SERVER"

rsync "$WHEEL" "$SERVER:$REMOTE_WHEEL"
ssh -t "$SERVER" "/opt/worldstage/deploy.sh $REMOTE_WHEEL && rm $REMOTE_WHEEL"