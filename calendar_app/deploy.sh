#!/bin/bash
# Install or update the calendar on the frame over SSH (run on the Mac):
#   bash calendar_app/deploy.sh 'fe80::xxxx%en26'
# Copies the skycal package and the service unit, creates the service user, and restarts the
# service. /etc/skycal/config.toml is created from config.example.toml only if it is missing;
# an existing config (which holds the secret calendar address) is never read or replaced.
set -euo pipefail
cd "$(dirname "$0")"
HOST=${1:?usage: deploy.sh <frame address>}
KEY=$HOME/.ssh/skylight_frame
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes "root@$HOST")

python3 -m py_compile skycal/*.py
tar -c --exclude __pycache__ skycal skycal.service config.example.toml | "${SSH[@]}" '
    set -eu
    new=/opt/skycal.new
    mkdir -p "$new" /opt/skycal /etc/skycal
    tar -x -C "$new" --no-same-owner -f -
    id skycal >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin skycal
    [ -e /etc/skycal/config.toml ] || install -m 600 "$new/config.example.toml" /etc/skycal/config.toml
    install -m 644 "$new/skycal.service" /etc/systemd/system/skycal.service
    mkdir -p /opt/skycal/skycal
    install -m 644 "$new"/skycal/*.py /opt/skycal/skycal/
    systemctl daemon-reload
    systemctl enable skycal.service >/dev/null 2>&1
    systemctl restart skycal.service
    sleep 8
    systemctl is-active skycal.service
'
echo "OK: skycal deployed to $HOST"
