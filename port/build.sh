#!/bin/bash
# Build the Skylight Frame 2 test kernel. Runs INSIDE the skylight-build container:
#   docker run --rm -v skylight-kbuild:/build -v "$PWD":/src skylight-build /src/port/build.sh
# Reads /src/port (dts, config fragment, initramfs). Outputs appear in /src/port/out only
# after every check has passed; any failure leaves that directory empty.
set -euo pipefail
V=6.18.63
SHA256=81764a545072d160748beff4d9de2e7d95aa9f16e8261110288f067a2cc61d71
K=/build/linux-$V
O=/build/out-$V
SRC=/src/port
OUT=$SRC/out
STAGE=/build/stage
# The stock loader puts the kernel at 0x60408000 and its device tree at 0x60f00000.
MAX_KERNEL=$((0x60f00000 - 0x60408000))

die() { echo "FAIL: $*" >&2; exit 1; }

# Whatever happens (error, interrupt), port/out ends up empty unless the script reaches the end.
ok=0
trap '[ "$ok" = 1 ] || { [ -d "$OUT" ] && find "$OUT" -mindepth 1 -delete; }' EXIT
mkdir -p "$OUT"
find "$OUT" -mindepth 1 -delete
mkdir -p "$O" "$STAGE"
find "$STAGE" -mindepth 1 -delete

if [ ! -d "$K" ]; then
    cd /build
    curl -fsSLO "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-$V.tar.xz"
    echo "$SHA256  linux-$V.tar.xz" | sha256sum -c -
    mkdir -p unpack.tmp
    find unpack.tmp -mindepth 1 -delete
    tar xf "linux-$V.tar.xz" -C unpack.tmp
    mv "unpack.tmp/linux-$V" "$K"
fi

# Initramfs: static busybox for armhf from Debian, plus our init.
R=/build/initramfs
if [ ! -x "$R/bin/busybox" ]; then
    mkdir -p /build/deb "$R"/{bin,sbin,proc,sys,dev,tmp,etc}
    cd /build/deb
    dpkg --add-architecture armhf
    apt-get update -qq
    apt-get download -qq busybox-static:armhf
    dpkg -x busybox-static_*_armhf.deb x
    bb=x/usr/bin/busybox
    [ -f "$bb" ] || bb=x/bin/busybox
    install -m 755 "$bb" "$R/bin/busybox"
fi
install -m 755 "$SRC/initramfs/init" "$R/init"
# Wireless regulatory database. cfg80211 is built in and asks for it about 2 s after power-on,
# before any root filesystem is mounted, so the copy in Debian comes too late: it has to be in
# the initramfs. The "-upstream" files carry the signature the kernel's built-in keys accept.
RD=/build/regdb
if [ ! -s "$RD/regulatory.db" ] || [ ! -s "$RD/regulatory.db.p7s" ]; then
    mkdir -p /build/deb-regdb "$RD"
    cd /build/deb-regdb
    apt-get update -qq
    apt-get download -qq wireless-regdb
    dpkg -x wireless-regdb_*_all.deb x
    db=$(find x -name regulatory.db-upstream | head -1)
    [ -n "$db" ] && [ -s "$db" ] && [ -s "${db%-upstream}.p7s-upstream" ] || die "wireless-regdb has no upstream-signed database"
    install -m 644 "$db" "$RD/regulatory.db"
    install -m 644 "${db%-upstream}.p7s-upstream" "$RD/regulatory.db.p7s"
fi
# The binary is armhf and cannot run here; check the applet names inside it instead.
for a in telnetd cttyhack setsid ifconfig devmem switch_root; do
    grep -a -q "$a" "$R/bin/busybox" || die "busybox has no '$a' applet, which init needs"
done
cat > /build/initramfs.list <<EOF
dir /bin 755 0 0
dir /sbin 755 0 0
dir /proc 755 0 0
dir /sys 755 0 0
dir /dev 755 0 0
dir /tmp 755 0 0
dir /etc 755 0 0
nod /dev/console 600 0 0 c 5 1
nod /dev/tty0 600 0 0 c 4 0
nod /dev/kmsg 600 0 0 c 1 11
file /init $R/init 755 0 0
file /bin/busybox $R/bin/busybox 755 0 0
EOF
# Wi-Fi firmware and board nvram, copied from the device backup into port/firmware (not in git).
FW=$SRC/firmware
[ -s "$FW/fw_bcm43455c0_ag.bin" ] && [ -s "$FW/nvram_ap6255.txt" ] || die "port/firmware lacks the BCM43455 firmware or nvram"
cat >> /build/initramfs.list <<EOF
dir /lib 755 0 0
dir /lib/firmware 755 0 0
dir /lib/firmware/brcm 755 0 0
file /lib/firmware/brcm/brcmfmac43455-sdio.bin $FW/fw_bcm43455c0_ag.bin 644 0 0
file /lib/firmware/brcm/brcmfmac43455-sdio.txt $FW/nvram_ap6255.txt 644 0 0
file /lib/firmware/regulatory.db $RD/regulatory.db 644 0 0
file /lib/firmware/regulatory.db.p7s $RD/regulatory.db.p7s 644 0 0
EOF
for a in sh mount umount mkdir echo cat ls dd sleep dmesg uname cut tr head grep ifconfig telnetd setsid cttyhack sync reboot poweroff ps free df vi hexdump devmem switch_root; do
    echo "slink /bin/$a busybox 777 0 0" >> /build/initramfs.list
