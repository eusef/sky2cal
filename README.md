# sky2cal

A Skylight Frame 2 photo frame turned into a family wall calendar. The stock Android 5.1 (2016 security patches, apps running as root) is replaced by mainline Linux 6.18 and Debian 13, which keeps itself patched and shows a touch-paged week view of one or more iCalendar feeds.

| Document | Content |
|---|---|
| `README.md` | Setup, from a stock frame to a running calendar |
| `RECON.md` | Hardware map, boot chain and every finding, labelled FACT, INFERRED or ASSUMED |
| `docs/HOW_THIS_WAS_BUILT.md` | How the project was made with Claude Code |
| `CLAUDE.md` | Working rules for Claude Code in this repository |

## Read this first

- This overwrites partitions on the frame. Once the kernel is in `boot` (step 10) Android no longer starts. The stock U-Boot and loader are never touched, so the frame can always be put back from your backup through loader mode, but only if the backup exists. Step 1 is not optional.
- Never run `rkdeveloptool ppt` on this device. It wedges the loader until the power is pulled.
- Everything here was done on one frame (build `D104S.V2.20.1`, see `props.txt`) from one Apple Silicon Mac. Other hardware revisions are untested.
- Each step below carries a status. **Verified** means the command was re-run on 2026-11-11 while writing this guide. **Done once** means it worked during the port and is written from the project notes, but was not re-run for the guide, because the reference frame no longer runs Android.

## What is not in the repository

| Missing | Why | Where it comes from |
|---|---|---|
| `skylight_backup/` | Full eMMC dump: holds Wi-Fi credentials and account data | Step 1 |
| `port/firmware/` | Vendor Wi-Fi firmware and board nvram | Step 3, from the backup |
| `recon/out/` | Pieces unpacked from the stock firmware (kernel, ramdisk, device tree, logos) | Optional, see "Unpacking the stock firmware" |
| `tools/rkdeveloptool/` | Third-party tool | Step 2 |
| `port/out/`, `port/images/` | Build outputs | Steps 4, 5 and 7 |
| `calendar_app/config.toml` | Holds the secret calendar address | Copy of `config.example.toml` |

## Prerequisites

| Need | Notes |
|---|---|
| Skylight Frame 2, stock | Root adb over micro-USB works out of the box on the tested build |
| Mac with Docker Desktop | Docker must be able to run `linux/arm/v7` images (emulation is on by default) |
| `adb` | `brew install android-platform-tools` |
| Python 3.11 or newer | The calendar uses `tomllib` |
| `libusb`, `autoconf`, `automake`, `pkg-config` | For building rkdeveloptool; Homebrew or MacPorts |
| About 20 GB free disk | 16 GB of it is the backup |

Run every command from the repository root unless it says otherwise.

## Setup

### 1. Back up the frame (done once, about 1 hour)

```
adb devices
bash backup.sh 2>&1 | tee backup.log
```

This only reads from the device. It writes `skylight_backup/<timestamp>/` with every partition, a full-disk image, the Skylight APKs and device info. Afterwards compare `md5_device_static.txt` with `md5_host_partitions.txt` in that directory by hand; the script records both but does not compare them. Keep a second copy of the backup somewhere else.

The rest of this guide writes `<backup>` for `skylight_backup/<timestamp>`.

### 2. Build rkdeveloptool (verified)

Needed for loader mode, which is the way back in if a flash goes wrong.

```
mkdir -p tools
git clone https://github.com/rockchip-linux/rkdeveloptool tools/rkdeveloptool
cd tools/rkdeveloptool
git checkout 304f073
./autogen.sh && ./configure && make CXXFLAGS="-g -O2 -Wno-error"
cd ../..
tools/rkdeveloptool/rkdeveloptool -v
```

`-Wno-error` is needed because current clang rejects two variable-length arrays under the project's `-Werror`.

Loader mode entry: `adb reboot bootloader` while Android still runs, or hold the **right** button of the ring (speaker icon) while plugging in power with USB attached. The screen stays blank and `tools/rkdeveloptool/rkdeveloptool ld` lists a device with vendor 0x2207. `rkdeveloptool rd` leaves loader mode.

### 3. Extract the Wi-Fi firmware from the backup (verified)

