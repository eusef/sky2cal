# Skylight Frame 2: recon for a mainline Linux port

Date: 2026-11-03. Sources: live device over root adb (read-only), backup images in `skylight_backup/` (named `backup/` at the time), decompiled stock DTB at `recon/out/boot__rk-kernel.dtb.dts`, mainline kernel tree (tag v7.4-rc1).
Labels: FACT = read directly; INFERRED = interpretation; ASSUMED = judgment.

## Verdict: GO

Every chip needed for a display-only build has a mainline Linux driver (FACT, checked in kernel source). The exception is the rk312x internal audio codec: no mainline driver (FACT; `inno_rk3036.c` matches only rk3036), so no audio unless one is written. The work is a board device tree, one new panel entry in an existing driver, and packaging the kernel for the stock bootloader.

## Hardware map

| Block | What is fitted (FACT unless marked) | Mainline driver |
|---|---|---|
| SoC | RK3128, 4x Cortex-A7, 512 MB RAM | `rk3128.dtsi` |
| PMIC | **RK805** at i2c0 0x18 (dmesg: "pmic is rk805, chip version is 8051"). ACT8931, RK818, RK816 nodes are template leftovers (dmesg: "The device is not act8931", "not rk818") | `rk8xx` (RK805) |
| Panel | 800x1280 MIPI DSI, 4 lanes, RGB888, 76 MHz in DT (stock kernel actually runs 74.25 MHz, 58 fps per dmesg), 510 MHz HS clock. Timings: h 70/20/70, v 16/8/22 | VOP + `dw-mipi-dsi` in `rk3128.dtsi` |
| Panel controller | INFERRED: Ilitek ILI9881C family (init sequence uses `FF 98 81 0N` page select). Full 194-command init sequence extracted | `panel-ilitek-ili9881c.c` (needs a new panel entry) |
| Panel GPIOs | power enable GPIO1_B4 (active low, 300 ms), reset GPIO0_D0 (high 30 / low 30 / high 50 ms) | gpio |
| Backlight | PWM1 (pin GPIO0_D3), 25 us period, 0-255, default 128 | `pwm-backlight` |
| Touch | Goodix gt9xx, i2c2 0x14, IRQ GPIO1_B0, reset GPIO0_D1, reports 1280x800 (landscape vs portrait panel: needs axis swap) | `goodix` |
| RTC | HYM8563, i2c0 0x51, IRQ GPIO2_B4 | `rtc-hym8563` |
| Wi-Fi | BCM43455 (SDIO 02d0:a9bf), AP6255-class module. Firmware `fw_bcm43455c0_ag.bin`, nvram `nvram_ap6255.txt` (both on device). WL_REG_ON GPIO3_D3, host wake GPIO3_C7, reset GPIO0_B6 (active low). DT `wifi_chip_type = "ap6354"` is stale | `brcmfmac` |
| Power-off | `gpio_poweroff` GPIO3_C1, active high | `gpio-poweroff` |
| Bluetooth | On UART0 (RTS GPIO0_C1), BT_REG_ON GPIO3_C5, wake GPIO3_D2, host wake GPIO3_C6 | `hci_uart` (bcm) |
| eMMC | 7.1 GB, 8-bit | `dw_mmc` |
| USB | OTG + EHCI/OHCI, host VBUS GPIO0_A2 | `dwc2`, `ehci`, `ohci` |
| Audio | rk312x internal codec, speaker amp GPIO0_D6 | INFERRED: not a priority for a display |
| HDMI / LVDS / camera / SD slot | HDMI disabled, LVDS inert (DSI shares the PHY), gc2145 camera absent (dmesg: "read chip id failed"), SD controller disabled | not needed |

## Boot chain

