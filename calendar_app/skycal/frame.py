"""Run the calendar on the frame: draw to the framebuffer, react to touch, refresh by itself.

Usage: python3 -m skycal.frame --config config.toml [--cache-dir DIR] [--fb /dev/fb0]

The panel is 800x1280 portrait, RGB565; the frame is mounted landscape with the framebuffer's
top edge on the right, so the 1280x800 picture is turned a quarter turn before it is written.

Screens: the week (tap an event for its details, a day's heading or "+N more" for the whole
day, the weather button for the hourly forecast; swipe sideways to change week), and a
screensaver that takes over after a while without a touch and dims the backlight. Any touch
ends the screensaver. SIGUSR1 starts it at once (for testing).
"""
from __future__ import annotations

import argparse
import os
import queue
import select
import signal
import sys
import threading
import time
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageChops

from . import weather as wx
from .feed import Event, events_between, fetch
from .render import (NAV, H, W, fmt_time, render_day, render_detail, render_saver, render_weather, render_week,
                     week_start)

FB_W, FB_H = H, W            # 800 x 1280
TAP_MARGIN = 24              # px of slack around each button
TAP_SLOP = 40                # a finger that moved less than this (px) made a tap
SWIPE_MIN = 120              # a finger that moved at least this far sideways (px) made a swipe
IDLE_BACK_S = 300            # return to the current week after this long without a touch
CARD_BACK_S = 120            # close details, a day list or the weather after this long without a touch
RETRY_S = 60                 # fetch again this soon when a feed was not fresh
WEATHER_S = 30 * 60          # how often the forecast is fetched again
WINDOW_BACK_W, WINDOW_AHEAD_W = 26, 78   # weeks around today that are read ahead of time
TICK_S = 15                  # longest sleep of the display loop
SAVER_DEFAULT_MIN = 10       # minutes without a touch before the screensaver; 0 in the config turns it off
SAVER_DIM = 0.2              # backlight during the screensaver, as a share of its normal level


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


class Backlight:
    """Dims the panel for the screensaver. The normal level is kept in a file while the panel
    is dimmed, so a restart in between still finds its way back. Does nothing where the
    brightness file is missing or not writable (the service unit opens it to the video group)."""

    def __init__(self, state_dir: Path):
        found = sorted(Path("/sys/class/backlight").glob("*/brightness"))
        self.path = found[0] if found else None
        self.saved = state_dir / "brightness"
        self.restore()

    def _write(self, level: int) -> bool:
        try:
            self.path.write_text(str(level))
            return True
        except OSError as err:
            print(f"backlight not changed: {type(err).__name__}", file=sys.stderr)
            self.path = None
            return False

    def dim(self) -> None:
        if self.path is None or self.saved.exists():
            return
        try:
            normal = int(self.path.read_text())
            self.saved.write_text(str(normal))
        except (OSError, ValueError):
            return
        if not self._write(max(1, int(normal * SAVER_DIM))):
            self.saved.unlink(missing_ok=True)

    def restore(self) -> None:
        if self.path is None or not self.saved.exists():
            return
        try:
            normal = int(self.saved.read_text())
        except (OSError, ValueError):
            normal = 0
        if normal <= 0 or self._write(normal):
            self.saved.unlink(missing_ok=True)


def to_picture(tx: int, ty: int, xmax: int, ymax: int, flip_x: bool, flip_y: bool) -> tuple[int, int]:
    """Touch panel coordinates -> coordinates in the landscape picture."""
    fx = tx * FB_W // (xmax + 1)
    fy = ty * FB_H // (ymax + 1)
    if flip_x:
        fx = FB_W - 1 - fx
    if flip_y:
        fy = FB_H - 1 - fy
    return W - 1 - fy, fx     # inverse of the quarter turn in show()


def inside(box: tuple, x: int, y: int, margin: int = 0) -> bool:
    return box[0] - margin <= x <= box[2] + margin and box[1] - margin <= y <= box[3] + margin


def button_at(x: int, y: int) -> str | None:
    """The header button under a tap; a tap that only lands in a button's margin counts too,
    but never against the button it actually hit."""
    for margin in (0, TAP_MARGIN):
        for name, box in NAV.items():
            if inside(box, x, y, margin):
                return name
    return None


def target_at(hits: list, x: int, y: int):
    """What was drawn under a tap: an Event, a date (that whole day), an int (a page of the day
    list) or None. Events win over the day behind them."""
    found = [t for x0, y0, x1, y1, t in hits if x0 <= x <= x1 and y0 <= y <= y1]
    return next((t for t in found if isinstance(t, Event)), found[0] if found else None)


def gesture(down: tuple[int, int], up: tuple[int, int]) -> str | None:
    """"tap", "left" or "right" (the way the finger moved) from picture coordinates; None for anything else."""
    dx, dy = up[0] - down[0], up[1] - down[1]
    if abs(dx) < TAP_SLOP and abs(dy) < TAP_SLOP:
        return "tap"
    if abs(dx) >= SWIPE_MIN and abs(dx) > 2 * abs(dy):
        return "left" if dx < 0 else "right"
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