done

cd "$K"
cp "$SRC/dts/rk3128-skylight-frame2.dts" arch/arm/boot/dts/rockchip/
grep -q skylight arch/arm/boot/dts/rockchip/Makefile || \
    sed -i 's/rk3128-evb.dtb \\/rk3128-evb.dtb \\\n\trk3128-skylight-frame2.dtb \\/' arch/arm/boot/dts/rockchip/Makefile
grep -q skylight arch/arm/boot/dts/rockchip/Makefile || die "could not add the board to the dts Makefile"

M="make -C $K O=$O ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf- -j$(nproc)"
$M -s multi_v7_defconfig
# Keep only the Rockchip platform: the stock loader leaves about 11 MiB for the zImage.
archs=$(sed -n 's/^CONFIG_\(ARCH_[A-Z0-9_]*\)=y$/\1/p' "$O/.config")
[ -n "$archs" ] || die "no ARCH_ options found in .config"
for a in $archs; do
    case $a in ARCH_ROCKCHIP|ARCH_MULTI*|ARCH_VIRT) ;; *) "$K/scripts/config" --file "$O/.config" -d "$a";; esac
done
"$K/scripts/kconfig/merge_config.sh" -m -O "$O" "$O/.config" "$SRC/config/skylight.config" >/dev/null
"$K/scripts/config" --file "$O/.config" --set-str INITRAMFS_SOURCE /build/initramfs.list
$M -s olddefconfig

echo "== config check"
for c in ARCH_ROCKCHIP CLK_RK312X USB_DWC2 USB_CDC_COMPOSITE U_SERIAL_CONSOLE FB_SIMPLE FRAMEBUFFER_CONSOLE MMC_DW_ROCKCHIP \
         PHY_ROCKCHIP_INNO_USB2 ARM_APPENDED_DTB PINCTRL_ROCKCHIP SERIAL_8250_DW BLK_DEV_INITRD KERNEL_XZ \
         I2C_RK3X MFD_RK8XX_I2C REGULATOR_RK808 RTC_DRV_HYM8563 PWM_ROCKCHIP BACKLIGHT_PWM TOUCHSCREEN_GOODIX \
         INPUT_EVDEV KEYBOARD_GPIO PWRSEQ_SIMPLE BRCMFMAC BRCMFMAC_SDIO DEVMEM \
         CMDLINE_PARTITION EXT4_FS CGROUPS NF_TABLES NF_TABLES_INET NF_TABLES_IPV4 NF_TABLES_IPV6 NFT_CT NF_CONNTRACK \
         PACKET IPV6 AUTOFS_FS FHANDLE TMPFS_POSIX_ACL CFG80211 NAMESPACES; do
    grep -q -E "^CONFIG_$c=y" "$O/.config" || die "required option CONFIG_$c is not =y"
done
# Exactly one legacy gadget may be built in, or another grabs the USB controller before g_cdc.
# The gadget drivers are the tristate options in the legacy Kconfig (bool ones are sub-options).
gadgets=$(awk '/^config /{c=$2} /^[ \t]+tristate/{if(c!=""){print c; c=""}}' drivers/usb/gadget/legacy/Kconfig)
[ -n "$gadgets" ] || die "could not read the legacy gadget list"
builtin=""
for g in $gadgets; do
    if grep -q -E "^CONFIG_$g=y" "$O/.config"; then builtin="$builtin $g"; fi
done
[ "$builtin" = " USB_CDC_COMPOSITE" ] || die "built-in legacy gadgets must be exactly USB_CDC_COMPOSITE, found:$builtin"
echo "config ok"

if ! $M -s zImage rockchip/rk3128-skylight-frame2.dtb > "$O/build.log" 2>&1; then
    tail -40 "$O/build.log"
    die "kernel build failed"
fi
tail -5 "$O/build.log"

DTB=$O/arch/arm/boot/dts/rockchip/rk3128-skylight-frame2.dtb
cat "$O/arch/arm/boot/zImage" "$DTB" > "$STAGE/zImage-dtb"
"$O/usr/gen_init_cpio" /build/initramfs.list > "$STAGE/ramdisk.cpio"
gzip -9 "$STAGE/ramdisk.cpio"
cp "$DTB" "$STAGE/"
cp "$O/.config" "$STAGE/kernel.config"

size=$(stat -c %s "$STAGE/zImage-dtb")
[ "$size" -le "$MAX_KERNEL" ] || die "zImage-dtb is $size bytes, limit is $MAX_KERNEL"
[ -s "$STAGE/ramdisk.cpio.gz" ] || die "ramdisk is empty"

ls -l "$O/arch/arm/boot/Image"
cp "$STAGE"/* "$OUT/"
for f in "$STAGE"/*; do cmp -s "$f" "$OUT/${f##*/}" || die "copy of ${f##*/} to port/out is incomplete"; done
ok=1
echo "OK: zImage-dtb $size bytes of $MAX_KERNEL allowed"