```
docker build -t skylight-build port/docker
mkdir -p port/firmware
docker run --rm -v "$PWD/<backup>/partitions":/bk:ro -v "$PWD/port/firmware":/fw skylight-build bash -c '
  apt-get update -qq >/dev/null && apt-get install -y -qq e2fsprogs >/dev/null
  for f in fw_bcm43455c0_ag.bin nvram_ap6255.txt; do debugfs -R "dump /etc/firmware/$f /fw/$f" /bk/system.img; done'
ls -l port/firmware
```

Both files must be non-empty. The first command also creates the build environment used by the next steps.

### 4. Build the kernel (done once per build; not re-run for this guide)

```
docker run --rm -v skylight-kbuild:/build -v "$PWD":/src skylight-build bash /src/port/build.sh
```

Downloads Linux 6.18.63 (checksum pinned), builds it with the board device tree in `port/dts/` and the bring-up initramfs in `port/initramfs/`, and writes `port/out/zImage-dtb` and `port/out/ramdisk.cpio.gz`. The script ends with `OK:`; on any failure `port/out/` is left empty. The first build takes a while; the `skylight-kbuild` volume keeps the tree for later builds.

### 5. Pack a boot image (verified)

```
mkdir -p port/images
python3 port/rkimg.py selftest <backup>/partitions/recovery.img
python3 port/rkimg.py mkboot --template <backup>/partitions/recovery.img \
    --kernel port/out/zImage-dtb --ramdisk port/out/ramdisk.cpio.gz --out port/images/recovery_linux.img
```

`selftest` must print `True` twice: it proves the packer reproduces your stock image byte for byte. `mkboot` prints the md5 of the new image; note it for the next step. The stock U-Boot checks an id in the image header, which `rkimg.py` recomputes.

### 6. Flash the test slot and boot it (done once)

`recovery` (mmcblk0p6) is the test slot: Android keeps working from `boot`, and the new kernel only runs when you ask for recovery.

```
adb push port/images/recovery_linux.img /data/local/tmp/rl.img
adb shell "dd if=/data/local/tmp/rl.img of=/dev/block/mmcblk0p6 bs=1048576"
adb shell "busybox md5sum /dev/block/mmcblk0p6"
adb reboot recovery
```

The md5 must equal the one `mkboot` printed. After about 70 seconds the frame shows console text and appears on the Mac as a USB network interface named "CDC Composite Gadget". With no Debian installed yet, it stops in a bring-up shell reachable by telnet. Find the interface (`networksetup -listallhardwareports`), then the frame's address:

```
ping6 -c 2 -I en26 ff02::1
export SKYLIGHT_HOST='fe80::xxxx%en26'
python3 port/tn.py 'uname -r'
```

Replace `en26` with your interface and `fe80::xxxx` with the address that answers and is not the Mac's own. `uname -r` must end in `-skylight`.

If the frame drops into loader mode instead (blank screen), the image was refused: `tools/rkdeveloptool/rkdeveloptool rd` returns to Android.

### 7. Build the Debian image (done once; not re-run for this guide)

Use a dedicated SSH key. It becomes the only way to log in.

```
ssh-keygen -t ed25519 -f ~/.ssh/skylight_frame
bash port/rootfs/build-rootfs.sh ~/.ssh/skylight_frame.pub
```

Writes `port/images/rootfs.ext4`: Debian 13 armhf with SSH (keys only, root password locked), an nftables firewall that drops everything inbound except SSH, unattended security upgrades and the calendar's Python dependencies. The time zone is set to `America/Los_Angeles` in `port/rootfs/Dockerfile`; change it there before building if needed.

### 8. Install Debian (done once; erases the `user` partition)

The `user` partition is Android's shared storage (photos on the frame). It is in your backup.

```
export SKYLIGHT_HOST='fe80::xxxx%en26'
python3 port/install_rootfs.py
adb reboot recovery
```

The script refuses to write unless the frame runs the test kernel and the partition is exactly where it should be, then sends the image and verifies it by reading it back. The frame returns to Android after a reset, hence the `adb reboot recovery` once Android is up (if the frame is still in the bring-up shell, restart it first with `python3 port/tn.py 'reboot -f'` or by pulling the power). This time the kernel finds Debian and starts it. About 70 seconds later:

```
ssh -i ~/.ssh/skylight_frame -o IdentitiesOnly=yes 'root@fe80::xxxx%en26'
```

The link-local address may differ from the bring-up one; repeat the `ping6`. On the frame, `systemctl is-system-running` should say `running`.

### 9. Wi-Fi and the calendar (done once)

On the frame:

```
wpa_passphrase "NETWORK NAME" >> /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
nano /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
systemctl restart wpa_supplicant@wlan0
ip addr show wlan0
```

`wpa_passphrase` asks for the passphrase. In the editor, delete the `#psk="..."` comment line that holds it in clear text.

The calendar is part of the Debian image: it is installed in `/opt/skycal` and starts by itself as the `skycal` service, showing the example feed (phases of the moon) once the frame is online. To show your own calendars, edit `/etc/skycal/config.toml` on the frame and put their iCalendar addresses in `feeds`; for a private Google calendar that is "Secret address in iCal format" in the calendar's settings. Treat that address as a password. Then `systemctl restart skycal`.

The three buttons at the top right of the screen go to the previous week, today and the next week. The view returns to the current week after five minutes without a tap.

### 10. Make it permanent (done once; ends Android)

Until now a normal power-on still starts Android. To start the calendar on every power-on, put the same kernel in `boot` (mmcblk0p5). Do this only after steps 6 to 9 work, and from Android (run `reboot` in the Debian SSH session first; a normal restart still starts Android at this point):

```
python3 port/rkimg.py mkboot --template <backup>/partitions/boot.img \
    --kernel port/out/zImage-dtb --ramdisk port/out/ramdisk.cpio.gz --out port/images/boot_linux.img
adb push port/images/boot_linux.img /data/local/tmp/bl.img
adb shell "dd if=/data/local/tmp/bl.img of=/dev/block/mmcblk0p5 bs=1048576"
adb shell "busybox md5sum /dev/block/mmcblk0p5"
adb reboot
```

After this there is no adb. Access is SSH, over Wi-Fi or the USB cable.

## Trying things without a frame

| Check | Command | Expected |
|---|---|---|
| Render a week on the Mac | `cd calendar_app && python3 -m venv .venv && .venv/bin/pip install pillow icalendar recurring-ical-events && cp config.example.toml config.toml && .venv/bin/python -m skycal --config config.toml --png week.png` | `week.png: week of ..., N events, fresh` and a 1280x800 picture |
| Packer against a stock image | `python3 port/rkimg.py selftest <backup>/partitions/recovery.img` | `True` twice |
| Kernel build | Step 4 | `OK: zImage-dtb ...` |
| Root filesystem build | Step 7 | `OK: port/images/rootfs.ext4` |

## Day to day

| Task | How |
|---|---|
| Update the calendar app | On the Mac: `bash calendar_app/deploy.sh '<frame address>'` (never touches the config on the frame) |
| Update the kernel | See "Updating the kernel" |
| Change feeds or time zone | Edit `/etc/skycal/config.toml` on the frame, `systemctl restart skycal` |
| Logs | `journalctl -u skycal` on the frame |
| OS updates | Automatic (unattended-upgrades) |
| Change the bring-up init or kernel config | Rebuild the kernel: the kernel uses its built-in initramfs and ignores the one in the boot image |

## Updating the kernel

Debian updates itself; the kernel does not. Once `boot` holds the mainline kernel there is no adb, so a new kernel is written from the Mac over SSH and tried out in the `recovery` slot before it goes into `boot`. This whole sequence was run on the reference frame on 2026-11-11 (kernel build 4 to build 5).

