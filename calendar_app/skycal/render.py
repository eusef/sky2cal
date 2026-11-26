"""Draw the calendar's screens as 1280x800 landscape images: the week, one event's details,
one day's list, the weather alerts, the hourly weather and the screensaver. Pure functions of
their inputs; no I/O except fonts.

How the screens look is up to the theme chosen with configure() (see skycal/themes); this
module holds what every theme shares: the touch areas of the header buttons, fonts, the kept
letters, text fitting, colours of events and the weather icons.

The functions that take `hits` append the tappable areas they draw to it, as
(x0, y0, x1, y1, target): target is an Event, a date for "show this whole day", the string
"alerts" for "show the weather alerts", or an int for "show page or alert number n"."""
from __future__ import annotations

import math
import sys
from collections import OrderedDict
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .feed import Event
from .weather import Weather

W, H = 1280, 800
HEADER_H = 76
DAYHEAD_H = 84
PAD = 6

BG = (250, 248, 244)
INK = (28, 28, 30)
MUTED = (120, 120, 128)
FAINT = (176, 174, 172)
RULE = (224, 220, 212)
TODAY_BG = (255, 255, 255)
ACCENT = (0, 110, 220)
WARN = (190, 70, 30)
CAL_COLORS = [(0, 110, 220), (30, 150, 90), (200, 90, 40), (140, 80, 190), (200, 50, 90), (90, 100, 120)]
NIGHT_BG = (6, 6, 8)
NIGHT_INK = (205, 205, 210)
NIGHT_MUTED = (130, 130, 140)

# Hit boxes of the week view's header buttons, shared with the touch handler: name -> (x0, y0, x1, y1).
# "settings" is the gear that shows the QR code for the settings page (see remote.py).
# Order matters where the tap margins of two buttons overlap: the earlier one wins.
NAV = {"weather": (836, 12, 998, 64), "settings": (772, 12, 824, 64), "prev": (1010, 12, 1074, 64), "today": (1082, 12, 1198, 64),
       "next": (1206, 12, 1270, 64)}
CLOSE = (1150, 12, 1270, 64)     # "Back" button of the weather screen

