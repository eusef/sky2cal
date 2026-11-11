# Debian 13 (trixie) armhf root filesystem for the Skylight Frame 2.
# Built as a container image, then exported and turned into an ext4 image by build-rootfs.sh.
# Build context is port/rootfs/context (assembled by build-rootfs.sh, not in git).
FROM debian:trixie

ENV DEBIAN_FRONTEND=noninteractive

RUN sed -i 's/^Components: main$/Components: main non-free-firmware/' /etc/apt/sources.list.d/debian.sources \
 && grep -q 'non-free-firmware' /etc/apt/sources.list.d/debian.sources \
 && grep -q 'trixie-security' /etc/apt/sources.list.d/debian.sources \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      systemd-sysv systemd-timesyncd systemd-resolved udev dbus kmod \
      openssh-server nftables wpasupplicant iw wireless-regdb firmware-brcm80211 \
      unattended-upgrades needrestart apt-utils ca-certificates tzdata \
      iproute2 iputils-ping e2fsprogs dosfstools mtools less nano \
      python3 python3-pil python3-icalendar python3-recurring-ical-events python3-evdev \
      fonts-dejavu-core \
 && apt-get clean

# /etc/hostname, /etc/hosts and /etc/resolv.conf are mounted by Docker during the build, so
# build-rootfs.sh writes them into the exported tree instead.

# The base image blocks service starts and strips docs; a real system must start services.
RUN find /usr/sbin/policy-rc.d -delete

COPY files/ /
COPY authorized_keys /root/.ssh/authorized_keys
COPY nvram_ap6255.txt /usr/lib/firmware/brcm/brcmfmac43455-sdio.txt
# The calendar app, in the same places calendar_app/deploy.sh uses (deploy.sh updates it later).
COPY --chmod=644 skycal/*.py /opt/skycal/skycal/
COPY --chmod=644 skycal.service /etc/systemd/system/skycal.service
# The settings page served over the USB cable.
COPY --chmod=644 skyadmin/*.py /opt/skycal/skyadmin/
COPY --chmod=644 skyadmin.service /etc/systemd/system/skyadmin.service
COPY --chmod=600 config.example.toml /etc/skycal/config.toml

RUN set -e; \
    passwd -l root; \
    useradd --system --no-create-home --shell /usr/sbin/nologin skycal; \
    systemctl enable skycal.service skyadmin.service; \
    install -d -m 700 /root/.ssh; \
    install -m 600 /root/.ssh/authorized_keys /root/.ssh/authorized_keys.new; \
    mv /root/.ssh/authorized_keys.new /root/.ssh/authorized_keys; \
    [ -s /root/.ssh/authorized_keys ]; \
    find /etc/ssh -name 'ssh_host_*' -delete; \
    systemctl enable systemd-networkd systemd-resolved systemd-timesyncd nftables ssh \
        wpa_supplicant@wlan0 skylight-firstboot skylight-usb-drive unattended-upgrades apt-daily.timer apt-daily-upgrade.timer; \
    ln -sf /usr/share/zoneinfo/America/Los_Angeles /etc/localtime; \
    echo America/Los_Angeles > /etc/timezone; \
    systemctl disable wpa_supplicant; \
    systemctl mask serial-getty@ttyS2.service serial-getty@ttyGS0.service getty@tty1.service; \
    test -f /usr/lib/firmware/brcm/brcmfmac43455-sdio.bin
