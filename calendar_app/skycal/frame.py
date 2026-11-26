"""Run the calendar on the frame: draw to the framebuffer, react to touch, refresh by itself.

Usage: python3 -m skycal.frame --config config.toml [--cache-dir DIR] [--fb /dev/fb0]

The panel is 800x1280 portrait, RGB565; the frame is mounted landscape with the framebuffer's
top edge on the right, so the 1280x800 picture is turned a quarter turn before it is written.

Screens: the week (tap an event for its details, a day's heading or "+N more" for the whole
day, the weather button for the hourly forecast, the alert band for the weather alerts, the
gear for a QR code that opens the settings page on a phone (remote.py) and buttons that change
the look at once; swipe sideways to change week), and a screensaver that takes over after a
while without a touch and dims the backlight. Any touch ends the screensaver. SIGUSR1 starts it
at once (for testing). How the screens look is the theme named in the config (`theme`), or the
one last picked on the frame while the config still names the same theme (themes.chosen()).
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
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageChops

from . import alerts as al
from . import remote, themes
from . import weather as wx
from .feed import READ_TIMEOUT_S, Event, events_between, feed_key, load_snapshot, read_feeds, read_feeds_apart
from .render import (NAV, H, W, configure, fmt_time, render_alerts, render_day, render_detail, render_saver,
                     render_settings, render_weather, render_week, theme, week_start)

FB_W, FB_H = H, W            # 800 x 1280
TAP_MARGIN = 24              # px of slack around each button
TAP_SLOP = 40                # a finger that moved less than this (px) made a tap
SWIPE_MIN = 120              # a finger that moved at least this far sideways (px) made a swipe
IDLE_BACK_S = 300            # return to the current week after this long without a touch
CARD_BACK_S = 120            # close details, a day list or the weather after this long without a touch
SETTINGS_BACK_S = 300        # close the settings QR code (and withdraw it) after this long without a touch
RETRY_S = 60                 # fetch again this soon when a feed was not fresh
WEATHER_S = 30 * 60          # how often the forecast is fetched again
ALERTS_S = 5 * 60            # how often the weather alerts are fetched again
WINDOW_BACK_W, WINDOW_AHEAD_W = 26, 78   # weeks around today that are read ahead of time
TICK_S = 15                  # longest sleep of the display loop
SAVER_DEFAULT_MIN = 10       # minutes without a touch before the screensaver; 0 in the config turns it off
SAVER_DIM = 0.2              # backlight during the screensaver, as a share of its normal level
AHEAD_PAUSE_S = 0.1          # how long the screen is left alone before weeks are drawn ahead


_fast565 = True              # until Pillow turns out not to have the "BGR;16" mode (gone in Pillow 12)
warnings.filterwarnings("ignore", message="BGR;16", category=DeprecationWarning)


def rgb565(img: Image.Image) -> bytes:
    """Little-endian RGB565 bytes of an RGB image."""
    global _fast565
    if _fast565:
        # Pillow's own conversion: the same bytes as the channel arithmetic below, in a sixth
        # of the time on the frame (39 ms against 224 ms, measured 2026-11-19).
        try:
            return img.convert("BGR;16").tobytes()
        except Exception:
            _fast565 = False
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
    list, or an alert), "alerts" (the alert band) or None. Events win over the day behind them."""
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

    Reading a large calendar takes seconds on the frame, so it is also done from here, once
    per fetch, for a wide window of weeks; paging inside that window then only has to draw.
    The reading itself runs in a process of its own (see read_feeds_apart), and in this one
    only if that fails. Each entry is (ics, saved_at, events or None, window first day,
    window last day).

    Before the first read it hands over the events the last read kept (load_snapshot), so
    after a restart the week shows them within a second instead of after that read. It is
    written at the end of each read and the next read starts refresh_s later, so a healthy one
    is at most refresh_s plus a read old; 5 more minutes cover a restart or a reboot."""
    kept = load_snapshot(feeds, cache, tz, max_age=refresh_s + READ_TIMEOUT_S + 300)
    if kept is not None:
        results.put(("feeds", (kept, time.time())))
        os.write(wake_fd, b"x")
    while True:
        today = datetime.now(tz).date()
        first, last = today - timedelta(weeks=WINDOW_BACK_W), today + timedelta(weeks=WINDOW_AHEAD_W)
        try:
            data = read_feeds_apart(feeds, cache, first, last, tz)
        except Exception as err:
            print(f"feeds read in the display's own process: {type(err).__name__}", file=sys.stderr)
            data = read_feeds(feeds, cache, first, last, tz)
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


def alerts_loop(place: str, cache: Path, tz: ZoneInfo, results: queue.SimpleQueue, wake_fd: int) -> None:
    """Background thread: fetch the weather alerts for the place's coordinates, hand them to the
    display loop, repeat. After a failure it waits as weather_loop() does. Outside the United
    States every fetch fails and nothing is ever shown."""
    failures = 0
    while True:
        found, fresh = None, False
        try:
            lat, lon, _ = wx.locate(place, cache)
            found, fresh = al.fetch(lat, lon, tz, cache)
        except Exception as err:
            print(f"weather alerts not usable: {type(err).__name__}", file=sys.stderr)
        results.put(("alerts", found or []))
        os.write(wake_fd, b"x")
        failures = 0 if fresh else failures + 1
        time.sleep(ALERTS_S if fresh else min(3600, RETRY_S * 2 ** (failures - 1)))


def feed_colors(cfg: dict) -> dict:
    """{feed number: colour} from the config's feed_colors table, which is keyed by feed_key()."""
    chosen = cfg.get("feed_colors", {})
    if not isinstance(chosen, dict):
        return {}
    return {i: chosen[feed_key(url)] for i, url in enumerate(cfg.get("feeds", []))
            if isinstance(url, str) and isinstance(chosen.get(feed_key(url)), str)}


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


