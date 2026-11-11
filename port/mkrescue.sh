#!/bin/bash
# Make a rescue kernel from the last build (run on the Mac):  bash port/mkrescue.sh
# Output: port/out/zImage-dtb-rescue. Pack it with rkimg.py mkboot like the normal kernel.
#
# The kernel uses its built-in ramdisk and ignores the one in the boot image, so the only
# thing a rescue image can change without a rebuild is the device tree's boot arguments.
# The rescue arguments put a 1 MiB dummy partition first: init then finds mmcblk0p1 in the
# wrong place and stays in the bring-up shell, and the Debian partition is mmcblk0p2.
set -euo pipefail
cd "$(dirname "$0")/.."
die() { echo "FAIL: $*" >&2; exit 1; }
mkdir -p port/out
: > port/out/zImage-dtb-rescue    # a failed run must not leave an older rescue kernel looking current
[ -s port/out/zImage-dtb ] && [ -s port/out/rk3128-skylight-frame2.dtb ] || die "run port/build.sh first"

docker run --rm -v "$PWD/port/out":/out skylight-build bash -euo pipefail -c '
    die() { echo "FAIL: $*" >&2; exit 1; }
    cd /out
    NORMAL="blkdevparts=mmcblk0:5158993920@2491416576(user)"
    RESCUE="blkdevparts=mmcblk0:1M@1M(dummy),5158993920@2491416576(user)"
    total=$(stat -c %s zImage-dtb)
    dsize=$(stat -c %s rk3128-skylight-frame2.dtb)
    ksize=$((total - dsize))
    [ "$ksize" -gt 0 ] || die "zImage-dtb is smaller than the device tree"
    tail -c "$dsize" zImage-dtb | cmp -s - rk3128-skylight-frame2.dtb || die "zImage-dtb does not end with the device tree"
    args=$(fdtget -t s rk3128-skylight-frame2.dtb /chosen bootargs)
    case "$args" in *"$NORMAL"*) ;; *) die "bootargs lack the expected partition layout: $args";; esac
    cp rk3128-skylight-frame2.dtb /tmp/rescue.dtb
    fdtput -t s /tmp/rescue.dtb /chosen bootargs "${args/"$NORMAL"/"$RESCUE"}"
    got=$(fdtget -t s /tmp/rescue.dtb /chosen bootargs)
    case "$got" in *"$RESCUE"*) ;; *) die "rescue bootargs were not written";; esac
    # Nothing but the boot arguments may differ between the two device trees.
    dtc -q -I dtb -O dts -o /tmp/a.dts rk3128-skylight-frame2.dtb && [ -s /tmp/a.dts ] || die "dtc failed"
    dtc -q -I dtb -O dts -o /tmp/b.dts /tmp/rescue.dtb && [ -s /tmp/b.dts ] || die "dtc failed"
    diff <(grep -v bootargs /tmp/a.dts) <(grep -v bootargs /tmp/b.dts) >/dev/null || die "device trees differ beyond bootargs"
    head -c "$ksize" zImage-dtb > zImage-dtb-rescue.tmp
    cat /tmp/rescue.dtb >> zImage-dtb-rescue.tmp
    mv zImage-dtb-rescue.tmp zImage-dtb-rescue
    echo "rescue bootargs: $got"
'
ls -l port/out/zImage-dtb-rescue
echo "OK: port/out/zImage-dtb-rescue"