| Item | Finding |
|---|---|
| Loader | Rockchip legacy loader (DDR v2.11 2019-08-15, loader timestamp 2020-07-28) + legacy U-Boot, `bootcmd=bootrk` (FACT) |
| Kernel location | `boot` and `recovery` are standard `ANDROID!` boot images: kernel + ramdisk + "second" (FACT). `kernel` partition is empty (FACT) |
| Device tree | Carried in the boot image "second" as a Rockchip `RSCE` resource file (`rk-kernel.dtb` + logos); U-Boot strings: "failed to load fdt from second address" (FACT). Identical copy in `resource` partition (FACT, same md5) |
| Hash in header | U-Boot has a "boot or recovery image sha mismatch!" check and enforces it (FACT, tested 2026-11-10). The id is a Rockchip-layout SHA-1 that we can recompute; see "Boot image hash" below |
| Safe test slot | `recovery` is a separate boot image; Android stays on `boot` (INFERRED: test there first) |
| Debug console | FIQ debugger on **UART2, pins GPIO1_C2/C3 = the SD card D0/D1 pins**, 115200 (FACT pins; INFERRED baud). `rockchip_jtag` on the cmdline suggests SD-pin UART/JTAG switching |
| Loader (rockusb) mode | Entered with `adb reboot bootloader` (FACT, tested 2026-11-10); see "Loader (rockusb) mode findings" below. U-Boot also has `do_rockusb`, `checkKey`, "fastboot key pressed"; vol-down is SARADC ch1 pulled to ground (FACT, template; key entry untested) |

## Physical (FACT, from the owner's photos 2026-11-03)

| Item | Detail |
|---|---|
| Ports | 3.5 mm headphone, USB-A (host, VBUS GPIO0_A2), micro-USB (OTG, used for adb), DC barrel jack (power) |
| Buttons | Menu, ring (vol-up top, vol-down bottom, next/prev sides), center play/pause, Power. Matches the DT `adc/key` list, so the keys are real, not template |
| microSD | None. UART2 debug console is only reachable on internal pads |

## Debug strategy without serial (INFERRED)

1. **Screen console via the U-Boot framebuffer:** stock U-Boot initializes the panel for the logo (`uboot-logo-on = 1`, cmdline `uboot_logo=0x02000000@0x7dc00000`). A mainline kernel with a `simple-framebuffer` node pointing at that region can print its console on the panel before any DSI/panel driver exists. Pixel format to be determined.
2. **SSH over the micro-USB cable:** dwc2 in USB gadget mode (CDC Ethernet).
3. **Loader mode:** `adb reboot bootloader` (FACT). U-Boot also drops into it by itself when the `recovery` image fails the hash check (FACT). Shows on the Mac as USB 2207:310C.
4. **Fallback:** open the case and attach a 3.3 V USB-UART to the UART2 pads (GPIO1_C2/C3), 115200.

## Security finding (FACT, 2026-11-03)

- A preinstalled SuperSU (`/system/xbin/su`, `daemonsu`) grants live root sessions to `com.skylight` (uid 10049) and `skylight.watchdog` (uid 10054).
- `/system` (mounted ro) had its ext4 mount count go from 9412 to 10071 in about 55 min, with the superblock and journal changing. So something with root remounts it read-write repeatedly (INFERRED: the Skylight apps).
- Consequence: any compromise of the Skylight app is a full device compromise.

## Backup notes

- Real partition starts are 4 MiB later than the cmdline `mtdparts` offsets (p1 at sector 16384). The loader area is 8 MiB, not the 4 MiB in `00_loader_area_4MiB.img`; `mmcblk0_full.img` contains all of it.

## Loader (rockusb) mode findings (FACT, tested 2026-11-10)

- Entry: `adb reboot bootloader`. The frame enumerates as USB 2207:310C with a blank screen. Answers open question 3 (vol-down not tested, not needed).
- Tool: `tools/rkdeveloptool/rkdeveloptool` (built in the project, upstream commit 304f073). `ld`, `rfi`, `rl` and `rd` work. `rd` resets back to Android.
- **Never run `ppt`:** it hangs on this legacy loader and wedges the USB session; only pulling the power recovers it. `rcb` fails harmlessly.
- **Addressing: rockusb LBAs are the cmdline `mtdparts` offsets, which are 0x2000 sectors (4 MiB) below raw eMMC sectors.** `recovery` is rockusb LBA 0x1A000, length 0x10000 (raw eMMC sector 0x1C000). Reading it with `rl 0x1A000 0x10000` returned md5 1e3cd4262065c3de9869636b5d347fd3, identical to the backup. Reading at 0x1C000 returns data 4 MiB into the partition.
- Rockusb writes (`wl`) are untested.

## Boot image hash (tested 2026-11-10)