def read_ahead(data: list, first: date, last: date) -> bool:
    """True when gather() can answer for first..last without reading a calendar again (which takes seconds)."""
    return all(ics is None or (prepared is not None and lo <= first and last <= hi)
               for ics, _, prepared, lo, hi in data)


class Touched(Exception):
    """Raised inside a week that is being drawn ahead when a finger arrives: the touch goes first."""


def finger_down(pos: dict) -> bool:
    """True between a finger's landing and its lifting, going by what touches() keeps in `pos`."""
    return bool(pos.get("landing")) or "down" in pos


def next_turn(events: list, now: datetime) -> datetime | None:
    """The next moment after `now` at which an event starts or ends in the middle of a minute
    (a picture is drawn anew every minute anyway); None if there is none."""
    return min((t for ev in events for t in (ev.start, ev.end) if (t.second or t.microsecond) and t > now), default=None)


class Weeks:
    """Week pictures already drawn, so that a card opens and closes, and a neighbouring week
    or the current one appears, without drawing the week again.

    `draw(which, now, pause)` gives (picture, tappable areas, events) for the week `which`
    weeks from this one. A picture is good for one `key` (the minute and the data it was drawn
    from) and until an event in it starts or ends. At most four are kept: the week on show,
    the one before, the one after and the current week. They are never drawn on, only copied."""

    def __init__(self, draw):
        self.draw, self.key, self.kept = draw, None, {}

    def fresh(self, which: int, key, now: datetime) -> bool:
        entry = self.kept.get(which) if key == self.key else None
        return entry is not None and (entry[3] is None or now < entry[3])

    def get(self, which: int, key, now: datetime, shown: int, pause=None) -> tuple:
        """The week `which`, drawn now if need be. `shown` is the week on the screen."""
        if key != self.key:
            self.kept.clear()
            self.key = key
        if not self.fresh(which, key, now):
            for other in [o for o in self.kept if o not in (0, which) and abs(o - shown) > 1]:
                del self.kept[other]
            img, hits, events = self.draw(which, now, pause)
            self.kept[which] = (img, hits, events, next_turn(events, now))
        return self.kept[which][:3]


def compose(cfg: dict, tz: ZoneInfo, data: list, fetched_at: float | None, offset: int, now: datetime | None = None,
            hits: list | None = None, forecast=None, pause=None, alerts: list | None = None) -> tuple[Image.Image, list]:
    """(picture of the week `offset` weeks from this one, the events it was drawn from).
    `pause` goes to render_week(); `alerts` are the active weather alerts."""
    now = now or datetime.now(tz)
    alerts = alerts or []
    start = week_start(now.date(), cfg.get("first_weekday", 6)) + timedelta(weeks=offset)
    if fetched_at is None:
        return render_week(start, [], now, "Starting", hits=hits, weather=forecast, alerts=alerts), []
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
    return render_week(start, events, now, note, stale, hits, forecast, message, pause, alerts), events


