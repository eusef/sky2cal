#!/bin/bash
# Build the Debian 13 armhf root filesystem image for the Skylight Frame 2 (run on the Mac):
#   bash port/rootfs/build-rootfs.sh [ssh-public-key-file]
# Output: port/images/rootfs.ext4 (1536 MiB; grown to the partition on first boot).
set -euo pipefail
cd "$(dirname "$0")/../.."
KEY=${1:-$HOME/.ssh/id_ed25519.pub}
CTX=port/rootfs/context
OUT=port/images/rootfs.ext4
SIZE_MB=1536
die() { echo "FAIL: $*" >&2; exit 1; }

# A failed build must not leave an old image looking current: remove it first, and remove the
# partial image on any exit that is not a success.
mkdir -p port/images
cid=""
ok=0
cleanup() {
    [ -z "$cid" ] || docker rm -f "$cid" >/dev/null 2>&1 || true
    [ "$ok" = 1 ] || find port/images -maxdepth 1 \( -name rootfs.ext4 -o -name rootfs.ext4.tmp \) -delete
}
trap cleanup EXIT
find port/images -maxdepth 1 \( -name rootfs.ext4 -o -name rootfs.ext4.tmp \) -delete

[ -s "$KEY" ] || die "no SSH public key at $KEY"
case "$(cut -d' ' -f1 "$KEY")" in ssh-ed25519|ssh-rsa|ecdsa-*) ;; *) die "$KEY is not a public key";; esac
[ -s port/firmware/nvram_ap6255.txt ] || die "port/firmware/nvram_ap6255.txt is missing"

# Assemble the build context (not in git: it holds the key and the vendor nvram).
mkdir -p "$CTX" port/images
find "$CTX" -mindepth 1 -delete
cp -R port/rootfs/files "$CTX/files"
cp port/rootfs/Dockerfile "$CTX/Dockerfile"
cp "$KEY" "$CTX/authorized_keys"
cp port/firmware/nvram_ap6255.txt "$CTX/nvram_ap6255.txt"

docker build --platform linux/arm/v7 -t skylight-rootfs "$CTX"
cid=$(docker create --platform linux/arm/v7 skylight-rootfs /bin/true)

# Unpack as root inside a container so ownership and modes survive, then make the ext4 image.
# It gets its final name only after every check and e2fsck have passed.
docker export "$cid" | docker run --rm -i -v "$PWD/port/images":/img skylight-build bash -euo pipefail -c '
    command -v mke2fs >/dev/null || { apt-get update -qq >/dev/null; apt-get install -y -qq e2fsprogs >/dev/null; }
    mkdir /rootfs && tar -x -C /rootfs --numeric-owner -f -
    cd /rootfs
    [ ! -e .dockerenv ] || find .dockerenv -delete
    echo skylight > etc/hostname
    printf "127.0.0.1 localhost\n127.0.1.1 skylight\n::1 localhost ip6-localhost ip6-loopback\n" > etc/hosts
    find etc/resolv.conf -delete
    ln -s /run/systemd/resolve/stub-resolv.conf etc/resolv.conf
    install -m 600 -o 0 -g 0 etc/wpa_supplicant/wpa_supplicant-wlan0.conf etc/wpa_supplicant/w.new
    mv etc/wpa_supplicant/w.new etc/wpa_supplicant/wpa_supplicant-wlan0.conf
    find var/lib/apt/lists -type f -delete
    : > etc/machine-id
    [ ! -e etc/dpkg/dpkg.cfg.d/docker-apt-speedup ] || find etc/dpkg/dpkg.cfg.d/docker-apt-speedup -delete
    for t in apt-daily.timer apt-daily-upgrade.timer; do
        [ -L etc/systemd/system/timers.target.wants/$t ] || { echo "FAIL: $t is not enabled" >&2; exit 1; }
    done
    for f in usr/lib/systemd/systemd sbin/init usr/sbin/sshd usr/sbin/wpa_supplicant usr/bin/python3 root/.ssh/authorized_keys \
             usr/lib/firmware/brcm/brcmfmac43455-sdio.bin usr/lib/firmware/brcm/brcmfmac43455-sdio.txt; do
        [ -e "$f" ] || { echo "FAIL: $f missing from the root filesystem" >&2; exit 1; }
    done
    [ "$(stat -c %a root/.ssh)" = 700 ] && [ "$(stat -c %a root/.ssh/authorized_keys)" = 600 ] \
        || { echo "FAIL: ssh key modes" >&2; exit 1; }
    grep -q "^root:[!*]" etc/shadow || { echo "FAIL: root password is not locked" >&2; exit 1; }
    echo "root filesystem MiB: $(du -sm . | cut -f1)"
    mke2fs -q -t ext4 -L skylight -d /rootfs /img/rootfs.ext4.tmp '"$SIZE_MB"'M
    e2fsck -f -n /img/rootfs.ext4.tmp
'
mv "$OUT.tmp" "$OUT"
ok=1
ls -l "$OUT"
echo "OK: $OUT"
