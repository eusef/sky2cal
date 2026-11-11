"""Write a packed boot image to the frame's `recovery` or `boot` slot from the Mac, over SSH.

For a frame that already runs Debian (there is no adb any more). The image must come from
`rkimg.py mkboot` with the stock image of the SAME slot as template, so it has the slot's size.

Usage:
  python3 port/update_kernel.py <frame address> <recovery|boot> <image>            # checks only
  python3 port/update_kernel.py <frame address> <recovery|boot> <image> --write    # checks, then writes

Without --write nothing on the frame's flash changes (the image is copied to /var/tmp and
removed again). With --write the slot is overwritten and read back; the frame is not restarted.
A bad image in `boot` leaves the frame unable to start until it is rewritten through loader
mode. Read README.md, "Updating the kernel", first: it says what is tested and what is not.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rkimg  # noqa: E402

# Raw eMMC sectors (512 bytes). Read back from the frame on 2026-11-11: both regions matched
# the md5 of the images that had been flashed to the slots (RECON.md, "Slot offsets").
SLOTS = {"boot": (90112, 24576), "recovery": (114688, 65536)}
EMMC_SECTORS = 14942208
USER_START = 4866048
TMP = "/var/tmp/skylight-kernel.img"

REMOTE = r'''
import hashlib, os, sys
start, sectors, want, write = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], sys.argv[4] == "write"
tmp, dev = sys.argv[5], "/dev/mmcblk0"
def die(msg):
    print("FAIL: " + msg); sys.exit(1)
def read(path):
    with open(path) as f: return f.read().strip()
if not os.uname().release.endswith("-skylight"): die("the frame is not running the Skylight kernel")
if read("/sys/class/block/mmcblk0/size") != sys.argv[6]: die("unexpected eMMC size")
if read("/sys/class/block/mmcblk0p1/start") != sys.argv[7]: die("the user partition is not where it should be")
size = sectors * 512
with open(tmp, "rb") as f: data = f.read()
if len(data) != size or hashlib.md5(data).hexdigest() != want: die("the copy on the frame is damaged")
with open(dev, "rb") as f:
    f.seek(start * 512); old = f.read(size)
if old[:8] != b"ANDROID!": die("no boot image at the slot's offset; refusing to write there")
print("slot now holds md5 " + hashlib.md5(old).hexdigest())
if not write:
    print("CHECKS PASSED (nothing written)"); sys.exit(0)
fd = os.open(dev, os.O_RDWR)
try:
    done = 0
    while done < size:
        done += os.pwrite(fd, data[done:done + 1048576], start * 512 + done)
    os.fsync(fd)
except OSError as err:
    die("the write stopped part-way (" + str(err) + "); the slot is NOT good, write it again before restarting")
finally:
    os.close(fd)
with open("/proc/sys/vm/drop_caches", "w") as f: f.write("3")
with open(dev, "rb") as f:
    f.seek(start * 512); got = hashlib.md5(f.read(size)).hexdigest()
if got != want: die("read-back md5 " + got + " does not match " + want + "; the slot is NOT good, write it again before restarting")
print("WRITTEN and verified, md5 " + got)
'''


def die(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("host", help="frame address, its Wi-Fi address, or 'fe80::xxxx%%en26' over the USB cable")
    ap.add_argument("slot", choices=sorted(SLOTS))
    ap.add_argument("image", type=Path)
    ap.add_argument("--write", action="store_true", help="overwrite the slot (default: checks only)")
    ap.add_argument("--key", type=Path, default=Path.home() / ".ssh" / "skylight_frame")
    a = ap.parse_args()

    start, sectors = SLOTS[a.slot]
    data = a.image.read_bytes()
    if len(data) != sectors * 512:
        die(f"{a.image} is {len(data)} bytes; the {a.slot} slot is {sectors * 512}. "
            f"Pack it with the stock {a.slot}.img as --template")
    try:
        p = rkimg.parse_boot(data)
    except AssertionError as e:
        die(f"{a.image}: {e}")
    if rkimg.rk_id(p["header"], p["kernel"], p["ramdisk"], p["second"]) != data[rkimg.ID_OFF:rkimg.ID_OFF + rkimg.ID_LEN]:
        die("the image id does not match its contents; the loader would refuse it")
    if len(p["kernel"]) % 4 or len(p["ramdisk"]) % 4 or not p["kernel"]:
        die("kernel and ramdisk lengths must be non-zero multiples of 4; the loader refuses others")
    want = hashlib.md5(data).hexdigest()
    print(f"{a.image}: {a.slot} image, md5 {want}")

    ssh = ["ssh", "-i", str(a.key), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", f"root@{a.host}"]
    try:
        subprocess.run(ssh + [f"umask 077 && cat > {TMP}"], input=data, check=True)
        r = subprocess.run(ssh + ["python3", "-", str(start), str(sectors), want, "write" if a.write else "check",
                                  TMP, str(EMMC_SECTORS), str(USER_START)], input=REMOTE.encode())
    finally:
        subprocess.run(ssh + [f"rm -f {TMP}"])
    if r.returncode:
        die("the frame reported a problem (see above)" + (
            ". If nothing above says CHECKS or WRITTEN, the connection may have dropped during the write: "
            "treat the slot as NOT good and write it again before restarting the frame" if a.write else ""))


if __name__ == "__main__":
    main()
