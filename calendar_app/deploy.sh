#!/bin/bash
# Install or update the calendar on the frame over SSH (run on the Mac):
#   bash calendar_app/deploy.sh 'fe80::xxxx%en26'
# Copies the skycal and skyadmin packages and their service units, creates the service user,
# installs the firewall, the usb0 network file and the USB drive service from port/rootfs/files
# (plus the dosfstools and mtools packages if the frame lacks them), and restarts the services. /etc/skycal/config.toml is created from config.example.toml only if it is missing;
# an existing config (which holds the secret calendar address) is never read or replaced.
set -euo pipefail
cd "$(dirname "$0")"
HOST=${1:?usage: deploy.sh <frame address>}
KEY=$HOME/.ssh/skylight_frame
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes "root@$HOST")

SYSFILES=../port/rootfs/files
python3 -m py_compile skycal/*.py skyadmin/*.py
tar -c --exclude __pycache__ skycal skyadmin skycal.service skyadmin.service config.example.toml \
    -C "$SYSFILES" etc/nftables.conf etc/systemd/network/10-usb0.network \
    usr/local/sbin/skylight-usb-drive etc/systemd/system/skylight-usb-drive.service | "${SSH[@]}" '
    set -eu
    new=/opt/skycal.new
    mkdir -p "$new" /opt/skycal /etc/skycal
    tar -x -C "$new" --no-same-owner -f -
    # Firewall: checked before anything is installed, loaded further down.
    nft -c -f "$new/etc/nftables.conf"
    id skycal >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin skycal
    [ -e /etc/skycal/config.toml ] || install -m 600 "$new/config.example.toml" /etc/skycal/config.toml
    install -m 644 "$new/skycal.service" /etc/systemd/system/skycal.service
    mkdir -p /opt/skycal/skycal
    install -m 644 "$new"/skycal/*.py /opt/skycal/skycal/
    install -m 644 "$new/skyadmin.service" /etc/systemd/system/skyadmin.service
    mkdir -p /opt/skycal/skyadmin
    install -m 644 "$new"/skyadmin/*.py /opt/skycal/skyadmin/
    install -m 644 "$new/etc/nftables.conf" /etc/nftables.conf
    nft -f /etc/nftables.conf
    install -m 644 "$new/etc/systemd/network/10-usb0.network" /etc/systemd/network/10-usb0.network
    systemctl daemon-reload
    systemctl enable skycal.service skyadmin.service >/dev/null 2>&1
    systemctl restart skycal.service skyadmin.service
    sleep 8
    systemctl is-active skycal.service
    systemctl is-active skyadmin.service
    # The USB drive FRAME with the link to the settings page. Its tools are installed only if
    # missing (that needs the internet). On a kernel without the configfs gadget the service
    # builds the drive image and attaches nothing.
    if ! command -v mkfs.vfat >/dev/null 2>&1 || ! command -v mcopy >/dev/null 2>&1; then
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends dosfstools mtools >/dev/null
    fi
    install -m 644 "$new/usr/local/sbin/skylight-usb-drive" /usr/local/sbin/skylight-usb-drive
    install -m 644 "$new/etc/systemd/system/skylight-usb-drive.service" /etc/systemd/system/skylight-usb-drive.service
    systemctl daemon-reload
    systemctl enable skylight-usb-drive.service >/dev/null 2>&1
    systemctl restart skylight-usb-drive.service
    systemctl is-active skylight-usb-drive.service
    # Last, in case reconfiguring usb0 interrupts a session that runs over the cable.
    networkctl reload
'
echo "OK: skycal and skyadmin deployed to $HOST"
