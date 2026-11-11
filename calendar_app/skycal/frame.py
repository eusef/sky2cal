"""Run the calendar on the frame: draw to the framebuffer, page weeks by touch, refresh by itself.

Usage: python3 -m skycal.frame --config config.toml [--cache-dir DIR] [--fb /dev/fb0]

The panel is 800x1280 portrait, RGB565; the frame is mounted landscape with the framebuffer's
top edge on the right, so the 1280x800 picture is turned a quarter turn before it is written.
"""
from __future__ import annotations

import argparse
import os
import queue
import select
import sys
import threading
import time
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageChops

from .feed import events_between, fetch
from .render import NAV, H, W, fmt_time, render_week, week_start

FB_W, FB_H = H, W            # 800 x 1280
TAP_MARGIN = 24              # px of slack around each button
IDLE_BACK_S = 300            # return to the current week after this long without a tap
RETRY_S = 60                 # fetch again this soon when a feed was not fresh
REDRAW_S = 600               # redraw at least this often
WINDOW_BACK_W, WINDOW_AHEAD_W = 26, 78   # weeks around today that are read ahead of time
TICK_S = 15                  # longest sleep of the display loop


def rgb565(img: Image.Image) -> bytes:
    """Little-endian RGB565 bytes of an RGB image."""
    r, g, b = img.split()
    hi = ImageChops.add(r.point(lambda v: v & 0xF8), g.point(lambda v: v >> 5))
    lo = ImageChops.add(g.point(lambda v: (v & 0x1C) << 3), b.point(lambda v: v >> 3))
    return Image.merge("LA", (lo, hi)).tobytes()


def show(img: Image.Image, fb: Path) -> None:
    sysfs = Path("/sys/class/graphics") / fb.name
    size = (sysfs / "virtual_size").read_text().strip()
    bpp = (sysfs / "bits_per_pixel").read_text().strip()
    if (size, bpp) != (f"{FB_W},{FB_H}", "16"):
        raise SystemExit(f"{fb} is {size} at {bpp} bpp, expected {FB_W},{FB_H} at 16")
    data = rgb565(img.transpose(Image.Transpose.ROTATE_90))
    with fb.open("r+b") as f:
        f.write(data)


def to_picture(tx: int, ty: int, xmax: int, ymax: int, flip_x: bool, flip_y: bool) -> tuple[int, int]:
    """Touch panel coordinates -> coordinates in the landscape picture."""
    fx = tx * FB_W // (xmax + 1)
    fy = ty * FB_H // (ymax + 1)
    if flip_x:
        fx = FB_W - 1 - fx
    if flip_y:
        fy = FB_H - 1 - fy
    return W - 1 - fy, fx     # inverse of the quarter turn in show()


def button_at(x: int, y: int) -> str | None:
    for name, (x0, y0, x1, y1) in NAV.items():
        if x0 - TAP_MARGIN <= x <= x1 + TAP_MARGIN and y0 - TAP_MARGIN <= y <= y1 + TAP_MARGIN:
            return name
    return None


def open_touch():
    """The first input device that reports multitouch positions, or None."""
    try:
        import evdev
        from evdev import ecodes as e
        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            axes = dict(dev.capabilities().get(e.EV_ABS, []))
            if e.ABS_MT_POSITION_X in axes and e.ABS_MT_POSITION_Y in axes:
                return dev, axes[e.ABS_MT_POSITION_X].max, axes[e.ABS_MT_POSITION_Y].max
    except Exception as err:
        print(f"no touch input: {err}", file=sys.stderr)
    return None


def taps(dev, pos: dict) -> list[tuple[int, int]]:
    """Read pending events; return the positions where a finger was lifted."""
    from evdev import ecodes as e
    out = []
    for ev in dev.read():
        if ev.type == e.EV_ABS and ev.code == e.ABS_MT_POSITION_X:
            pos["x"] = ev.value
        elif ev.type == e.EV_ABS and ev.code == e.ABS_MT_POSITION_Y:
            pos["y"] = ev.value
        elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH and ev.value == 0 and "x" in pos and "y" in pos:
            out.append((pos["x"], pos["y"]))
    return out