- FACT: the 20-byte header id is an unkeyed SHA-1 in Rockchip's mkbootimg layout: kernel, kernel size, ramdisk, ramdisk size, second, second size, then header bytes 32..0x240 (tags_addr, page_size, unused, name, cmdline). It reproduces the stock `boot` and `recovery` ids exactly. So the cmdline **is** hashed, contrary to the note in open question 1.
- FACT: with one ramdisk byte changed and the stale id left in place (`port/recovery_hashtest.img`), `adb reboot recovery` lands in loader mode (2207:310C) instead of recovery. `rkdeveloptool rd` then returns to Android; `misc` is unchanged, so there is no sticky boot-recovery flag.
- FACT: the same one-byte change with the id recomputed (`port/recovery_hashtest2.img`, md5 1b645df03583beb2cf3603bb04fbf841) boots: `adb devices` shows the frame in `recovery` state about 20 s after `adb reboot recovery`, and `adb reboot` returns to Android. So U-Boot enforces the hash, it is a checksum and not a signature, and custom images with a recomputed id are accepted. Open question 1 is answered.
- State of the device: `recovery` currently holds `recovery_hashtest2.img` (functionally stock). Everything else is unchanged from the backup.
- Writes so far were done from root adb in Android with `dd` to `/dev/block/mmcblk0p6` (the `recovery` partition). The owner runs these by hand: a global hook blocks Claude from running `dd` to a device node.

### Length alignment (2026-11-10)

- FACT: `recovery_linux2.img` as first packed (kernel 6441691 bytes, ramdisk 1222554 bytes, correct id) was refused: the frame dropped to loader mode 4 s after `adb reboot recovery`, too soon for a kernel to have run, and no boot log was saved. Every accepted image (stock boot, stock recovery, hash test 2, `recovery_linux1.img`) has kernel and ramdisk lengths divisible by 4.
- Confirmed by test: the same build repacked with padded lengths (`recovery_linux2b.img`) booted. The loader's SHA-1 evidently only handles 4-byte-aligned lengths. `rkimg.py mkboot` now zero-pads kernel and ramdisk to a multiple of 4.

## First mainline boot (FACT, 2026-11-10)

- Linux 6.18.63 (long-term) boots from the `recovery` partition under the stock U-Boot. Image: `port/out/recovery_linux1.img`, md5 8fe431efba905c65a0b810fdb98d5b98. Answers open question 4.
- Method: zImage with the board device tree **appended** (`CONFIG_ARM_APPENDED_DTB`), stock "second" left untouched so U-Boot still initialises the panel and PMIC from the vendor tree, id recomputed with `port/rkimg.py`. Build: `port/build.sh` in the `skylight-build` Docker image.
- Working: 4 CPUs, 512 MB RAM, eMMC (`mmcblk0`, 14942208 sectors, 50 MHz high speed), USB device mode (dwc2 + g_cdc), simple-framebuffer registered at 0x7dc00000 as 800x1280 r5g6b5 (whether that guess matches what the panel shows is not yet confirmed).
- Access from the Mac: the frame appears as "CDC Composite Gadget" (0525:a4aa) with a network interface. Telnet to the frame's IPv6 link-local address on that interface reaches a root shell (bring-up only). The USB serial port carries kernel console output; the shell on it did not respond (open issue).
- Timing: the gadget appears about 60 s after `adb reboot recovery`; the kernel itself is up in under 2 s, the rest is Android shutting down and U-Boot.
- The bring-up init saves its boot log into the `kpanic` partition (raw sector 0x292000); `kpanic` therefore no longer matches the backup.
- Boot log: `port/logs/boot1-dmesg.txt`. Only errors: four "Failed to initialize /timer@200440x0: -22" lines (unused timers).

## U-Boot framebuffer and panel orientation (FACT, 2026-11-10)

- Read from the display controller (VOP at 0x1010e000) under the running mainline kernel: win0 enabled, format RGB565, buffer at 0x7dc00000, virtual stride 120 words, active size 240x750 (the logo), shown centred (DSP_ST 0x01270172). Panel timing: htotal 960, vtotal 1326, active start x=90, y=30.
- U-Boot therefore leaves a logo-sized window, not a full-screen one. Writing WIN0_VIR (0x30)=0x190, WIN0_ACT_INFO (0x34)=0x04FF031F, WIN0_DSP_INFO (0x38)=0x04FF031F, WIN0_DSP_ST (0x3c)=0x001E005A, then REG_CFG_DONE (0x90)=1 makes it full-screen 800x1280 RGB565, matching the simple-framebuffer node. Confirmed by photo: readable console text over the whole panel.
- Orientation: with the frame standing landscape, the framebuffer's top edge is the frame's right edge. A 1280x800 landscape image must be rotated 90 degrees counter-clockwise (PIL `ROTATE_90`) before being written as 800x1280.
- A calendar render was pushed from the Mac to `/dev/fb0` over USB Ethernet this way (fbcon unbound first so the console does not draw over it).

