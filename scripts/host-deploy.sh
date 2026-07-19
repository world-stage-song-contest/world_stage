#!/usr/bin/env bash
set -euo pipefail

WHEEL="${1:?Usage: deploy.sh path/to/wheel.whl}"
VENV="/opt/worldstage/venv"

# Make sure the service user can read the wheel.
chmod 644 "$WHEEL"

# Install as the service user so the venv stays owned by worldstage.
sudo -H -u worldstage "$VENV/bin/pip" install --upgrade --force-reinstall "$WHEEL"

# Run migrations – bail if they fail, before touching the running service.
sudo systemctl start worldstage-migrate.service

# Graceful reload: new workers start, old ones finish in-flight requests.
sudo systemctl reload worldstage.service

echo "Deployed $WHEEL"
sudo systemctl status worldstage.service --no-pager