def touches(dev, pos: dict) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Read pending events; return (where the finger came down, where it was lifted) per touch,
    in touch panel coordinates. The position of a new touch is only known at the end of the
    report that announces it, so that is where it is taken."""
    from evdev import ecodes as e
    out = []
    for ev in dev.read():
        if ev.type == e.EV_ABS and ev.code == e.ABS_MT_POSITION_X:
            pos["x"] = ev.value
        elif ev.type == e.EV_ABS and ev.code == e.ABS_MT_POSITION_Y:
            pos["y"] = ev.value
        elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
            if ev.value == 1:
                pos["landing"] = True
            elif ev.value == 0 and "x" in pos and "y" in pos:
                here = (pos["x"], pos["y"])
                out.append((here if pos.pop("landing", False) else pos.pop("down", here), here))
                pos.pop("down", None)
        elif ev.type == e.EV_SYN and ev.code == e.SYN_REPORT and pos.get("landing") and "x" in pos and "y" in pos:
            pos["down"] = (pos["x"], pos["y"])
            pos["landing"] = False
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
        results.put(("feeds", (data, time.time())))
        os.write(wake_fd, b"x")
        fresh = all(entry[0] is not None and entry[1] is None for entry in data)
        time.sleep(refresh_s if fresh else min(refresh_s, RETRY_S))


def weather_loop(place: str, units: str, cache: Path, tz: ZoneInfo, results: queue.SimpleQueue, wake_fd: int) -> None:
    """Background thread: fetch the forecast, hand it to the display loop, repeat. After a
    failure it tries again in a minute, then waits twice as long each time, up to an hour."""
    failures = 0
    while True:
        forecast, fresh = None, False
        try:
            forecast, fresh = wx.fetch(place, units, tz, cache)
        except Exception as err:
            print(f"weather not usable: {type(err).__name__}", file=sys.stderr)
        results.put(("weather", forecast))
        os.write(wake_fd, b"x")
        failures = 0 if fresh else failures + 1
        time.sleep(WEATHER_S if fresh else min(3600, RETRY_S * 2 ** (failures - 1)))


def gather(data: list, tz: ZoneInfo, first: date, last: date) -> tuple[list, datetime | None, int]:
    """(events touching first..last, when the oldest cached feed was saved, feeds without data)."""
    events, oldest, missing = [], None, 0
    for i, (ics, saved_at, prepared, lo, hi) in enumerate(data):
        try:
            if ics is None:
                raise ValueError("no data")
            if prepared is not None and lo <= first and last <= hi:
                events += prepared                    # the drawing code picks out the days it shows
            else:
                events += events_between(ics, first, last, tz, cal=i)   # outside the window: slow
        except Exception:
            missing += 1
            continue
        if saved_at is not None:
            oldest = min(oldest, saved_at) if oldest else saved_at
    return events, oldest, missing


def compose(cfg: dict, tz: ZoneInfo, data: list, fetched_at: float | None, offset: int, now: datetime | None = None,
            hits: list | None = None, forecast=None) -> tuple[Image.Image, list]:
    """(picture of the week `offset` weeks from this one, the events it was drawn from)."""
    now = now or datetime.now(tz)
    start = week_start(now.date(), cfg.get("first_weekday", 6)) + timedelta(weeks=offset)
    if fetched_at is None:
        return render_week(start, [], now, "Starting", hits=hits, weather=forecast), []
    events, oldest, missing = gather(data, tz, start, start + timedelta(days=6))
    note, stale = "", True
    if missing:
        note = "No calendar data yet" if missing == len(data) else "Some calendars unavailable"
    elif oldest:
        saved = oldest.astimezone(tz)
        note = f"Offline, saved {saved:%b} {saved.day} {fmt_time(saved)}"
    message = ""
    if not data:
        message = ("No calendars yet. Connect the frame to a computer with its USB cable, open the drive "
                   "named FRAME and follow the link to the settings page.")
    return render_week(start, events, now, note, stale, hits, forecast, message), events


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
    place = str(cfg.get("weather_place", "")).strip()
    saver_s = max(0, int(cfg.get("screensaver_minutes", SAVER_DEFAULT_MIN))) * 60
    data: list = []
    fetched_at = None
    forecast = None

    # First picture before any network use, so a framebuffer problem stops the service at once.
    show(compose(cfg, tz, data, fetched_at, 0)[0], args.fb)
    cache.mkdir(parents=True, exist_ok=True)
    backlight = Backlight(cache)

    results: queue.SimpleQueue = queue.SimpleQueue()
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_r, False)
    os.set_blocking(wake_w, False)
    threading.Thread(target=fetch_loop, args=(feeds, cache, refresh_s, tz, results, wake_w), daemon=True).start()
    if place:
        threading.Thread(target=weather_loop, args=(place, str(cfg.get("weather_units", "fahrenheit")), cache, tz,
                                                    results, wake_w), daemon=True).start()

    asked = {"saver": False}

    def on_usr1(signum, frame) -> None:
        asked["saver"] = True
        try:
            os.write(wake_w, b"s")
        except OSError:
            pass

    def on_term(signum, frame) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGUSR1, on_usr1)
    signal.signal(signal.SIGTERM, on_term)

    touch = open_touch()
    pos: dict = {}
    # view: "week", "detail" (one event), "day" (one day's list), "weather" or "saver"
    view, chosen, chosen_day, day_first, back_to = "week", None, None, 0, "week"
    offset, last_touch, drawn_minute, dirty = 0, time.monotonic(), None, True
    hits: list = []

    try:
        while True:
            mono = time.monotonic()
            idle = mono - last_touch
            while not results.empty():
                kind, value = results.get()
                if kind == "feeds":
                    data, fetched_at = value
                else:
                    forecast = value
                dirty = True
            if asked["saver"]:
                asked["saver"] = False
                if view != "saver":
                    view, offset, dirty = "saver", 0, True
                    backlight.dim()
            if view in ("detail", "day", "weather") and idle > CARD_BACK_S:
                view, dirty = "week", True
            if offset and idle > IDLE_BACK_S:
                offset, dirty = 0, True
            if saver_s and touch and view != "saver" and idle > saver_s:    # never without a way back
                view, offset, dirty = "saver", 0, True
                backlight.dim()
            now = datetime.now(tz)
            minute = (now.date(), now.hour, now.minute)
            if minute != drawn_minute:           # the clock, "now" marks and today's highlight
                dirty = True
            if dirty:
                try:
                    hits = []
                    if view == "saver":
                        events = gather(data, tz, now.date(), now.date() + timedelta(days=1))[0] if fetched_at else []
                        img = render_saver(now, events, forecast)
                    elif view == "weather":
                        message = "" if place else "Type your town on the settings page to see the weather here."
                        img = render_weather(forecast, now, message or "The forecast has not arrived yet.")
                    else:
                        week_hits: list = []
                        img, events = compose(cfg, tz, data, fetched_at, offset, now, week_hits, forecast)
                        if view == "detail" and chosen is not None:
                            if back_to == "day" and chosen_day is not None:
                                img = render_day(img, chosen_day, events, now, first=day_first)
                            img = render_detail(img, chosen, now)
                        elif view == "day" and chosen_day is not None:
                            img = render_day(img, chosen_day, events, now, hits, day_first)
                        else:
                            view, hits = "week", week_hits
                    show(img, args.fb)
                    drawn_minute, dirty = minute, False
                except Exception as err:
                    # A screen that cannot be drawn must not take the calendar down with it: go back
                    # to the plain week. If that is what failed, let the service restart.
                    print(f"{view} screen not drawn: {type(err).__name__}", file=sys.stderr)
                    if view == "week":
                        raise
                    if view == "saver":
                        backlight.restore()
                        last_touch = time.monotonic()        # try the screensaver again after the usual wait
                    view, dirty = "week", True
                    continue

            if touch is None:
                touch = open_touch()
            fds = [wake_r] + ([touch[0].fd] if touch else [])
            wait = min(TICK_S, 60.5 - datetime.now(tz).second)
            ready = select.select(fds, [], [], wait)[0]
            if wake_r in ready:
                try:
                    os.read(wake_r, 64)
                except BlockingIOError:
                    pass
            if not (touch and touch[0].fd in ready):
                continue
            dev, xmax, ymax = touch
            try:
                pairs = touches(dev, pos)
            except OSError as err:
                print(f"touch input lost: {err}", file=sys.stderr)
                touch = None
                continue
            for down, up in pairs:
                last_touch, dirty = time.monotonic(), True
                x, y = to_picture(*down, xmax, ymax, flip_x, flip_y)
                move = gesture((x, y), to_picture(*up, xmax, ymax, flip_x, flip_y))
                if view == "saver":                       # a touch only wakes the screen; the rest of
                    view = "week"                         # this batch was aimed at the dark screen too
                    backlight.restore()
                    break
                elif view == "detail":                    # any touch closes the card
                    view = back_to
                elif view == "weather":
                    view = "week"
                elif view == "day":
                    target = target_at(hits, x, y) if move == "tap" else None
                    if isinstance(target, Event):
                        view, chosen, back_to = "detail", target, "day"
                    elif isinstance(target, int):
                        day_first = target
                    elif move == "tap":
                        view = "week"
                elif move in ("left", "right"):
                    offset += 1 if move == "left" else -1
                elif move == "tap":
                    name = button_at(x, y)
                    target = None if name else target_at(hits, x, y)
                    if name == "weather":
                        view = "weather"
                    elif name:
                        offset = {"prev": offset - 1, "next": offset + 1, "today": 0}[name]
                    elif isinstance(target, Event):
                        view, chosen, back_to = "detail", target, "week"
                    elif isinstance(target, date):
                        view, chosen_day, day_first = "day", target, 0
    finally:
        backlight.restore()


if __name__ == "__main__":
    main()
