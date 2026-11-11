#!/bin/bash
# Read-only full backup of the Skylight Frame 2 (RK3128) eMMC over root adb.
# Nothing is written to the device.
set -euo pipefail

OUT="$(cd "$(dirname "$0")" && pwd)/skylight_backup/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT/partitions" "$OUT/apks"
cd "$OUT"

echo "== device info"
adb shell getprop > info_getprop.txt
adb shell cat /proc/cmdline > info_cmdline.txt
adb shell cat /proc/partitions > info_partitions.txt
adb shell cat /proc/mounts > info_mounts.txt
adb shell ls -l /dev/block/platform/1021c000.rksdmmc/by-name/ > info_by-name.txt
adb shell pm list packages -f > info_packages.txt

echo "== apks"
adb pull /system/app/Skylight apks/system_Skylight
adb pull /data/app/com.skylight-1/base.apk apks/com.skylight-6.9.1.apk
adb pull /data/app/skylight.watchdog-1/base.apk apks/skylight.watchdog.apk

# Loader area (idbloader + Rockchip parameter) sits before the first partition at sector 0x2000.
echo "== loader area (first 4 MiB)"
adb exec-out "dd if=/dev/block/mmcblk0 bs=1048576 count=4 2>/dev/null" > partitions/00_loader_area_4MiB.img

for p in uboot misc resource kernel boot recovery backup cache userdata metadata kpanic system radical_update user; do
  echo "== $p"
  adb exec-out "dd if=/dev/block/platform/1021c000.rksdmmc/by-name/$p bs=1048576 2>/dev/null" > "partitions/$p.img"
done

echo "== whole disk mmcblk0"
adb exec-out "dd if=/dev/block/mmcblk0 bs=1048576 2>/dev/null" > mmcblk0_full.img

echo "== checksums"
( cd partitions && md5 -r *.img ) > md5_host_partitions.txt
md5 -r mmcblk0_full.img > md5_host_full.txt
for p in uboot misc resource kernel boot recovery backup kpanic system radical_update; do
  adb shell "busybox md5sum /dev/block/platform/1021c000.rksdmmc/by-name/$p" | tr -d '\r'
done > md5_device_static.txt
ls -l partitions mmcblk0_full.img > sizes_host.txt
echo "DONE $OUT"