_FONT_DIRS = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]
_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}
FONTS = Path(__file__).resolve().parent / "fonts"
_files: dict[tuple[str, int, int], ImageFont.FreeTypeFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    if (size, bold) not in _cache:
        for regular, heavy in _FONT_DIRS:
            if Path(regular).exists():
                _cache[(size, bold)] = ImageFont.truetype(heavy if bold else regular, size)
                break
        else:
            raise RuntimeError("no font found; install fonts-dejavu-core")
    return _cache[(size, bold)]


def font_file(name: str, size: int, weight: int = 0) -> ImageFont.FreeTypeFont:
    """A font shipped in skycal/fonts, by file name. `weight` picks the weight of a variable
    font (the files named *-VF.ttf); 0 keeps its default."""
    key = (name, size, weight)
    if key not in _files:
        f = ImageFont.truetype(str(FONTS / name), size)
        if weight:
            try:
                f.set_variation_by_axes([weight])
            except (OSError, AttributeError):        # a FreeType without variable fonts: default weight
                pass
        _files[key] = f
    return _files[key]


def week_start(day: date, first_weekday: int = 6) -> date:
    """First day of the week containing `day`. first_weekday: Monday=0 .. Sunday=6."""
    return day - timedelta(days=(day.weekday() - first_weekday) % 7)


def fmt_time(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    suffix = "a" if dt.hour < 12 else "p"
    return f"{hour}{suffix}" if dt.minute == 0 else f"{hour}:{dt.minute:02d}{suffix}"


def fmt_when(ev: Event) -> str:
    """When an event happens, in words: day, and times unless it lasts all day."""
    def day(d: date) -> str:
        return f"{d:%A}, {d:%B} {d.day}"

    if ev.all_day:
        last = (ev.end - timedelta(days=1)).date()
        if last <= ev.start.date():
            return f"{day(ev.start.date())}, all day"
        return f"{day(ev.start.date())} to {day(last)}"
    if ev.end <= ev.start:
        return f"{day(ev.start.date())}, {fmt_time(ev.start)}"
    if ev.end.date() == ev.start.date() or ev.end == datetime.combine(ev.start.date() + timedelta(days=1), time.min, ev.start.tzinfo):
        return f"{day(ev.start.date())}, {fmt_time(ev.start)} to {fmt_time(ev.end)}"
    return f"{day(ev.start.date())} {fmt_time(ev.start)} to {day(ev.end.date())} {fmt_time(ev.end)}"


def fmt_until(ev: Event, now: datetime) -> str:
    """Where `now` is against a timed event: "In 2 h 15 min", "Now", "Ended"; "" for all-day events."""
    if ev.all_day:
        return ""
    if now >= ev.end and now > ev.start:
        return "Ended"
    if now >= ev.start:
        return "Now"
    minutes = int((ev.start - now).total_seconds() // 60) + 1
    if minutes < 60:
        return f"In {minutes} min"
    if minutes < 24 * 60:
        return f"In {minutes // 60} h {minutes % 60} min" if minutes % 60 else f"In {minutes // 60} h"
    days = (ev.start.date() - now.date()).days
    return "Tomorrow" if days == 1 else f"In {days} days"


# Drawing letters is where the time of a screen goes on the frame (FreeType shapes every
# letter anew each time), and most words come back minute after minute and week after week.
# So the drawn letters of a line are kept (_text), and so is how a text was cut or wrapped
# (fit, wrap). All three give exactly what Pillow gives without them.
_masks: OrderedDict = OrderedDict()   # (text, font, anchor, share of a pixel) -> (the letters as a mask, its offset)
_masks_px = 0                         # pixels held in _masks
_MASKS_PX = 4_000_000                 # most pixels kept (a byte each); the longest unused go first
_MASK_PX = 60_000                     # a line bigger than this (the screensaver's clock) is not kept
_plain_text = False                   # set if this Pillow lacks what _text() uses: d.text() from then on
_cuts: dict = {}                      # arguments of fit() and wrap() -> their result
_CUTS = 3000


def _text(d: ImageDraw.ImageDraw, xy: tuple, text: str, font, fill: tuple, anchor: str | None = None) -> None:
    """d.text() for one line, with the same result, drawn from the kept letters when there are some."""
    global _masks_px, _plain_text
    if _plain_text or not text or "\n" in text:
        d.text(xy, text, font=font, fill=fill, anchor=anchor)
        return
    try:      # the steps of ImageDraw.text(), with the mask kept in between
        ink = d._getink(fill)[0]
        start = (math.modf(xy[0])[0], math.modf(xy[1])[0])
        key = (text, font, anchor, start, d.fontmode)
        kept = _masks.get(key)
        if kept is not None:
            _masks.move_to_end(key)
        else:
            kept = font.getmask2(text, d.fontmode, anchor=anchor, ink=ink, start=start)
            px = kept[0].size[0] * kept[0].size[1]
            if px <= _MASK_PX:
                _masks[key] = kept
                _masks_px += px
                while _masks_px > _MASKS_PX:
                    old = _masks.popitem(last=False)[1][0]
                    _masks_px -= old.size[0] * old.size[1]
        mask, offset = kept
        d.draw.draw_bitmap([int(xy[0]) + offset[0], int(xy[1]) + offset[1]], mask, ink)
    except (AttributeError, TypeError, ValueError):      # raised before anything is drawn
        _plain_text = True
        d.text(xy, text, font=font, fill=fill, anchor=anchor)


def _kept_cut(key: tuple, work):
    """`work()`, or what it gave the last time for the same `key`."""
    if key not in _cuts:
        if len(_cuts) >= _CUTS:
            _cuts.clear()
        _cuts[key] = work()
    return _cuts[key]


def fit(draw: ImageDraw.ImageDraw, text: str, fnt, width: int) -> str:
    """`text` on one line, cut with an ellipsis if it is wider than `width`."""
    return _kept_cut((text, fnt, width, draw.fontmode), lambda: _fit(draw, text, fnt, width))


def _fit(draw: ImageDraw.ImageDraw, text: str, fnt, width: int) -> str:
    text = " ".join(text[:2000].split())
    if draw.textlength(text, font=fnt) <= width:
        return text
    lo, hi = 0, len(text)                  # the longest start that fits with the ellipsis, by halving
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid] + "…", font=fnt) <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, width: int, max_lines: int) -> list[str]:
    """Word-wrap `text`; line breaks in it are kept. The last line allowed ends in an ellipsis."""
    return list(_kept_cut((text, fnt, width, max_lines, draw.fontmode), lambda: _wrap(draw, text, fnt, width, max_lines)))