## GPIO levels under Android with Wi-Fi connected (FACT, 2026-11-10, `/sys/kernel/debug/gpio`)

- GPIO3_D3 (WL_REG_ON) output high; GPIO0_B6 (vendor "wlan reset") output **low** while Wi-Fi works, so that pin must stay low. U-Boot leaves both in the same state.
- GPIO3_C1 is claimed as `poweroff-gpio`, output low (high would cut power). The mainline tree does not touch it.
- The photo confirmed the calendar render upright, full screen, correct colours via simple-framebuffer.
- Build 2 (`port/images/recovery_linux2.img`) adds RK805, HYM8563, PWM1 backlight, Goodix, power key and brcmfmac; Wi-Fi firmware and nvram come from the backup's `system.img` (`/etc/firmware`), copied to `port/firmware/`.

## Build 2 result (FACT, 2026-11-10, from the kernel log over the USB shell)

- RK805 bound (regulators, clkout, pwrkey sub-devices), board stayed up through PMIC probe; cpufreq active (ondemand).
- HYM8563 registered as rtc0 and set the system clock (correct UTC time, kept from Android).
- Goodix reports ID 9271; input device created. Touch not yet exercised by hand.
- PWM backlight registered, brightness 128 of 255.
- BCM4345/6 enumerated on SDIO at 37 MHz, vendor firmware 7.45.69 loaded, `wlan0` up with the same MAC Android uses. No `regulatory.db` or clm_blob yet. Not yet associated to a network.
- The USB gadget took about 68 s to appear on the Mac this boot; cause not investigated.

## Open questions

1. ~~Boot image hash: is the check enforced?~~ Answered 2026-11-10: enforced, but it is a recomputable SHA-1 that also covers the cmdline. See "Boot image hash".
2. ~~microSD slot?~~ Answered: none.
3. ~~Which key combo enters loader mode?~~ Answered: `adb reboot bootloader`. Key entry untested.
4. ~~Will a mainline kernel boot from the stock U-Boot?~~ Answered 2026-11-10: yes, see "First mainline boot".
5. **Touch orientation:** final axis swap / inversion is found by testing.

## Files

| Path | Content |
|---|---|
| `backup.sh` | Read-only full eMMC backup over adb |
| `skylight_backup/<timestamp>/` (not in git) | Partition images, full-disk image, checksums, APKs, device info |
| `recon/extract.py` | Unpacks boot images and RSCE, decompiles DTBs |
| `recon/out/boot__rk-kernel.dtb.dts` | Stock device tree (decompiled) |

## Loader mode by key (2026-11-11)

- FACT: holding the ring's **right** button (speaker icon, reports `KEY_VOLUMEDOWN`, `rockchip,adc_value = <0x1>` in the stock DT) while power is plugged in, with USB connected to the Mac, enters loader mode (2207:310C, blank screen). `rkdeveloptool rd` returns to the normal boot. This works without Android, so it is the escape hatch once `boot` no longer holds Android.
- FACT: the ring's bottom button reports `KEY_PREVIOUSSONG` and does not enter loader mode. Ring top is `KEY_NEXTSONG`, left `KEY_VOLUMEUP` (INFERRED from press order), centre `KEY_PLAYPAUSE`.
- FACT: `boot` is mmcblk0p5 (12 MiB), `recovery` mmcblk0p6 (32 MiB), `misc` mmcblk0p2.
- FACT: the mainline kernel uses its built-in initramfs (`CONFIG_INITRAMFS_SOURCE`); the ramdisk in the boot image is ignored, so an init change needs a kernel rebuild. Rescue images change only the DT bootargs (`port/mkrescue.sh`).

## Slot offsets under the mainline kernel (FACT, 2026-11-11)

