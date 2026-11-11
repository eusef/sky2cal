# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A hardware port plus a small display app. A Skylight Frame 2 digital photo frame (Rockchip RK3128, 512 MB RAM, 800x1280 MIPI DSI panel) has had its stock Android 5.1 replaced by mainline Linux 6.18 and Debian 13 armhf, running a week-view wall calendar (`skycal`) on the framebuffer.

- `README.md` is the setup guide, from a stock frame to a running calendar.
- `RECON.md` is the source of truth for hardware, boot chain and every finding made along the way. Read it before touching the device, and do not re-derive its findings. It labels claims FACT, INFERRED or ASSUMED; keep that convention when adding to it.
- `docs/HOW_THIS_WAS_BUILT.md` describes the process.

## State of the reference device (2026-11-11)

- `boot` (mmcblk0p5) and `recovery` (mmcblk0p6) both hold the mainline kernel. Android no longer boots and adb is gone.
- Debian lives on the former `user` partition (the only partition the mainline kernel exposes, as mmcblk0p1).
- Access is SSH only (Wi-Fi, or IPv6 link-local over the USB cable), key-only, root password locked.
- Stock U-Boot, `system` and the other Android partitions are untouched.

## Safety rules

- Every write to a device partition needs the owner's explicit go-ahead, and the owner runs the write command. Do not route around a hook that blocks it.
- `recovery` is the test slot. Try a new kernel there before putting it in `boot`.
- The backup under `skylight_backup/` is the only restore path. Never modify, move or delete it or `skylight_backup.zip`. Neither is in git.
- Never run `rkdeveloptool ppt` on this device: it wedges the loader until the power is pulled.
- Tools such as `rkdeveloptool` get built inside the project directory (`tools/`), not installed globally.
- Secrets stay out of git: the calendar feed address (`calendar_app/config.toml`, `/etc/skycal/config.toml` on the frame), Wi-Fi credentials, SSH keys, anything from the backup.

## Commands

All run from the repository root on the Mac. `README.md` has the full sequence and prerequisites.

| Task | Command |
|---|---|
| Unpack stock boot images, decompile DTBs | `python3 -m venv recon/.venv && recon/.venv/bin/pip install fdt`, then `recon/.venv/bin/python -I recon/extract.py skylight_backup/<timestamp> recon/out` |
| Build environment image | `docker build -t skylight-build port/docker` |
| Build kernel + initramfs to `port/out/` | `docker run --rm -v skylight-kbuild:/build -v "$PWD":/src skylight-build bash /src/port/build.sh` |
| Check the packer against a stock image | `python3 port/rkimg.py selftest skylight_backup/<timestamp>/partitions/recovery.img` |
| Pack a flashable image | `python3 port/rkimg.py mkboot --template <stock recovery.img or boot.img> --kernel port/out/zImage-dtb --ramdisk port/out/ramdisk.cpio.gz --out port/images/<name>.img` |
| Rescue kernel (stays in the bring-up shell) | `bash port/mkrescue.sh` |
| Debian root filesystem image | `bash port/rootfs/build-rootfs.sh ~/.ssh/skylight_frame.pub` |
| Write the root filesystem (erases `user`) | `SKYLIGHT_HOST='fe80::xxxx%enN' python3 port/install_rootfs.py` |
| Deploy or update the calendar | `bash calendar_app/deploy.sh '<frame address>'` |
| Render a week to PNG on the Mac | `cd calendar_app && .venv/bin/python -m skycal --config config.toml --png week.png` |

There are no tests, linter config or CI. The build scripts check their own results and fail closed: on any error the output directory is left empty rather than stale.

## Layout

| Path | Content |
|---|---|
| `backup.sh` | Read-only full eMMC backup over root adb (stock Android only), into `skylight_backup/<timestamp>/` |
| `recon/extract.py`, `recon/out/` | Boot image and RSCE unpacker and its output (output is vendor material: git-ignored, regenerate from the backup). `recon/out/boot__rk-kernel.dtb.dts` is the stock device tree; the panel init sequence is the `rockchip,cmd` entries under `screen-on-cmds` |
| `cpuinfo.txt`, `meminfo.txt`, `mounts.txt`, `partitions.txt`, `props.txt` | Captures from the stock device (identifiers redacted) |
| `port/docker/`, `port/build.sh`, `port/config/`, `port/dts/`, `port/initramfs/` | Kernel build: environment, script, config fragment, board device tree, bring-up init |
| `port/rkimg.py` | Packs boot images the stock U-Boot accepts (Rockchip SHA-1 id, 4-byte length padding) |
| `port/rootfs/` | Debian 13 armhf image: Dockerfile, overlay files, build script |
| `port/install_rootfs.py`, `port/tn.py` | Write the image over the bring-up telnet shell; address from `SKYLIGHT_HOST` |
| `calendar_app/skycal/` | Calendar: `feed.py` (iCalendar fetch and cache), `render.py` (week view), `frame.py` (framebuffer and touch loop), `__main__.py` (PNG output for development) |

Not in git, and needed to build: `skylight_backup/`, `port/firmware/` (Wi-Fi firmware and nvram from the backup), `tools/rkdeveloptool/`. Also not in git: `recon/out/`. Never commit anything unpacked from the stock firmware.

## Things that are easy to get wrong

- The kernel uses its built-in initramfs and ignores the ramdisk in the boot image, so any change to `port/initramfs/init` needs a kernel rebuild.
- The loader refuses boot images whose kernel or ramdisk length is not a multiple of 4, and images whose id (an unkeyed SHA-1 that also covers the cmdline) is stale. `rkimg.py` handles both.
- rockusb LBAs are 0x2000 sectors below raw eMMC sectors (see `RECON.md`).
- The stock DTB contains template leftovers that do not match the fitted hardware (ACT8931/RK818/RK816 PMIC nodes, `wifi_chip_type = "ap6354"`, a gc2145 camera). Trust the hardware map in `RECON.md`.
- `partitions/00_loader_area_4MiB.img` in a backup is incomplete (the loader area is 8 MiB); use `mmcblk0_full.img` for anything before the first partition.
- The framebuffer is 800x1280 RGB565 and a landscape image needs `ROTATE_90`. The touch panel is turned half a turn against it (both flips default to true).
- On the frame, background processes started over SSH die with the session; use `systemd-run --unit`.

## Open items

1. A kernel update path now that adb is gone (loader mode with `rkdeveloptool wl` is untested).
2. `regulatory.db` and the brcmfmac clm_blob.
3. Bake the calendar app into the `port/rootfs` image instead of deploying it afterwards.