def _wrap(draw: ImageDraw.ImageDraw, text: str, fnt, width: int, max_lines: int) -> list[str]:
    lines = []
    for para in text.splitlines() or [""]:
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if draw.textlength(trial, font=fnt) <= width:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            while draw.textlength(word, font=fnt) > width and len(word) > 1:  # hard-break a long word
                lo, hi = 1, len(word) - 1          # the longest start of the word that fits, by halving
                while lo < hi:
                    mid = (lo + hi + 1) // 2
                    if draw.textlength(word[:mid], font=fnt) <= width:
                        lo = mid
                    else:
                        hi = mid - 1
                lines.append(word[:lo])
                word = word[lo:]
                if len(lines) > max_lines:
                    break
            cur = word
        if cur:
            lines.append(cur)
        if len(lines) > max_lines:
            break
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=fnt) > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines


def _mix(color: tuple, toward: tuple, amount: float) -> tuple:
    return tuple(int(c + (t - c) * amount) for c, t in zip(color, toward))


def _temp(value: float) -> str:
    return f"{round(value)}°"


# ---- weather pictures ----

ICON_COLORS = ((245, 170, 30), (128, 138, 180), (150, 156, 168), (40, 120, 220), (110, 165, 225), (240, 175, 20))


def weather_icon(d: ImageDraw.ImageDraw, cx: int, cy: int, size: int, name: str, is_day: bool = True,
                 bg: tuple = BG, fade: bool = False, colors: tuple = ICON_COLORS, faint: tuple = FAINT) -> None:
    """One of weather.describe()'s icon names, drawn from shapes so it looks the same with any font.
    `colors` are sun, moon, cloud, rain, snow and lightning; a faded icon is drawn in `faint`."""
    s = size
    sun, moon, cloud, rain, snow, bolt = colors
    if fade:
        sun = moon = cloud = rain = snow = bolt = _mix(faint, bg, 0.25)

    def light(x: float, y: float, k: float) -> None:
        if is_day:
            r = 0.22 * s * k
            d.ellipse((x - r, y - r, x + r, y + r), fill=sun)
            for i in range(8):
                a = i * math.pi / 4
                d.line((x + math.cos(a) * r * 1.45, y + math.sin(a) * r * 1.45,
                        x + math.cos(a) * r * 2.0, y + math.sin(a) * r * 2.0), fill=sun, width=max(2, s // 14))
        else:
            r = 0.3 * s * k
            d.ellipse((x - r, y - r, x + r, y + r), fill=moon)
            d.ellipse((x - r * 0.45, y - r * 1.25, x + r * 1.45, y + r * 0.65), fill=bg)

    def puff(y: float) -> None:
        d.rounded_rectangle((cx - 0.4 * s, y, cx + 0.4 * s, y + 0.26 * s), radius=0.13 * s, fill=cloud)
        d.ellipse((cx - 0.3 * s, y - 0.18 * s, cx + 0.08 * s, y + 0.2 * s), fill=cloud)
        d.ellipse((cx - 0.02 * s, y - 0.09 * s, cx + 0.28 * s, y + 0.2 * s), fill=cloud)

    if name == "clear":
        light(cx, cy, 1.0)
    elif name == "partly":
        light(cx + 0.18 * s, cy - 0.16 * s, 0.75)
        puff(cy - 0.02 * s)
    elif name == "fog":
        for i in range(4):
            y = cy - 0.27 * s + i * 0.18 * s
            inset = 0.1 * s if i % 2 else 0
            d.line((cx - 0.38 * s + inset, y, cx + 0.38 * s - inset, y), fill=cloud, width=max(2, s // 12))
    else:
        puff(cy - 0.2 * s)
        low = cy + 0.14 * s
        if name == "rain":
            for i in (-1, 0, 1):
                x = cx + i * 0.22 * s
                d.line((x + 0.05 * s, low, x - 0.05 * s, low + 0.24 * s), fill=rain, width=max(2, s // 12))
        elif name == "snow":
            r = max(2, s // 14)
            for i, dy in ((-1, 0.05), (0, 0.19), (1, 0.05)):
                x, y = cx + i * 0.22 * s, low + dy * s
                d.ellipse((x - r, y - r, x + r, y + r), fill=snow)
        elif name == "storm":
            d.polygon([(cx + 0.06 * s, low - 0.04 * s), (cx - 0.14 * s, low + 0.16 * s), (cx - 0.01 * s, low + 0.16 * s),
                       (cx - 0.08 * s, low + 0.36 * s), (cx + 0.16 * s, low + 0.1 * s), (cx + 0.03 * s, low + 0.1 * s)],
                      fill=bolt)


@lru_cache(maxsize=16)
def _gear_mask(size: int) -> Image.Image:
    big = size * 4
    m = Image.new("L", (big, big), 0)
    g = ImageDraw.Draw(m)
    c, outer, body, hole = big / 2, big * 0.5, big * 0.36, big * 0.15
    g.ellipse((c - body, c - body, c + body, c + body), fill=255)
    for k in range(8):                       # eight teeth, each a short bar out from the body
        a = k * math.pi / 4
        dx, dy, px, py = math.cos(a), math.sin(a), -math.sin(a) * big * 0.085, math.cos(a) * big * 0.085
        g.polygon([(c + dx * body * 0.8 + px, c + dy * body * 0.8 + py), (c + dx * outer + px, c + dy * outer + py),
                   (c + dx * outer - px, c + dy * outer - py), (c + dx * body * 0.8 - px, c + dy * body * 0.8 - py)], fill=255)
    g.ellipse((c - hole, c - hole, c + hole, c + hole), fill=0)
    return m.resize((size, size), Image.LANCZOS)


def gear_icon(d: ImageDraw.ImageDraw, cx: float, cy: float, size: int, fill: tuple) -> None:
    """A settings gear `size` px across, from shapes, smoothed; its hole shows what is behind it."""
    d.bitmap((round(cx - size / 2), round(cy - size / 2)), _gear_mask(size), fill=fill)


@lru_cache(maxsize=4)
def qr_image(text: str, size: int) -> Image.Image | None:
    """`text` as a QR code: black on white with the usual quiet zone, at most `size` px square,
    in whole pixels per module so a phone reads it easily. None when the QR library is missing."""
    try:
        import segno
    except ImportError:
        return None
    rows = segno.make(text, error="m", micro=False).matrix
    n = len(rows) + 8                         # four modules of white all round
    img = Image.new("L", (n, n), 255)
    for y, row in enumerate(rows):
        for x, dark in enumerate(row):
            if dark:
                img.putpixel((x + 4, y + 4), 0)
    scale = max(1, size // n)
    return img.resize((n * scale, n * scale), Image.NEAREST).convert("RGB")


def warn_icon(d: ImageDraw.ImageDraw, cx: float, cy: float, size: float, fill: tuple, ink: tuple) -> None:
    """A warning triangle with an exclamation mark, from shapes (not every font has the sign)."""
    s = size
    d.polygon([(cx, cy - 0.5 * s), (cx + 0.56 * s, cy + 0.44 * s), (cx - 0.56 * s, cy + 0.44 * s)], fill=fill)
    w = max(2, round(s * 0.11))
    d.rounded_rectangle((cx - w / 2, cy - 0.2 * s, cx + w / 2, cy + 0.17 * s), radius=w / 2, fill=ink)
    d.ellipse((cx - w / 2 - 0.5, cy + 0.24 * s, cx + w / 2 + 0.5, cy + 0.24 * s + w + 1), fill=ink)


def dim_table(toward: tuple, amount: float) -> list[int]:
    """Lookup table for Image.point() that gives what Image.blend() with a plain `toward` picture
    gives (it is made by blending every level once), in a quarter of the time on the frame."""
    ramp = Image.frombytes("L", (256, 1), bytes(range(256)))
    return [v for c in toward for v in Image.blend(ramp, Image.new("L", (256, 1), c), amount).tobytes()]


# ---- colours of events ----

@lru_cache(maxsize=256)
def parse_color(text: str) -> tuple | None:
    """(r, g, b) of a CSS colour name, #rgb, #rrggbb or #rrggbbaa (alpha dropped); None if not a colour."""
    text = (text or "").strip()
    if not text or len(text) > 40:
        return None
    try:
        rgb = tuple(ImageColor.getrgb(text)[:3])
    except (ValueError, OverflowError):
        return None
    return rgb if all(isinstance(c, int) and 0 <= c <= 255 for c in rgb) else None


_feed_colors: dict[int, tuple] = {}      # feed number -> colour chosen for that calendar in the config


def event_color(ev: Event, palette: list) -> tuple:
    """An event's colour: its own (COLOR in the feed), else the one chosen for its calendar on
    the settings page, else the one its feed gives the calendar, else the theme's colour for
    that feed number."""
    return (parse_color(getattr(ev, "color", "")) or _feed_colors.get(ev.cal)
            or parse_color(getattr(ev, "cal_color", "")) or palette[ev.cal % len(palette)])


# ---- themes ----

_theme = None


def configure(theme: str = "original", feed_colors: dict | None = None) -> str:
    """Pick the theme by name (an unknown name gives the original) and the colours chosen per
    calendar, as {feed number: colour text}. Returns the name of the theme in use."""
    global _theme
    from . import themes
    _theme = themes.make(theme)
    _feed_colors.clear()
    for i, text in (feed_colors or {}).items():
        rgb = parse_color(str(text))
        if rgb is not None:
            _feed_colors[int(i)] = rgb
    return _theme.name


def theme():
    """The theme in use (the original until configure() picks another)."""
    if _theme is None:
        configure()
    return _theme


def _draw(method: str, args: tuple, hits: list | None = None, paused: list | None = None) -> Image.Image:
    """theme().<method>(*args). When a theme other than the original fails while drawing, the
    original takes over for good (a theme must never keep the calendar from showing) and draws
    the screen again; the tappable areas the failed theme had added are dropped first. What
    `pause` raised (listed in `paused`) is passed on unchanged: it is not a failure."""
    global _theme
    drawer = theme()
    kept = len(hits) if hits is not None else 0
    try:
        return getattr(drawer, method)(*args)
    except Exception as err:
        if paused or drawer.name == "original":
            raise
        print(f"theme {drawer.name!r} failed in {method}: {type(err).__name__}: {err}; using the original",
              file=sys.stderr)
        if hits is not None:
            del hits[kept:]
        from .themes.original import Original
        _theme = Original()
        return getattr(_theme, method)(*args)


DAY_ROWS = 13     # rows of the day list; a longer day is shown in pages


def render_week(start: date, events: list[Event], now: datetime, note: str | None = None, stale: bool = False,
                hits: list | None = None, weather: Weather | None = None, message: str = "", pause=None,
                alerts: list | tuple = ()) -> Image.Image:
    """`start` is the first day shown; `now` (tz-aware) sets the clock and today's highlight.
    `note` is the status text under the clock; `stale` draws it as a warning. The weather
    button shows the current conditions when `weather` has them. `message` is written across
    the middle of the week (for a frame that has no calendars yet). `pause` is called after
    each day; what it raises ends the drawing (for a picture nobody is waiting for yet).
    `alerts` (active weather alerts) are shown in a band that leads to render_alerts()."""
    paused: list = []

    def watched() -> None:
        try:
            pause()
        except BaseException as err:
            paused.append(err)
            raise

    return _draw("render_week", (start, events, now, note or "", stale, hits, weather, message, watched if pause else None,
                                 list(alerts)), hits, paused)


def render_detail(base: Image.Image, ev: Event, now: datetime) -> Image.Image:
    """One event's details on a card over `base`. A tap anywhere closes it."""
    return _draw("render_detail", (base, ev, now))


def render_day(base: Image.Image, day: date, events: list[Event], now: datetime, hits: list | None = None,
               first: int = 0) -> Image.Image:
    """Every event of one day as a list on a card over `base` (the week view cuts long days short).
    A day with more than DAY_ROWS events is shown in pages, from event number `first`; its last
    row then leads to the next page (hit target: the number of that page's first event, an int)."""
    return _draw("render_day", (base, day, events, now, hits, first), hits)


def render_alerts(base: Image.Image, alerts: list, now: datetime, hits: list | None = None, index: int = 0) -> Image.Image:
    """Weather alert number `index` in full on a card over `base`. With more than one, a row
    leads to the next (hit target: its number, an int); a tap anywhere else closes the card."""
    index = index if 0 <= index < len(alerts) else 0
    return _draw("render_alerts", (base, list(alerts), now, hits, index), hits)


def render_weather(weather: Weather | None, now: datetime, message: str = "", alerts: list | tuple = ()) -> Image.Image:
    """Hour by hour for today and tomorrow. `message` is shown instead when there is no forecast."""
    return _draw("render_weather", (weather, now, message, list(alerts)))


def render_settings(base: Image.Image, url: str | None, now: datetime, message: str = "",
                    hits: list | None = None) -> Image.Image:
    """A card over the week with the QR code of the settings page (`url`), or `message` when
    there is no address to show (the frame is not on Wi-Fi), and a button for each style
    (hit target: ("theme", name)); the style in use is marked."""
    return _draw("render_settings", (base, url, now, message, hits), hits)


def render_saver(now: datetime, events: list[Event], weather: Weather | None, alerts: list | tuple = ()) -> Image.Image:
    """Dark screen with the time, the date, the weather and what comes next. The block moves a
    little every minute so that nothing stays lit in one place."""
    return _draw("render_saver", (now, events, weather, list(alerts)))