- The mainline kernel exposes only the `user` partition, so the slots are reached as regions of `/dev/mmcblk0`: `boot` is raw sector 90112 (0x16000), 24576 sectors (12 MiB); `recovery` is raw sector 114688 (0x1C000), 65536 sectors (32 MiB). Read back over SSH, both regions had the md5 of the images flashed to those slots (b34fd6bdf94c7f700d99ec4e76cd15e8 and 693642960edc21ab95acbcb47009fe64).
- `port/update_kernel.py` uses these offsets. FACT: its write mode wrote `recovery` three times and `boot` once on 2026-11-11, each verified by read-back and by an independent md5 of the region.

## Wi-Fi regulatory data (2026-11-11)

- FACT (kernel log, build 4): `cfg80211: failed to load regulatory.db` at 2.2 s. cfg80211 is built in and asks before the root filesystem is mounted, so Debian's copy in `/lib/firmware` is never seen and the regulatory domain stays at world (`country 00`).
- Build 5 puts the upstream-signed `regulatory.db` and `regulatory.db.p7s` (Debian `wireless-regdb`) into the initramfs. FACT (build 5 running from `boot`): the load failure is gone from the kernel log and `iw reg get` reports `country US: DFS-FCC`, the country set in the wpa_supplicant config.
- FACT: brcmfmac also reports `no clm_blob available`. The fitted firmware is the vendor's 7.45.69 from 2017. INFERRED: there is no clm_blob matching it; Wi-Fi associates and works without one.

## Starting the recovery slot without Android (FACT, tested 2026-11-11)

- No ring button starts `recovery`. Each of the five was held while power was plugged in, with no USB host attached; all started the normal boot. (With a USB host attached the right button enters loader mode, as recorded above.)
- The stock U-Boot reads a boot-mode word from the PMU at 0x100a0038 (base 0x100a0000, offset 0x38). Found by disassembling `uboot.img` from the backup: the routine at file offset 0x78f8 loads that register, and the caller at 0xae88 compares it, with the low byte masked, against 0x5242C300.
- Writing 0x5242C303 (Rockchip `BOOT_RECOVERY`) there from Linux and restarting starts the `recovery` slot. U-Boot rewrites the register to 0xC3524203, and the next restart starts `boot` again, so the flag works once. The register is not flash; a power cycle also clears it.
- The write must be one aligned 32-bit store. A byte-wise copy into the mapped register page raised a bus error.
- GRF 0x200081d8, which mainline's rk3036 device tree uses for the same purpose, is NOT read by this U-Boot: the value survived a restart there and the frame started normally.
- Helper: `/usr/local/sbin/skylight-reboot-recovery` on the frame (source in `port/rootfs/files`).
- Kernel build 5 was tried this way from `recovery` (full Debian, Wi-Fi associated, no failed units) before it was written to `boot`. `recovery` now holds the rescue variant of build 5.

## Build 6: configfs USB gadget (tested 2026-11-11 on the reference frame, macOS host)

- FACT: the legacy `g_cdc` gadget is replaced by a configfs gadget created in the initramfs `init` before Debian starts: NCM network, ACM serial, one removable read-only mass storage LUN. USB ID 1d6b:0104, product string "Skylight Frame Settings". The kernel log shows the gadget bound to `10180000.usb` about 2.6 s after kernel start.
- FACT: macOS lists it as a network port named "Skylight Frame Settings" and gets an address from the frame's DHCP server on `usb0` (address only, no router or DNS). `http://192.168.77.1` answers over the cable and is refused over Wi-Fi.
- FACT: the network addresses come from a hash of the eMMC CID, so the Mac kept the same interface across three boots. The frame's `usb0` link-local address differs from the one it had under build 5.
- FACT: `skylight-usb-drive.service` attaches a 2 MiB FAT image; macOS mounts it read-only as `FRAME` with `Open Frame Settings.html` and `README.txt`.
- FACT: the rescue image (`recovery_linux6_rescue.img`) was started once with `skylight-reboot-recovery`; its telnet shell answered on the IPv6 link-local address of the new interface, and `reboot -f` there returned the frame to `boot`.
- FACT: one USB serial port appears on the Mac in both the Debian and the rescue boot. Whether a shell answers on it was not tested.
- INFERRED: Windows 11 binds its in-box NCM driver to the network function. Not tested on any Windows version.
- Sequence used: build 6 normal image in `recovery`, tested there, then `boot_linux6.img` to `boot` and the rescue image to `recovery`, all with `port/update_kernel.py`.