def main() -> None:
    ap = argparse.ArgumentParser(prog="skycal.frame")
    ap.add_argument("--config", type=Path, default=Path("config.toml"))
    ap.add_argument("--cache-dir", type=Path, help="overrides cache_dir from the config")
    ap.add_argument("--fb", type=Path, default=Path("/dev/fb0"))
    args = ap.parse_args()

    cfg = tomllib.loads(args.config.read_text())
    tz = ZoneInfo(cfg["timezone"])
    cache = args.cache_dir or Path(cfg.get("cache_dir", "cache"))
    remote.drop_code(cache)          # one left behind by a calendar that stopped with the card open
    configured = str(cfg.get("theme", themes.DEFAULT))
    print(f"theme: {configure(themes.chosen(cache, configured), feed_colors(cfg))}", file=sys.stderr)
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
    warnings_now: list = []      # weather alerts as last fetched; al.active() picks those still running

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
        threading.Thread(target=alerts_loop, args=(place, cache, tz, results, wake_w), daemon=True).start()

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
    # view: "week", "detail" (one event), "day" (one day's list), "alerts", "weather", "settings" or "saver"
    view, chosen, chosen_day, day_first, back_to = "week", None, None, 0, "week"
    settings_url, settings_note = None, ""     # what the settings card shows while view == "settings"
    alert_index = 0
    offset, last_touch, drawn_minute, dirty = 0, time.monotonic(), None, True
    hits: list = []
    stamp, ahead_failed = 0, None      # arrivals of new data so far; the `stamp` at which drawing ahead failed

    def draw_week(which: int, now: datetime, pause) -> tuple:
        found: list = []
        img, events = compose(cfg, tz, data, fetched_at, which, now, found, forecast, pause, al.active(warnings_now, now))
        return img, found, events

    def touched() -> None:
        if touch and select.select([touch[0].fd], [], [], 0)[0]:
            raise Touched

    weeks = Weeks(draw_week)

    try:
        while True:
            mono = time.monotonic()
            idle = mono - last_touch
            while not results.empty():
                kind, value = results.get()
                if kind == "feeds":
                    data, fetched_at = value
                elif kind == "alerts":
                    warnings_now = value
                else:
                    forecast = value
                stamp += 1
                dirty = True
            if asked["saver"]:
                asked["saver"] = False
                if view != "saver":
                    view, offset, dirty = "saver", 0, True
                    backlight.dim()
            if view in ("detail", "day", "weather", "alerts") and idle > CARD_BACK_S:
                view, dirty = "week", True
            if view == "settings" and idle > min(SETTINGS_BACK_S, remote.CODE_S):
                view, dirty = "week", True
            if offset and idle > IDLE_BACK_S:
                offset, dirty = 0, True
            if saver_s and touch and view != "saver" and idle > saver_s:    # never without a way back
                view, offset, dirty = "saver", 0, True
                backlight.dim()
            if view != "settings" and settings_url:    # the card has closed: its code stops working
                remote.drop_code(cache)
                settings_url = None
            now = datetime.now(tz)
            minute = int(now.timestamp() // 60)   # counted from 1970, so the hour that comes twice in autumn is two
            if minute != drawn_minute:           # the clock, "now" marks and today's highlight
                dirty = True
            if dirty:
                try:
                    hits = []
                    running = al.active(warnings_now, now)
                    if view == "alerts" and not running:     # the last one ended while it was open
                        view = "week"
                    if view == "saver":
                        events = gather(data, tz, now.date(), now.date() + timedelta(days=1))[0] if fetched_at else []
                        img = render_saver(now, events, forecast, running)
                    elif view == "weather":
                        message = "" if place else "Type your town on the settings page to see the weather here."
                        img = render_weather(forecast, now, message or "The forecast has not arrived yet.", running)
                    else:
                        img, week_hits, events = weeks.get(offset, (minute, stamp), now, offset)
                        if view == "detail" and chosen is not None:
                            if back_to == "day" and chosen_day is not None:
                                img = render_day(img, chosen_day, events, now, first=day_first)
                            img = render_detail(img, chosen, now)
                        elif view == "day" and chosen_day is not None:
                            img = render_day(img, chosen_day, events, now, hits, day_first)
                        elif view == "alerts":
                            alert_index = alert_index if alert_index < len(running) else 0
                            img = render_alerts(img, running, now, hits, alert_index)
                        elif view == "settings":
                            img = render_settings(img, settings_url, now, settings_note, hits)
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
            # While the week is on show and no finger is on the glass, draw the weeks a swipe or
            # the Today button would ask for next, so that those only have to show a picture.
            # A finger that arrives meanwhile ends the drawing (Touched) and goes first.
            ahead = None
            if view == "week" and touch and fetched_at is not None and ahead_failed != stamp and not finger_down(pos):
                first = week_start(now.date(), cfg.get("first_weekday", 6))
                ahead = next((o for o in (offset + 1, offset - 1, 0) if not weeks.fresh(o, (minute, stamp), now) and read_ahead(
                    data, first + timedelta(weeks=o), first + timedelta(weeks=o, days=6))), None)
            ready = select.select(fds, [], [], wait if ahead is None else AHEAD_PAUSE_S)[0]
            if ahead is not None and not ready:
                try:
                    weeks.get(ahead, (minute, stamp), now, offset, touched)
                except Touched:
                    pass
                except Exception as err:
                    print(f"week not drawn ahead: {type(err).__name__}", file=sys.stderr)
                    ahead_failed = stamp
                continue
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
            before = (view, offset, chosen, chosen_day, day_first, alert_index, theme().name)
            for down, up in pairs:
                last_touch = time.monotonic()
                x, y = to_picture(*down, xmax, ymax, flip_x, flip_y)
                move = gesture((x, y), to_picture(*up, xmax, ymax, flip_x, flip_y))
                if view == "saver":                       # a touch only wakes the screen; the rest of
                    view = "week"                         # this batch was aimed at the dark screen too
                    backlight.restore()
                    break
                elif view == "detail":                    # any touch closes the card
                    view = back_to
                elif view == "weather":                   # any touch closes it
                    view = "week"
                elif view == "settings":                  # a tap on a look switches to it, any other closes
                    target = target_at(hits, x, y) if move == "tap" else None
                    if isinstance(target, tuple) and target[0] == "theme":
                        if target[1] != theme().name:
                            name = configure(target[1], feed_colors(cfg))
                            stamp += 1                    # the weeks drawn so far are in the old look
                            try:
                                themes.choose(cache, name, configured)
                            except OSError as err:
                                print(f"look not saved: {type(err).__name__}", file=sys.stderr)
                    else:
                        view = "week"
                elif view == "alerts":                    # a tap on "Next alert" pages, any other closes
                    target = target_at(hits, x, y) if move == "tap" else None
                    if isinstance(target, int):
                        alert_index = target
                    else:
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
                    elif name == "settings":
                        view, settings_url, settings_note = "settings", None, ""
                        address = remote.wifi_address()
                        if address:
                            try:
                                settings_url = remote.settings_url(address, remote.new_code(cache))
                            except OSError as err:
                                print(f"settings code not saved: {type(err).__name__}", file=sys.stderr)
                                settings_note = ("The code could not be made just now. Try again, or connect a "
                                                 "computer with the USB cable and open the drive named FRAME.")
                        elif not settings_url:
                            settings_note = ("The frame is not on Wi-Fi. Connect a computer with the USB cable, "
                                             "open the drive named FRAME and follow the link to the settings page.")
                    elif name:
                        offset = {"prev": offset - 1, "next": offset + 1, "today": 0}[name]
                    elif target == "alerts":
                        view, alert_index = "alerts", 0
                    elif isinstance(target, Event):
                        view, chosen, back_to = "detail", target, "week"
                    elif isinstance(target, date):
                        view, chosen_day, day_first = "day", target, 0
            if (view, offset, chosen, chosen_day, day_first, alert_index, theme().name) != before:    # a touch that changed nothing
                dirty = True                                               # needs no new picture
    finally:
        backlight.restore()
        remote.drop_code(cache)      # a code must not outlive the card that showed it


if __name__ == "__main__":
    main()
