#!/usr/bin/env bash

# Reuse one authenticated SSH connection for the whole deployment. The server's
# public SSH port regularly reaches MaxStartups due to unauthenticated traffic;
# opening a new connection for every rsync makes deployments fail at random.
if [[ -z "${DEPLOY_SSH_CONTROL_PATH:-}" ]]; then
    DEPLOY_SSH_CONTROL_DIR=$(mktemp -d /tmp/worldstage-deploy.XXXXXX)
    DEPLOY_SSH_CONTROL_PATH="$DEPLOY_SSH_CONTROL_DIR/control"
    export DEPLOY_SSH_CONTROL_PATH

    deploy_ssh_cleanup() {
        ssh -o ControlPath="$DEPLOY_SSH_CONTROL_PATH" -O exit "$SERVER" >/dev/null 2>&1 || true
        rmdir "$DEPLOY_SSH_CONTROL_DIR" 2>/dev/null || true
    }
    trap deploy_ssh_cleanup EXIT
fi

DEPLOY_SSH=(
    ssh
    -o ControlMaster=auto
    -o ControlPersist=60
    -o ControlPath="$DEPLOY_SSH_CONTROL_PATH"
    -o ConnectionAttempts=5
    -o ConnectTimeout=10
)
DEPLOY_RSYNC=(rsync -e "${DEPLOY_SSH[*]}")