`port/update_kernel.py` checks the image (size, id, length padding) and the frame (kernel, eMMC size, a boot image already at the slot's offset). Without `--write` it changes nothing on the flash and must end with `CHECKS PASSED`; with `--write` it writes the slot and reads it back.

1. Build, and pack the same kernel three ways:

```
docker run --rm -v skylight-kbuild:/build -v "$PWD":/src skylight-build bash /src/port/build.sh
bash port/mkrescue.sh
python3 port/rkimg.py mkboot --template <backup>/partitions/recovery.img \
    --kernel port/out/zImage-dtb --ramdisk port/out/ramdisk.cpio.gz --out port/images/recovery_new.img
python3 port/rkimg.py mkboot --template <backup>/partitions/boot.img \
    --kernel port/out/zImage-dtb --ramdisk port/out/ramdisk.cpio.gz --out port/images/boot_new.img
python3 port/rkimg.py mkboot --template <backup>/partitions/recovery.img \
    --kernel port/out/zImage-dtb-rescue --ramdisk port/out/ramdisk.cpio.gz --out port/images/recovery_rescue.img
```

2. Try it from `recovery`. `boot` is untouched, so if the new kernel does not start, pulling the power brings the old one back:

```
python3 port/update_kernel.py <frame address> recovery port/images/recovery_new.img --write
ssh -i ~/.ssh/skylight_frame root@<frame address> python3 /usr/local/sbin/skylight-reboot-recovery
```

The helper is part of the Debian image; on a frame installed from an older image, copy `port/rootfs/files/usr/local/sbin/skylight-reboot-recovery` to the same path first. The frame restarts into the new kernel and Debian. Check it over SSH: `uname -v` shows the new build time, `systemctl is-system-running` says `running`.

3. Put it in `boot`, then leave a rescue kernel in `recovery`:

```
python3 port/update_kernel.py <frame address> boot port/images/boot_new.img --write
python3 port/update_kernel.py <frame address> recovery port/images/recovery_rescue.img --write
ssh -i ~/.ssh/skylight_frame root@<frame address> reboot
```

`skylight-reboot-recovery` sets a flag in a chip register that the stock U-Boot reads once (details in `RECON.md`). It works for one restart only; the restart after that, or a power cycle, starts `boot` again. No ring button starts `recovery`: all five were tried.

## Recovery

| Situation | Way out |
|---|---|
| New image in `recovery` does not boot | Frame falls into loader mode; `rkdeveloptool rd` returns to the normal boot |
| Debian is damaged but still starts far enough for SSH | `python3 /usr/local/sbin/skylight-reboot-recovery` on the frame starts the rescue kernel kept in `recovery`: the frame stays in the telnet bring-up shell on the USB cable, with Debian's partition as mmcblk0p2 |
| Debian or the kernel in `boot` does not start at all | Loader mode (below). The rescue kernel cannot be reached, because starting `recovery` needs a running system to set the flag |
| Back to stock | Loader mode by the ring's right button, then write the stock images from the backup with `rkdeveloptool wl`. **Untested.** rockusb block addresses are 0x2000 sectors below raw eMMC sectors; see `RECON.md` before trying |

## Open items

- Writing through loader mode (`rkdeveloptool wl`) is untested, and it is the only way back from a `boot` kernel that does not start.
- A `reboot-mode` node in the device tree, so that `systemctl reboot recovery` replaces the helper script.
- brcmfmac reports no clm_blob. The vendor's 2017 Wi-Fi firmware has no matching one, and Wi-Fi works without it.
- Audio: the rk312x codec has no mainline driver. Not needed for a calendar.

## Unpacking the stock firmware (optional)

Not needed for setup. It is how the stock device tree that `RECON.md` refers to (`recon/out/boot__rk-kernel.dtb.dts`) is produced, from your own backup:

```
python3 -m venv recon/.venv && recon/.venv/bin/pip install fdt
recon/.venv/bin/python -I recon/extract.py <backup> recon/out
```

## License and warranty

MIT license, see `LICENSE`. The board device tree (`port/dts/`) is additionally offered under GPL-2.0-or-later, as its header says, to match the Linux kernel.

There is no warranty of any kind. This project replaces the operating system of a consumer device by writing to its flash storage. Following this guide can leave a frame unable to start, erase what is stored on it, and will void the manufacturer's warranty and end the vendor's service on that frame. Several procedures here are marked untested. Nobody involved in this project is liable for any damage or loss. If you follow it, you do so entirely at your own risk; make and keep the backup in step 1 first.

## Legal

This repository contains no vendor firmware. Everything unpacked from the stock images, the device backup and the Wi-Fi firmware stays on your own machine and is git-ignored; do not commit it. The project is not affiliated with, endorsed by or supported by Skylight or Rockchip; product names are used only to identify the hardware.