def fetch_loop(feeds: list, cache: Path, refresh_s: int, tz: ZoneInfo, results: queue.SimpleQueue, wake_fd: int) -> None:
    """Background thread: fetch every feed, hand the results to the display loop, repeat.
    Runs apart from the display loop so a slow or dead network never delays a tap.

    Reading a large calendar takes seconds on the frame, so it is also done here, once per
    fetch, for a wide window of weeks; paging inside that window then only has to draw.
    Each entry is (ics, saved_at, events or None, window first day, window last day)."""
    while True:
        data = []
        today = datetime.now(tz).date()
        first, last = today - timedelta(weeks=WINDOW_BACK_W), today + timedelta(weeks=WINDOW_AHEAD_W)
        for i, url in enumerate(feeds):
            ics, saved_at, events = None, None, None
            try:
                ics, saved_at = fetch(url, cache)
                if ics is not None:
                    events = events_between(ics, first, last, tz, cal=i)
            except Exception as err:
                print(f"feed {i} not usable: {type(err).__name__}", file=sys.stderr)
            data.append((ics, saved_at, events, first, last))
        results.put((data, time.time()))
        os.write(wake_fd, b"x")
        fresh = all(entry[0] is not None and entry[1] is None for entry in data)
        time.sleep(refresh_s if fresh else min(refresh_s, RETRY_S))


def compose(cfg: dict, tz: ZoneInfo, data: list, fetched_at: float | None, offset: int) -> Image.Image:
    now = datetime.now(tz)
    start = week_start(now.date(), cfg.get("first_weekday", 6)) + timedelta(weeks=offset)
    if fetched_at is None:
        return render_week(start, [], now, "Starting", stale=False)
    events, oldest, missing = [], None, 0
    end = start + timedelta(days=6)
    for i, (ics, saved_at, prepared, first, last) in enumerate(data):
        try:
            if ics is None:
                raise ValueError("no data")
            if prepared is not None and first <= start and end <= last:
                events += prepared                    # render_week picks out the days it shows
            else:
                events += events_between(ics, start, end, tz, cal=i)   # outside the window: slow
        except Exception:
            missing += 1
            continue
        if saved_at is not None:
            oldest = min(oldest, saved_at) if oldest else saved_at
    if missing:
        note = "No calendar data yet" if missing == len(data) else "Some calendars unavailable"
    elif oldest:
        saved = oldest.astimezone(tz)
        note = f"Offline, saved {saved:%b} {saved.day} {fmt_time(saved)}"
    else:
        return render_week(start, events, now, f"Updated {fmt_time(datetime.fromtimestamp(fetched_at, tz))}", stale=False)
    return render_week(start, events, now, note, stale=True)


def main() -> None:
    ap = argparse.ArgumentParser(prog="skycal.frame")
    ap.add_argument("--config", type=Path, default=Path("config.toml"))
    ap.add_argument("--cache-dir", type=Path, help="overrides cache_dir from the config")
    ap.add_argument("--fb", type=Path, default=Path("/dev/fb0"))
    args = ap.parse_args()

    cfg = tomllib.loads(args.config.read_text())
    tz = ZoneInfo(cfg["timezone"])
    cache = args.cache_dir or Path(cfg.get("cache_dir", "cache"))
    refresh_s = max(1, int(cfg.get("refresh_minutes", 10))) * 60
    # Measured on the frame (2026-11-11): the touch panel is turned half a turn against the
    # framebuffer, so both axes are flipped unless the config says otherwise.
    flip_x, flip_y = bool(cfg.get("touch_flip_x", True)), bool(cfg.get("touch_flip_y", True))
    feeds: list = cfg["feeds"]
    data: list = []
    fetched_at = None

    # First picture before any network use, so a framebuffer problem stops the service at once.
    show(compose(cfg, tz, data, fetched_at, 0), args.fb)

    results: queue.SimpleQueue = queue.SimpleQueue()
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_r, False)
    threading.Thread(target=fetch_loop, args=(feeds, cache, refresh_s, tz, results, wake_w), daemon=True).start()

    touch = open_touch()
    pos: dict = {}
    offset, last_tap, last_draw, dirty = 0, 0.0, time.monotonic(), False

    while True:
        mono = time.monotonic()
        while not results.empty():
            data, fetched_at = results.get()
            dirty = True
        if offset and mono - last_tap > IDLE_BACK_S:
            offset, dirty = 0, True
        if mono - last_draw > REDRAW_S:      # keeps today's highlight right across midnight
            dirty = True
        if dirty:
            show(compose(cfg, tz, data, fetched_at, offset), args.fb)
            last_draw, dirty = time.monotonic(), False

        if touch is None:
            touch = open_touch()
        fds = [wake_r] + ([touch[0].fd] if touch else [])
        ready = select.select(fds, [], [], TICK_S)[0]
        if wake_r in ready:
            try:
                os.read(wake_r, 64)
            except BlockingIOError:
                pass
        if touch and touch[0].fd in ready:
            dev, xmax, ymax = touch
            try:
                for tx, ty in taps(dev, pos):
                    name = button_at(*to_picture(tx, ty, xmax, ymax, flip_x, flip_y))
                    if name:
                        offset = {"prev": offset - 1, "next": offset + 1, "today": 0}[name]
                        last_tap, dirty = time.monotonic(), True
            except OSError as err:
                print(f"touch input lost: {err}", file=sys.stderr)
                touch = None


if __name__ == "__main__":
    main()
