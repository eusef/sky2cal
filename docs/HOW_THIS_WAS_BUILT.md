# How this was built

This project was made by one person and Claude Code (Anthropic's coding agent, running in a terminal on a Mac) over three working days: 2026-11-03, 2026-11-10 and 2026-11-11. The owner had never done a kernel port. This is an account of how the work was divided and what the process looked like, including the parts that went wrong.

## The starting point

The frame shipped with Android 5.1 carrying 2016 security patches. Recon later showed that the vendor apps run as root through a preinstalled SuperSU and remount `/system` read-write over and over. The owner did not want that on the home network. Three options were on the table: lock Android down, buy different hardware, or replace the operating system. The owner chose the third, on one condition: find out whether it is feasible before changing anything.

## Who did what

| Owner | Claude Code |
|---|---|
| Set the goal and made every decision with a cost: go or no-go, which distro, which partition to erase, when to give up Android | Laid out the options with a recommendation, then carried out the choice |
| Physical work: cables, buttons, power, photos of the screen and the board | Read-only inspection of the device over adb, the backup script, unpacking the firmware |
| Ran every command that writes to a device partition | Wrote the device tree, kernel config, init, image packer, root filesystem build and the calendar app |
| Eyes and fingers: confirmed what the screen showed and whether taps landed | Staged images, computed checksums, verified results over telnet and SSH |

## Timeline

| Date | What happened |
|---|---|
| 2026-11-03 | Read-only inspection over root adb. Full eMMC backup (about an hour). Boot images unpacked, stock device tree decompiled, each chip checked against mainline kernel source. Verdict: go. A separate read-only agent fact-checked the findings (20 of 20 confirmed). Work paused. |
| 2026-11-10 morning | Product goal set: a landscape week-view wall calendar reading the family calendar. Loader mode found. The boot image check turned out to be a plain SHA-1 that can be recomputed, proven by flashing a stock recovery with one byte changed. First mainline boot from the `recovery` slot, reachable by telnet over USB Ethernet. |
| 2026-11-10 afternoon | Screen console confirmed by photo. A calendar picture pushed from the Mac appeared on the panel. Second kernel build bound the PMIC, clock, backlight, touch controller, power key and Wi-Fi. |
| 2026-11-10 evening | Debian 13 image built and reviewed. Decision: install on the `user` partition and keep Android bootable as a fallback. |
| 2026-11-11 morning | Debian running on the frame: SSH keys only, firewall, automatic upgrades. Calendar running as a locked-down systemd service. |
| 2026-11-11 midday | Wi-Fi joined, family calendar added. Loader mode by hardware key confirmed, which made it safe to give up adb. |
| 2026-11-11 afternoon | Kernel written to `boot`. The frame now starts the calendar on a cold power-on. |
| 2026-11-11 evening | Owner tested by hand. Two real bugs found and fixed (below). |

## The working method

**Recon before commitment.** Nothing was written to the device on the first day. The output of that day was `RECON.md` and a go or no-go answer, not code.

**Every claim labelled.** `RECON.md` marks each statement FACT (read directly from the device or source), INFERRED or ASSUMED. Several ASSUMED items were wrong and were caught because they were marked as open: an early note said the kernel command line was not covered by the image hash (it is), and the touch orientation was a guess until a finger tested it.

**One reversible step at a time.** The order was chosen so each write could be undone by the step before it: backup first, then find loader mode (the way back in), then a one-byte test of the hash check in the `recovery` slot, then a kernel in `recovery` with Android still on `boot`, then Debian on a partition Android does not need, and only at the end the `boot` slot, after the hardware-key route into loader mode had been confirmed.

**A human hand on every destructive command.** The owner's Claude Code setup has a hook that blocks any shell command writing to a device node or force-deleting recursively. Claude prepared each flash (image, checksum, exact command) and the owner ran it. At one point the owner asked Claude to run the flashes itself; the hook still refused, and the agreed rule was not to route around it. The same hook blocked a cleanup command while this document was being prepared.

**Independent review.** Changes were checked by a separate reviewer agent that was given only the requirements, not the reasoning behind the implementation, so it could not simply agree. The root filesystem build passed on its second round.

**Scripts that fail closed.** The build scripts verify their own output (required kernel options, image size limits, file modes, a locked root password) and leave the output directory empty on any failure, so a stale image can never be mistaken for a fresh one. The installer refuses to write unless the partition geometry and the running kernel are exactly as expected, and reads the data back afterwards.

**Notes that survive the session.** Claude Code starts each session without memory of the last. Continuity came from `RECON.md`, `CLAUDE.md` and a short running status note that Claude keeps between sessions. The week-long pause between day one and day two cost nothing because of them.

## What went wrong, and what it taught

| Problem | How it showed | Resolution |
|---|---|---|
| `rkdeveloptool ppt` wedged the loader | USB session hung; only pulling the power recovered it | Never run it; recorded as a rule |
| Second kernel image refused | Frame dropped to loader mode 4 seconds after reboot, too early for a kernel to have run | Every accepted image had kernel and ramdisk lengths divisible by 4; the packer now pads |
| Screen showed only a narrow strip | U-Boot leaves a logo-sized window on the display controller | Five register writes in the init widen it to the full panel |
| An init change had no effect | The kernel uses its built-in initramfs and ignores the one in the boot image | Init changes mean a kernel rebuild; rescue images change only the boot arguments |
| Injected test taps were dropped | The kernel discards repeated touch events with unchanged coordinates | Vary the coordinates in tests |
| Taps landed in the wrong place | The touch panel is mounted half a turn against the display | Both axes flipped by default; found only when a real finger tried it |
| Paging weeks took over 5 seconds | Parsing a 574 KB calendar takes 5.4 s on this CPU | Parse once per fetch in the background for a two-year window; a tap now takes 0.6 s |
| Background jobs on the frame died | Processes started over SSH end with the session | `systemd-run --unit` |

The last three were invisible to every automated check. They were found because a person stood in front of the device.

## What Claude Code was good and bad at here

Good: reading a decompiled vendor device tree against mainline driver bindings, writing the board device tree, reverse-engineering the image hash by reproducing known ids, keeping long procedures consistent, and writing scripts that check themselves.

Limited: it cannot see the screen, press a button or feel a tap, and tests it wrote for itself passed while the real thing was wrong (touch orientation, responsiveness). It also needed the hook and the review step: left alone, an agent that can run `dd` is one typo away from erasing the wrong partition.

## Preparing this repository

The repository was assembled at the end, also by Claude Code: the setup guide was written from the scripts and notes, commands that could be re-run without the device were re-run (one needed correcting), and personal details were removed (commit identity, home directory paths, device identifiers, home network addresses, the owner's name). History starts at a single clean commit for that reason.
