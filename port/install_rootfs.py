"""Write port/images/rootfs.ext4 to the frame's "user" partition. ERASES that partition.

The frame must be running the bring-up shell of the build 3 test kernel (booted from
`recovery`, Debian not yet installed), reachable by telnet over the USB network; the
address comes from the SKYLIGHT_HOST environment variable (see port/tn.py). Android's boot, system and data partitions are not touched:
the kernel only exposes the user partition, as mmcblk0p1.

Usage:  SKYLIGHT_HOST='fe80::xxxx%en26' python3 port/install_rootfs.py
"""
import hashlib
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGE = HERE / "images" / "rootfs.ext4"
PART = "mmcblk0p1"
START, SECTORS = 4866048, 10076160   # user partition, verified against the full-disk backup
PORT = 9001
MIB = 1048576


def tn(cmd: str, secs: float = 8) -> str:
    """Run a command in the frame's shell and return what it printed after the echo."""
    out = subprocess.run([sys.executable, str(HERE / "tn.py"), cmd, str(secs)],
                         capture_output=True, text=True, timeout=secs + 30).stdout
    return out.split("echo __DONE__", 1)[-1]


def die(msg: str) -> None:
    sys.exit(f"FAIL: {msg}")


def main() -> None:
    host = os.environ.get("SKYLIGHT_HOST") or die("set SKYLIGHT_HOST to the frame's address, e.g. fe80::xxxx%en26")
    size = IMAGE.stat().st_size
    if size == 0 or size % MIB or size > SECTORS * 512:
        die(f"{IMAGE} is {size} bytes: empty, not a whole number of MiB, or larger than the partition")
    print("hashing the image ...")
    want = hashlib.md5(IMAGE.read_bytes()).hexdigest()

    geo = tn(f"echo GEO $(cat /sys/class/block/{PART}/start) $(cat /sys/class/block/{PART}/size)")
    m = re.search(r"GEO (\d+) (\d+)", geo)
    if not m or (int(m.group(1)), int(m.group(2))) != (START, SECTORS):
        die(f"{PART} is not the user partition (expected start {START}, {SECTORS} sectors): {geo.strip()!r}")
    if not re.search(r"^KREL \d+\.\d+\.\d+-skylight$", tn("echo KREL $(uname -r)"), re.M):
        die("the frame is not running the Skylight test kernel")

    # No earlier receiver may be alive, or the data could go to it and the marker would lie.
    if not re.search(r"^BUSY 0$", tn("echo BUSY $(ps | grep -c '[n]c -l -p')"), re.M):
        die("a receiver from an earlier run is still running on the frame; reboot the frame first")
    # The marker says "ok" only if nc and dd both succeeded and the data was synced.
    tn(f"rm -f /tmp/wr.done /tmp/wr.log; "
       f"(busybox nc -l -p {PORT} | busybox dd of=/dev/{PART} bs={MIB} 2>/tmp/wr.log "
       f"&& sync && echo ok > /tmp/wr.done || echo fail > /tmp/wr.done) >/dev/null 2>&1 & true", 3)
    time.sleep(2)
    state = tn("echo LISTEN $(ps | grep -c '[n]c -l -p') MARK $(cat /tmp/wr.done 2>/dev/null)")
    if not re.search(r"^LISTEN 1 MARK$", state, re.M):
        die(f"the receiver did not start on the frame: {state.strip()!r}")

    ai = socket.getaddrinfo(host, PORT, socket.AF_INET6, socket.SOCK_STREAM)[0]
    s = socket.socket(*ai[:3])
    s.settimeout(60)
    s.connect(ai[4])
    sent, t0 = 0, time.time()
    with IMAGE.open("rb") as f:
        while chunk := f.read(MIB):
            s.sendall(chunk)
            sent += len(chunk)
            if sent % (64 * MIB) == 0:
                print(f"  sent {sent // MIB} of {size // MIB} MiB ({sent / MIB / (time.time() - t0):.1f} MiB/s)")
    s.shutdown(socket.SHUT_WR)
    s.close()

    for _ in range(120):
        mark = re.search(r"^MARK (\w*)$", tn("echo MARK $(cat /tmp/wr.done 2>/dev/null)", 3), re.M)
        if mark and mark.group(1) == "ok":
            break
        if mark and mark.group(1) == "fail":
            die("the write failed on the frame: " + tn("cat /tmp/wr.log"))
        time.sleep(2)
    else:
        die("the frame did not finish writing: " + tn("cat /tmp/wr.log"))
    print("written; reading back to verify ...")
    got = tn(f"echo 3 > /proc/sys/vm/drop_caches; busybox dd if=/dev/{PART} bs={MIB} count={size // MIB} 2>/dev/null "
             f"| busybox md5sum", 600)
    if want not in got:
        die(f"read-back md5 does not match {want}: {got.strip()!r}")
    print(f"OK: {PART} holds the image (md5 {want}). Reboot the frame into recovery to start Debian.")


if __name__ == "__main__":
    main()
